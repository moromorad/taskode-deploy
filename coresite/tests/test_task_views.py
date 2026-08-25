from unittest.mock import MagicMock, patch
import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory
from django.utils import timezone
from rest_framework import status

from coresite.models import Project, Task, Weather
from coresite.views.task_views import (
    ProjectViewSet,
    TaskViewSet,
    UserDetail,
    UserList,
    task_interface,
)


@pytest.mark.django_db
def test_task_viewset_crud(auth_client, test_user, sample_project):
    # Create
    create_payload = {
        "title": "New Test Task",
        "description": "Task description here",
        "ticket_type": "bug",
        "project": sample_project.id,
    }
    create_resp = auth_client.post("/api/tasks/", create_payload)
    assert create_resp.status_code == status.HTTP_201_CREATED
    task_id = create_resp.json()["id"]

    # List
    list_resp = auth_client.get("/api/tasks/")
    assert list_resp.status_code == status.HTTP_200_OK
    assert any(t["id"] == task_id for t in list_resp.json())

    # Retrieve
    retrieve_resp = auth_client.get(f"/api/tasks/{task_id}/")
    assert retrieve_resp.status_code == status.HTTP_200_OK
    assert retrieve_resp.json()["title"] == "New Test Task"

    # Update (PUT)
    put_payload = {
        "title": "Updated Task Title",
        "description": "Updated description",
        "ticket_type": "chore",
        "completed": True,
        "project": sample_project.id,
    }
    put_resp = auth_client.put(f"/api/tasks/{task_id}/", put_payload)
    assert put_resp.status_code == status.HTTP_200_OK
    assert put_resp.json()["title"] == "Updated Task Title"
    assert put_resp.json()["completed"] is True

    # Partial Update (PATCH)
    patch_resp = auth_client.patch(f"/api/tasks/{task_id}/", {"completed": False})
    assert patch_resp.status_code == status.HTTP_200_OK
    assert patch_resp.json()["completed"] is False

    # Delete
    delete_resp = auth_client.delete(f"/api/tasks/{task_id}/")
    assert delete_resp.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
def test_task_viewset_swagger_fake_view(test_user):
    view = TaskViewSet()
    view.swagger_fake_view = True
    assert view.get_queryset().count() == 0


@pytest.mark.django_db
def test_project_viewset_crud_and_autosync(auth_client, test_user):
    # Create with github_repo triggering auto-sync
    with patch("coresite.views.task_views.sync_project_ast", return_value="Sync successful") as mock_sync:
        create_resp = auth_client.post("/api/projects/", {
            "name": "Sync Project",
            "github_repo": "owner/repo",
            "github_token": "token123",
        })
        assert create_resp.status_code == status.HTTP_201_CREATED
        project_id = create_resp.json()["id"]
        assert mock_sync.called

    # Create with auto-sync raising exception
    with patch("coresite.views.task_views.sync_project_ast", side_effect=Exception("Network error")):
        create_resp2 = auth_client.post("/api/projects/", {
            "name": "Failing Sync Project",
            "github_repo": "owner/failing-repo",
        })
        assert create_resp2.status_code == status.HTTP_201_CREATED

    # List
    list_resp = auth_client.get("/api/projects/")
    assert list_resp.status_code == status.HTTP_200_OK
    assert any(p["id"] == project_id for p in list_resp.json())

    # Retrieve
    retrieve_resp = auth_client.get(f"/api/projects/{project_id}/")
    assert retrieve_resp.status_code == status.HTTP_200_OK
    assert retrieve_resp.json()["name"] == "Sync Project"

    # Partial update
    patch_resp = auth_client.patch(f"/api/projects/{project_id}/", {"name": "Renamed Project"})
    assert patch_resp.status_code == status.HTTP_200_OK
    assert patch_resp.json()["name"] == "Renamed Project"

    # Put update
    put_resp = auth_client.put(f"/api/projects/{project_id}/", {"name": "Full Put Renamed"})
    assert put_resp.status_code == status.HTTP_200_OK

    # Delete
    delete_resp = auth_client.delete(f"/api/projects/{project_id}/")
    assert delete_resp.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
def test_project_viewset_swagger_fake_view():
    view = ProjectViewSet()
    view.swagger_fake_view = True
    assert view.get_queryset().count() == 0


@pytest.mark.django_db
def test_project_sync_repo_endpoint_success(auth_client, sample_project):
    with patch("coresite.views.task_views.sync_project_ast", return_value="Sync successful"):
        sample_project.ast_outline = "File: main.py\n  class App"
        sample_project.save()

        response = auth_client.post(f"/api/projects/{sample_project.id}/sync_repo/", {
            "github_token": "custom_token"
        })
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "success"
        assert "main.py" in data["ast_preview"]


