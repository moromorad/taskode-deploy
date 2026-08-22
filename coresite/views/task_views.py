# pyrefly: ignore [missing-import]
from typing import Optional

from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from ..models import Project, Task, Weather
from ..serializers import ProjectSerializer, TaskSerializer, UserSerializer
from ..services import utils
from ..services.github_parser import sync_project_ast


class TaskViewSet(viewsets.ModelViewSet):
    
    # Tell Django which translator to use
    serializer_class = TaskSerializer

    def get_queryset(self):
        return Task.objects.filter(owner=self.request.user)

    def perform_create(self, serializer: BaseSerializer) -> None:
        serializer.save(owner=self.request.user)

    @action(detail=False, methods=['post'])
    def gen(self, request):
        text: str = request.data.get("text")
        user_timezone: str = request.data.get("timezone", "UTC")
        project_id = request.data.get("project_id")
        if not text: 
            return Response({"message": "No text provided"}, status=status.HTTP_400_BAD_REQUEST)
        
        project = None
        ast_outline = None

        if project_id:
            try:
                project = Project.objects.get(id=project_id, owner=request.user)
                ast_outline = project.ast_outline
            except Project.DoesNotExist:
                return Response({"error": "Project not found or access denied"}, status=status.HTTP_404_NOT_FOUND)
        
        try:
            task = utils.text_to_tasks(text, user_timezone, ast_outline)
        except Exception:
            task = utils.text_to_tasks(text, "UTC", ast_outline)

        task_data = task.model_dump()
        Task.objects.create(owner=request.user, project=project, **task_data)
            
        return Response({"message": "Task created successfully"}, status=status.HTTP_201_CREATED)
        

class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        project = serializer.save(owner=self.request.user)
        if project.github_repo:
            try:
                sync_project_ast(project, project.github_token)
            except Exception as e:
                print(f"Auto-sync failed for {project.name}: {e}")

    #endpoint: POST /api/projects/<id>/sync_repo/
    @action(detail=True, methods=['post'])
    def sync_repo(self, request, pk=None):
        project = self.get_object()
        
        github_token = request.data.get('github_token') or project.github_token or None
        
        try:

            result_message = sync_project_ast(project, github_token)
            
            if "Failed" in result_message or "empty" in result_message:
                return Response({"error": result_message}, status=status.HTTP_400_BAD_REQUEST)
                
            return Response({
                "status": "success", 
                "message": result_message,
                "ast_preview": project.ast_outline[:500] if project.ast_outline else ""
            })
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)




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


class UserList(generics.ListAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer


class UserDetail(generics.RetrieveAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
