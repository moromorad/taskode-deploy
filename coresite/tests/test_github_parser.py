import io
import json
import urllib.error
from unittest.mock import MagicMock, patch
import pytest

from coresite.models import Project
from coresite.services.github_parser import (
    extract_symbols_multilang,
    fetch_file_content,
    fetch_repo_tree,
    sync_project_ast,
)


def test_extract_symbols_python():
    py_code = """
class Calculator:
    def add(self, a, b):
        return a + b

def standalone_func():
    pass
"""
    symbols = extract_symbols_multilang(py_code, ".py")
    assert any("class Calculator" in s for s in symbols)
    assert any("function standalone_func" in s for s in symbols)


def test_extract_symbols_javascript_and_jsx():
    js_code = """
class UserService {
    getUser() {}
}

function fetchDetails() {}

const helperArrow = () => {};
"""
    js_symbols = extract_symbols_multilang(js_code, ".js")
    assert any("class UserService" in s for s in js_symbols)
    assert any("function fetchDetails" in s for s in js_symbols)
    assert any("function getUser" in s for s in js_symbols)

    jsx_symbols = extract_symbols_multilang(js_code, ".jsx")
    assert any("class UserService" in s for s in jsx_symbols)


def test_extract_symbols_typescript_and_tsx():
    ts_code = """
class OrderManager {
    processOrder() {}
}
function validatePayment() {}
"""
    # Test that .ts and .tsx execute without throwing unhandled exceptions
    ts_symbols = extract_symbols_multilang(ts_code, ".ts")
    assert isinstance(ts_symbols, list)

    tsx_symbols = extract_symbols_multilang(ts_code, ".tsx")
    assert isinstance(tsx_symbols, list)


def test_extract_symbols_java():
    java_code = """
public class AccountService {
    public void deposit(double amount) {}
}
"""
    java_symbols = extract_symbols_multilang(java_code, ".java")
    assert any("class AccountService" in s for s in java_symbols)
    assert any("function deposit" in s for s in java_symbols)


def test_extract_symbols_unsupported_extension():
    assert extract_symbols_multilang("print('hello')", ".txt") == []
    assert extract_symbols_multilang("print('hello')", ".cpp") == []


def test_extract_symbols_parsing_exception():
    with patch("coresite.services.github_parser.Parser", side_effect=Exception("Parser crashed")):
        assert extract_symbols_multilang("code", ".py") == []


def test_fetch_repo_tree_success():
    mock_response = MagicMock()
    mock_payload = {
        "tree": [
            {"path": "app/models.py", "type": "blob"},
            {"path": "frontend/index.tsx", "type": "blob"},
            {"path": "src/main/Game.java", "type": "blob"},
            {"path": "src/test/GameTest.java", "type": "blob"},
            {"path": "tests/test_models.py", "type": "blob"},
            {"path": "coresite/migrations/0001_initial.py", "type": "blob"},
            {"path": "frontend/bundle.min.js", "type": "blob"},
            {"path": "types/index.d.ts", "type": "blob"},
            {"path": "node_modules/pkg/index.js", "type": "blob"},
            {"path": "docs/readme.md", "type": "blob"},
            {"path": "src", "type": "tree"},
        ]
    }
    mock_response.read.return_value = json.dumps(mock_payload).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        files = fetch_repo_tree("owner/repo", github_token="pat123")
        assert "app/models.py" in files
        assert "frontend/index.tsx" in files
        assert "src/main/Game.java" in files
        assert "src/test/GameTest.java" not in files
        assert "tests/test_models.py" not in files
        assert "coresite/migrations/0001_initial.py" not in files
        assert "frontend/bundle.min.js" not in files
        assert "types/index.d.ts" not in files
        assert "node_modules/pkg/index.js" not in files
        assert "docs/readme.md" not in files


def test_fetch_repo_tree_http_error():
    http_err = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
    with patch("urllib.request.urlopen", side_effect=http_err):
        assert fetch_repo_tree("owner/repo") == []


def test_fetch_repo_tree_general_error():
    with patch("urllib.request.urlopen", side_effect=Exception("Network failure")):
        assert fetch_repo_tree("owner/repo") == []


def test_fetch_file_content_main_branch_success():
    mock_response = MagicMock()
    mock_response.read.return_value = b"def main(): pass"
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        content = fetch_file_content("owner/repo", "main.py", github_token="pat123")
        assert content == "def main(): pass"


def test_fetch_file_content_fallback_to_master():
    err_404 = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
    master_response = MagicMock()
    master_response.read.return_value = b"def master_code(): pass"
    master_response.__enter__.return_value = master_response

    def urlopen_side_effect(req, *args, **kwargs):
        if "/main/" in req.full_url:
            raise err_404
        return master_response

    with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
        content = fetch_file_content("owner/repo", "main.py", github_token="pat123")
        assert content == "def master_code(): pass"


def test_fetch_file_content_fallback_to_master_with_exception():
    err_404 = urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    def urlopen_side_effect(req, *args, **kwargs):
        if "/main/" in req.full_url:
            raise err_404
        raise Exception("Master branch read failed")

    with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
        content = fetch_file_content("owner/repo", "main.py", github_token="pat123")
        assert content == ""


def test_fetch_file_content_404_master_fails():
    err_404 = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
    with patch("urllib.request.urlopen", side_effect=err_404):
        content = fetch_file_content("owner/repo", "main.py")
        assert content == ""


def test_fetch_file_content_http_error_non_404():
    err_500 = urllib.error.HTTPError("url", 500, "Server Error", {}, None)
    with patch("urllib.request.urlopen", side_effect=err_500):
        assert fetch_file_content("owner/repo", "main.py") == ""


def test_fetch_file_content_general_exception():
    with patch("urllib.request.urlopen", side_effect=Exception("Unexpected crash")):
        assert fetch_file_content("owner/repo", "main.py") == ""


@pytest.mark.django_db
def test_sync_project_ast_empty_repo(test_user):
    project = Project.objects.create(name="Empty Repo Project", github_repo="owner/empty", owner=test_user)
    with patch("coresite.services.github_parser.fetch_repo_tree", return_value=[]):
        res = sync_project_ast(project)
        assert res == "Failed to fetch files or repository is empty."


@pytest.mark.django_db
def test_sync_project_ast_no_symbols_found(test_user):
    project = Project.objects.create(name="No Symbols Project", github_repo="owner/repo", owner=test_user)
    with patch("coresite.services.github_parser.fetch_repo_tree", return_value=["empty.py"]):
        with patch("coresite.services.github_parser.fetch_file_content", return_value="# only comments"):
            res = sync_project_ast(project)
            assert res == "No functions or classes found in the repository."


@pytest.mark.django_db
def test_sync_project_ast_success(test_user):
    project = Project.objects.create(name="Valid Repo Project", github_repo="owner/valid", owner=test_user)
    with patch("coresite.services.github_parser.fetch_repo_tree", return_value=["main.py", "empty.py"]):
        def mock_fetch_content(repo, path, token=None):
            if path == "main.py":
                return "class Server:\n    pass"
            return ""

        with patch("coresite.services.github_parser.fetch_file_content", side_effect=mock_fetch_content):
            res = sync_project_ast(project)
            assert res == "Sync successful"
            project.refresh_from_db()
            assert "File: main.py" in project.ast_outline
            assert "class Server" in project.ast_outline
