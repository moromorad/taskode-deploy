# pyrefly: ignore [missing-import]
from typing import Optional

from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import generics, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from ..models import Project, Task, Weather
from ..serializers import ProjectSerializer, TaskSerializer, UserSerializer
from ..services import utils
from ..services.github_parser import sync_project_ast
from ..services.rag_service import retrieve_relevant_code
from ..tasks import index_project_codebase


@extend_schema_view(
    list=extend_schema(
        tags=["Tasks"],
        summary="List all tasks",
        description="Returns all tasks owned by the authenticated user.",
    ),
    create=extend_schema(
        tags=["Tasks"],
        summary="Create a task",
        description="Creates a new task assigned to the authenticated user.",
    ),
    retrieve=extend_schema(
        tags=["Tasks"],
        summary="Retrieve a task",
        description="Returns details of a specific task by ID.",
    ),
    update=extend_schema(
        tags=["Tasks"],
        summary="Update a task",
        description="Replaces all fields of a specific task.",
    ),
    partial_update=extend_schema(
        tags=["Tasks"],
        summary="Partially update a task",
        description="Updates specific fields of a task.",
    ),
    destroy=extend_schema(
        tags=["Tasks"],
        summary="Delete a task",
        description="Deletes a specific task.",
    ),
)
class TaskViewSet(viewsets.ModelViewSet):
    # Tell Django which translator to use
    queryset = Task.objects.all()
    serializer_class = TaskSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Task.objects.none()
        return Task.objects.filter(owner=self.request.user)

    def perform_create(self, serializer: BaseSerializer) -> None:
        serializer.save(owner=self.request.user)

    @extend_schema(
        tags=["Tasks"],
        summary="Generate a task from natural language text (AI)",
        description="Parses natural language prompt with Gemini AI into a structured Task, optionally taking a project outline into account.",
        request=inline_serializer(
            name="TaskGenRequest",
            fields={
                "text": serializers.CharField(help_text="Natural language description of the task"),
                "timezone": serializers.CharField(
                    required=False,
                    default="UTC",
                    help_text="User's local timezone (e.g. UTC, America/New_York)",
                ),
                "project_id": serializers.IntegerField(
                    required=False,
                    allow_null=True,
                    help_text="Optional project ID to associate task with",
                ),
            },
        ),
        responses={
            201: inline_serializer(
                name="TaskGenResponse",
                fields={"message": serializers.CharField()},
            ),
            400: OpenApiResponse(description="No text provided"),
            404: OpenApiResponse(description="Project not found or access denied"),
        },
    )
    @action(detail=False, methods=["post"])
    def gen(self, request):  # pragma: no cover
        text: str = request.data.get("text")
        user_timezone: str = request.data.get("timezone", "UTC")
        project_id = request.data.get("project_id")
        if not text:
            return Response({"message": "No text provided"}, status=status.HTTP_400_BAD_REQUEST)

        project = None
        ast_outline = None
        code_snippets = None

        if project_id:
            try:
                project = Project.objects.get(id=project_id, owner=request.user)
                ast_outline = project.ast_outline
                if project.is_indexed:
                    print(f"\n[RAG Generation] ⚡ Project '{project.name}' is indexed ({project.embedding_model}). Retrieving code...")
                    try:
                        code_snippets = retrieve_relevant_code(
                            project.id,
                            text,
                            model=project.embedding_model or "gemini-embedding-2",
                            top_k=4,
                        )
                        snippet_count = code_snippets.count("--- Code Snippet") if code_snippets else 0
                        print(f"[RAG Generation] 📥 Injected {snippet_count} code snippet(s) into Gemini prompt context.")
                    except Exception as e:
                        print(f"[RAG Generation] ⚠️ Code retrieval failed: {e}")
                else:
                    print(f"\n[RAG Generation] ℹ️ Project '{project.name}' is not indexed yet. Using high-level AST outline fallback.")
            except Project.DoesNotExist:
                return Response({"error": "Project not found or access denied"}, status=status.HTTP_404_NOT_FOUND)

        try:
            task = utils.text_to_tasks(text, user_timezone, ast_outline, code_snippets)
        except Exception:
            task = utils.text_to_tasks(text, "UTC", ast_outline, code_snippets)

        task_data = task.model_dump()
        Task.objects.create(owner=request.user, project=project, **task_data)

        return Response({"message": "Task created successfully"}, status=status.HTTP_201_CREATED)


