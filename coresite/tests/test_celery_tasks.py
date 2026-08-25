from unittest.mock import MagicMock, patch
import pytest
from django.utils import timezone

from coresite.models import Weather
from coresite.tasks import fetch_weather_and_cleanup


@pytest.mark.django_db
def test_fetch_weather_and_cleanup_creates_record():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "current": {
            "temperature_2m": 28.5,
            "weather_code": 0,
            "time": "2026-08-24T12:00:00Z",
        }
    }

    with patch("coresite.tasks.requests.get", return_value=mock_response):
        fetch_weather_and_cleanup()

    latest = Weather.objects.latest("id")
    assert latest.temp == 28.5
    assert latest.weather == "Clear sky"
    assert latest.weather_code == 0


@pytest.mark.django_db
def test_fetch_weather_and_cleanup_deletes_excess_records():
    now = timezone.now()
    # Create 1005 weather records in bulk
    records = [
        Weather(temp=20.0, weather="Clear sky", time=now, weather_code=0)
        for _ in range(1005)
    ]
    Weather.objects.bulk_create(records)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "current": {
            "temperature_2m": 22.0,
            "weather_code": 1,
            "time": "2026-08-24T13:00:00Z",
        }
    }

    with patch("coresite.tasks.requests.get", return_value=mock_response):
        fetch_weather_and_cleanup()

    # Total should be capped at MAX_RECORDS (1000) + 1 newly added record before cleanup -> exactly 1000
    assert Weather.objects.count() <= 1000


@pytest.mark.django_db
def test_index_project_codebase_project_not_found():
    from coresite.tasks import index_project_codebase

    result = index_project_codebase(9999)
    assert result == "Project 9999 not found."


@pytest.mark.django_db
def test_index_project_codebase_no_github_repo(test_user):
    from coresite.models import Project
    from coresite.tasks import index_project_codebase

    project = Project.objects.create(name="No Repo Project", owner=test_user, github_repo="")
    result = index_project_codebase(project.id)
    assert "has no github_repo configured" in result


@pytest.mark.django_db
def test_index_project_codebase_no_files_found(test_user):
    from coresite.models import Project
    from coresite.tasks import index_project_codebase

    project = Project.objects.create(name="Empty Repo", owner=test_user, github_repo="owner/empty")
    with patch("coresite.tasks.fetch_repo_tree", return_value=[]):
        result = index_project_codebase(project.id)
        assert "No code files found" in result


@pytest.mark.django_db
def test_index_project_codebase_no_ast_blocks(test_user):
    from coresite.models import Project
    from coresite.tasks import index_project_codebase

    project = Project.objects.create(name="No AST Repo", owner=test_user, github_repo="owner/no-ast")
    with patch("coresite.tasks.fetch_repo_tree", return_value=["main.py"]):
        with patch("coresite.tasks.fetch_file_content", return_value="# comment only"):
            with patch("coresite.tasks.extract_ast_code_blocks", return_value=[]):
                result = index_project_codebase(project.id)
                assert "No AST code blocks extracted" in result


@pytest.mark.django_db
def test_index_project_codebase_no_chunks_after_guardrails(test_user):
    from coresite.models import Project
    from coresite.tasks import index_project_codebase

    project = Project.objects.create(name="Filtered Repo", owner=test_user, github_repo="owner/filtered")
    raw_blocks = [{"filepath": "stub.py", "code": "pass", "symbol_type": "Function", "start_line": 1, "end_line": 1}]
    with patch("coresite.tasks.fetch_repo_tree", return_value=["stub.py"]):
        with patch("coresite.tasks.fetch_file_content", return_value="pass"):
            with patch("coresite.tasks.extract_ast_code_blocks", return_value=raw_blocks):
                with patch("coresite.tasks.chunk_with_bpe_guardrails", return_value=[]):
                    result = index_project_codebase(project.id)
                    assert "No valid chunks after guardrails" in result


@pytest.mark.django_db
def test_index_project_codebase_success(test_user):
    from coresite.models import Project
    from coresite.tasks import index_project_codebase

    project = Project.objects.create(name="Valid Repo", owner=test_user, github_repo="owner/valid")
    raw_blocks = [{"filepath": "app.py", "code": "def run(): pass", "symbol_type": "Function", "start_line": 1, "end_line": 2}]
    final_chunks = [{"filepath": "app.py", "text": "def run(): pass", "symbol_type": "Function", "start_line": 1, "end_line": 2}]

    with patch("coresite.tasks.fetch_repo_tree", return_value=["app.py", "empty.py"]):
        def mock_content(repo, path, token=None):
            if path == "app.py":
                return "def run(): pass"
            return ""

        with patch("coresite.tasks.fetch_file_content", side_effect=mock_content):
            with patch("coresite.tasks.extract_ast_code_blocks", return_value=raw_blocks):
                with patch("coresite.tasks.chunk_with_bpe_guardrails", return_value=final_chunks):
                    with patch("coresite.tasks.index_project_chunks", return_value=(1, "gemini-embedding-2")) as mock_index:
                        result = index_project_codebase(project.id)
                        assert "Successfully indexed 1 chunks" in result
                        mock_index.assert_called_once_with(project.id, final_chunks)

                        project.refresh_from_db()
                        assert project.is_indexed is True
                        assert project.collection_name == f"project_{project.id}"
                        assert project.embedding_model == "gemini-embedding-2"
                        assert project.last_indexed_at is not None