@pytest.mark.django_db
def test_project_sync_repo_endpoint_failure_message(auth_client, sample_project):
    with patch("coresite.views.task_views.sync_project_ast", return_value="Failed to fetch files or repository is empty."):
        response = auth_client.post(f"/api/projects/{sample_project.id}/sync_repo/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Failed" in response.json()["error"]


@pytest.mark.django_db
def test_project_sync_repo_endpoint_exception(auth_client, sample_project):
    with patch("coresite.views.task_views.sync_project_ast", side_effect=Exception("Connection timed out")):
        response = auth_client.post(f"/api/projects/{sample_project.id}/sync_repo/")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Connection timed out" in response.json()["error"]


@pytest.mark.django_db
def test_project_index_status_cached(auth_client, sample_project):
    from coresite.tasks import update_project_progress
    update_project_progress(
        project_id=sample_project.id,
        progress=65,
        stage="batch_embedding",
        current_step="Processing batch 1/2...",
        new_log="[RAG Indexing] 🚀 Starting...",
        model="gemini-embedding-2",
        chunk_count=78,
    )

    response = auth_client.get(f"/api/projects/{sample_project.id}/index_status/")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["progress"] == 65
    assert data["stage"] == "batch_embedding"
    assert len(data["logs"]) >= 1


@pytest.mark.django_db
def test_project_index_status_not_cached(auth_client, sample_project):
    from django.core.cache import cache
    from coresite.tasks import _IN_MEMORY_RAG_STATUS
    cache.delete(f"project_rag_status:{sample_project.id}")
    _IN_MEMORY_RAG_STATUS.pop(sample_project.id, None)

    sample_project.is_indexed = True
    sample_project.embedding_model = "gemini-embedding-2"
    sample_project.save()

    response = auth_client.get(f"/api/projects/{sample_project.id}/index_status/")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "completed"
    assert data["progress"] == 100
    assert data["is_indexed"] is True


@pytest.mark.django_db
def test_task_interface_view(rf: RequestFactory):
    request = rf.get("/tasks/")

    # Case 1: 0 weather records
    Weather.objects.all().delete()
    resp1 = task_interface(request)
    assert resp1.status_code == 200

    # Case 2: 2 weather records (< 5)
    now = timezone.now()
    Weather.objects.create(temp=20.0, time=now, weather="Sunny", weather_code=0)
    Weather.objects.create(temp=18.0, time=now, weather="Cloudy", weather_code=2)
    resp2 = task_interface(request)
    assert resp2.status_code == 200

    # Case 3: 6 weather records (>= 5)
    for i in range(4):
        Weather.objects.create(temp=15.0 + i, time=now, weather="Rain", weather_code=61)
    resp3 = task_interface(request)
    assert resp3.status_code == 200


@pytest.mark.django_db
def test_user_list_and_detail(auth_client, test_user):
    list_resp = auth_client.get("/api/users/")
    assert list_resp.status_code == status.HTTP_200_OK
    assert any(u["id"] == test_user.id for u in list_resp.json())

    detail_resp = auth_client.get(f"/api/users/{test_user.id}/")
    assert detail_resp.status_code == status.HTTP_200_OK
    assert detail_resp.json()["username"] == test_user.username


@pytest.mark.django_db
def test_task_viewset_gen_no_text(auth_client):
    resp = auth_client.post("/api/tasks/gen/", {})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_task_viewset_gen_project_not_found(auth_client):
    resp = auth_client.post("/api/tasks/gen/", {"text": "Make a button", "project_id": 9999})
    assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_task_viewset_gen_with_indexed_project_rag(auth_client, test_user, sample_project):
    sample_project.is_indexed = True
    sample_project.ast_outline = "File: auth.py\n  class Login"
    sample_project.embedding_model = "gemini-embedding-2"
    sample_project.save()

    mock_task_obj = MagicMock()
    mock_task_obj.model_dump.return_value = {
        "title": "Add 2FA Login",
        "description": "Implement 2FA flow",
        "ticket_type": "feature",
        "due_date": None,
        "completed": False,
        "subtasks": [{"title": "Subtask 1", "completed": False}],
    }

    with patch("coresite.views.task_views.retrieve_relevant_code", return_value="def login(): pass") as mock_rag:
        with patch("coresite.views.task_views.utils.text_to_tasks", return_value=mock_task_obj) as mock_ai:
            resp = auth_client.post("/api/tasks/gen/", {
                "text": "Add 2FA Login",
                "timezone": "UTC",
                "project_id": sample_project.id,
            })
            assert resp.status_code == status.HTTP_201_CREATED
            mock_rag.assert_called_once_with(sample_project.id, "Add 2FA Login", model="gemini-embedding-2", top_k=4)
            mock_ai.assert_called_once_with("Add 2FA Login", "UTC", sample_project.ast_outline, "def login(): pass")

            created = Task.objects.filter(owner=test_user, title="Add 2FA Login").first()
            assert created is not None
            assert created.project == sample_project