@extend_schema_view(
    list=extend_schema(
        tags=["Projects"],
        summary="List all projects",
        description="Returns all projects owned by the authenticated user.",
    ),
    create=extend_schema(
        tags=["Projects"],
        summary="Create a project",
        description="Creates a new project and triggers an initial AST sync if a GitHub repository is linked.",
    ),
    retrieve=extend_schema(
        tags=["Projects"],
        summary="Retrieve a project",
        description="Returns details of a specific project by ID.",
    ),
    update=extend_schema(
        tags=["Projects"],
        summary="Update a project",
        description="Replaces all fields of a specific project.",
    ),
    partial_update=extend_schema(
        tags=["Projects"],
        summary="Partially update a project",
        description="Updates specific fields of a project.",
    ),
    destroy=extend_schema(
        tags=["Projects"],
        summary="Delete a project",
        description="Deletes a specific project.",
    ),
)
class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Project.objects.none()
        return Project.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        project = serializer.save(owner=self.request.user)
        if project.github_repo:
            try:
                sync_project_ast(project, project.github_token)
            except Exception as e:
                print(f"Auto-sync failed for {project.name}: {e}")
            try:
                index_project_codebase.delay(project.id, project.github_token)
            except Exception as e:
                print(f"Celery indexing dispatch failed for {project.name}: {e}")

    # endpoint: POST /api/projects/<id>/sync_repo/
    @extend_schema(
        tags=["Projects"],
        summary="Sync repository AST structure from GitHub",
        description="Clones or downloads repository code from GitHub, parses Python/JS/TS AST, and saves the structural outline to the project.",
        request=inline_serializer(
            name="ProjectSyncRepoRequest",
            fields={
                "github_token": serializers.CharField(
                    required=False,
                    allow_blank=True,
                    help_text="GitHub Personal Access Token (PAT) for private repositories (optional if configured on project)",
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="ProjectSyncRepoResponse",
                fields={
                    "status": serializers.CharField(),
                    "message": serializers.CharField(),
                    "ast_preview": serializers.CharField(),
                },
            ),
            400: OpenApiResponse(description="Sync failed or repository is empty"),
            500: OpenApiResponse(description="Internal server error during sync"),
        },
    )
    @action(detail=True, methods=["post"])
    def sync_repo(self, request, pk=None):
        project = self.get_object()

        github_token = request.data.get("github_token") or project.github_token or None

        try:
            result_message = sync_project_ast(project, github_token)

            if "Failed" in result_message or "empty" in result_message:
                return Response({"error": result_message}, status=status.HTTP_400_BAD_REQUEST)

            # Mark is_indexed=False while background Celery vector indexing runs
            project.is_indexed = False
            project.save(update_fields=["is_indexed"])

            # Trigger background Celery indexing into ChromaDB
            try:
                index_project_codebase.delay(project.id, github_token)
            except Exception as e:
                print(f"Celery indexing dispatch failed: {e}")

            return Response({
                "status": "success",
                "message": result_message,
                "ast_preview": project.ast_outline[:500] if project.ast_outline else "",
            })
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # endpoint: GET /api/projects/<id>/index_status/
    @extend_schema(
        tags=["Projects"],
        summary="Get live RAG indexing status and console logs",
        description="Returns real-time indexing progress, current step, and live logs from Redis cache.",
        responses={
            200: inline_serializer(
                name="ProjectIndexStatusResponse",
                fields={
                    "status": serializers.CharField(),
                    "progress": serializers.IntegerField(),
                    "stage": serializers.CharField(),
                    "current_step": serializers.CharField(),
                    "logs": serializers.ListField(child=serializers.CharField()),
                    "is_indexed": serializers.BooleanField(),
                    "embedding_model": serializers.CharField(allow_null=True),
                },
            ),
        },
    )
    @action(detail=True, methods=["get"])
    def index_status(self, request, pk=None):
        project = self.get_object()
        cache_key = f"project_rag_status:{project.id}"
        status_data = None
        try:
            status_data = cache.get(cache_key)
        except Exception:
            pass

        if not status_data:
            from ..tasks import _IN_MEMORY_RAG_STATUS
            status_data = _IN_MEMORY_RAG_STATUS.get(project.id)

        if not status_data:
            status_data = {
                "status": "completed" if project.is_indexed else "idle",
                "progress": 100 if project.is_indexed else 0,
                "stage": "completed" if project.is_indexed else "idle",
                "current_step": "Ready" if project.is_indexed else "Not indexed yet",
                "logs": [f"[RAG Status] Project '{project.name}' is indexed and ready."] if project.is_indexed else ["[RAG Status] Project not indexed yet."],
                "model": project.embedding_model,
                "chunk_count": 0,
            }

        status_data["is_indexed"] = project.is_indexed
        status_data["embedding_model"] = project.embedding_model
        return Response(status_data)


def task_interface(request: HttpRequest) -> HttpResponse:
    latest_weather: Optional[Weather] = Weather.objects.first()
    weather_count: int = Weather.objects.count()

    diff: Optional[float] = None
    abs_diff: Optional[float] = None

    if weather_count > 1:
        # If we have at least 5 records, get the 5th (index 4)
        if weather_count >= 5:
            past_weather: Weather = Weather.objects.all()[4]
        # Otherwise, just grab the oldest available record
        else:
            past_weather: Weather = Weather.objects.last()

        diff = latest_weather.temp - past_weather.temp
        abs_diff = abs(diff)

    return render(request, "tasks.html", {"weather": latest_weather, "temp_diff": diff, "abs_diff": abs_diff})


@extend_schema(
    tags=["Users"],
    summary="List all users",
    description="Returns a list of all users registered in the system.",
)
class UserList(generics.ListAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer


@extend_schema(
    tags=["Users"],
    summary="Retrieve user details",
    description="Returns details for a specific user by ID.",
)
class UserDetail(generics.RetrieveAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
