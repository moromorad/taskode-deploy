from unittest.mock import MagicMock, patch
import pytest
from django.utils import timezone



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
                    with patch("coresite.tasks.index_project_chunks", return_value=(1, "gemini-embedding-001", {"cached": 0, "new": 1, "deleted": 0})) as mock_index:
                        result = index_project_codebase(project.id)
                        assert "Successfully indexed 1 chunks" in result
                        assert mock_index.call_count == 1
                        assert mock_index.call_args[0][0] == project.id
                        assert mock_index.call_args[0][1] == final_chunks

                        project.refresh_from_db()
                        assert project.is_indexed is True
                        assert project.collection_name == f"project_{project.id}"
                        assert project.embedding_model == "gemini-embedding-001"
                        assert project.last_indexed_at is not None
