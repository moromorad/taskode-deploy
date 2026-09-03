from unittest.mock import MagicMock, patch
import pytest

from coresite.models import Project, CodeChunk
from django.contrib.auth.models import User
from coresite.services.rag_service import (
    compute_chunk_content_hash,
    embed_code_document,
    embed_code_documents_batch,
    embed_code_query,
    index_project_chunks,
    normalize_vector,
    retrieve_relevant_code,
    retrieve_relevant_code_with_metadata,
)


# ==========================================
# 1. Normalization Tests
# ==========================================

def test_normalize_vector():
    vec = [3.0, 4.0]
    normed = normalize_vector(vec)
    assert pytest.approx(normed[0]) == 0.6
    assert pytest.approx(normed[1]) == 0.8

    zero_vec = [0.0, 0.0]
    assert normalize_vector(zero_vec) == [0.0, 0.0]


# ==========================================
# 2. Embedding Generation Tests (gemini-embedding-001)
# ==========================================

def test_embed_code_document_primary_model():
    mock_response = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.values = [3.0, 4.0]
    mock_response.embeddings = [mock_embedding]

    with patch("coresite.services.rag_service.genai_client.models.embed_content", return_value=mock_response):
        vec, model_used = embed_code_document("coresite/models.py", "class Project(models.Model): pass")
        assert pytest.approx(vec[0]) == 0.6
        assert pytest.approx(vec[1]) == 0.8
        assert model_used == "gemini-embedding-001"


def test_embed_code_documents_batch_slicing():
    mock_response_1 = MagicMock()
    mock_response_1.embeddings = [MagicMock(values=[1.0, 0.0])]

    mock_response_2 = MagicMock()
    mock_response_2.embeddings = [MagicMock(values=[0.0, 1.0])]

    chunks = [
        {"filepath": "a.py", "text": "chunk 1"},
        {"filepath": "b.py", "text": "chunk 2"},
    ]

    with patch("coresite.services.rag_service.genai_client.models.embed_content", side_effect=[mock_response_1, mock_response_2]) as mock_embed:
        with patch("time.sleep"):  # skip sleep in tests
            vectors, model_used = embed_code_documents_batch(chunks, batch_size=1)
            assert len(vectors) == 2
            assert mock_embed.call_count == 2
            assert pytest.approx(vectors[0][0]) == 1.0
            assert pytest.approx(vectors[1][1]) == 1.0


def test_embed_code_documents_batch_fails_raises_error():
    chunks = [{"filepath": "fail.py", "text": "error code"}]

    with patch("coresite.services.rag_service.genai_client.models.embed_content", side_effect=Exception("API Quota Exhausted")):
        with pytest.raises(Exception) as exc_info:
            embed_code_documents_batch(chunks, batch_size=1)
        assert "API Quota Exhausted" in str(exc_info.value)


def test_embed_code_query_gemini_001():
    mock_response = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.values = [0.0, 5.0]
    mock_response.embeddings = [mock_embedding]

    with patch("coresite.services.rag_service.genai_client.models.embed_content", return_value=mock_response):
        vec = embed_code_query("How do I authenticate a user?")
        assert pytest.approx(vec[0]) == 0.0
        assert pytest.approx(vec[1]) == 1.0


# ==========================================
# 3. Hash Generation Tests
# ==========================================

def test_compute_chunk_content_hash_deterministic():
    code_v1 = "File: main.py\nLines: 1-10\n----------------------------------------\ndef run():\n    return 42"
    code_v2 = "File: main.py\nLines: 50-60\n----------------------------------------\ndef run():\n    return 42"
    code_v3 = "File: main.py\nLines: 1-10\n----------------------------------------\ndef run():\n    return 99"

    hash_v1 = compute_chunk_content_hash(code_v1, "main.py")
    hash_v2 = compute_chunk_content_hash(code_v2, "main.py")
    hash_v3 = compute_chunk_content_hash(code_v3, "main.py")

    assert hash_v1 == hash_v2
    assert hash_v1 != hash_v3


# ==========================================
# 4. Vector Storage & Indexing Tests (Database)
# ==========================================

@pytest.mark.django_db
def test_index_project_chunks_empty():
    count, model_used, diff_stats = index_project_chunks(1, [])
    assert count == 0
    assert diff_stats["cached"] == 0
    assert diff_stats["new"] == 0


@pytest.mark.django_db
def test_index_project_chunks_success():
    user = User.objects.create_user(username="testuser", password="testpassword")
    project = Project.objects.create(name="TestProj", owner=user)

    chunks = [
        {
            "filepath": "coresite/models.py",
            "symbol_type": "Class",
            "start_line": 10,
            "end_line": 20,
            "text": "File: coresite/models.py\nclass Task:\n    pass",
        },
    ]

    with patch("coresite.services.rag_service.embed_code_documents_batch", return_value=([[0.1] * 768], "gemini-embedding-001")):
        count, model_used, diff_stats = index_project_chunks(project.id, chunks)
        assert count == 1
        assert model_used == "gemini-embedding-001"
        assert diff_stats["new"] == 1
        assert diff_stats["cached"] == 0
        assert CodeChunk.objects.filter(project=project).count() == 1


@pytest.mark.django_db
def test_index_project_chunks_incremental_all_cached():
    user = User.objects.create_user(username="testuser2", password="testpassword")
    project = Project.objects.create(name="TestProj2", owner=user)

    text = "File: coresite/models.py\nclass Task:\n    pass"
    content_hash = compute_chunk_content_hash(text, "coresite/models.py")
    doc_id = f"chunk_{project.id}_{content_hash}"

    CodeChunk.objects.create(
        project=project,
        chunk_id=doc_id,
        filepath="coresite/models.py",
        text=text,
        start_line=10,
        end_line=20,
        content_hash=content_hash,
        embedding=[0.1] * 768,
    )

    chunks = [
        {
            "filepath": "coresite/models.py",
            "symbol_type": "Class",
            "start_line": 10,
            "end_line": 20,
            "text": text,
        },
    ]

    with patch("coresite.services.rag_service.embed_code_documents_batch") as mock_embed:
        count, model_used, diff_stats = index_project_chunks(project.id, chunks)
        assert count == 1
        assert diff_stats["cached"] == 1
        assert diff_stats["new"] == 0
        assert diff_stats["deleted"] == 0
        mock_embed.assert_not_called()


@pytest.mark.django_db
def test_index_project_chunks_incremental_partial_diff():
    user = User.objects.create_user(username="testuser3", password="testpassword")
    project = Project.objects.create(name="TestProj3", owner=user)

    text1 = "File: app/a.py\ndef func_a(): pass"
    hash1 = compute_chunk_content_hash(text1, "app/a.py")
    doc_id1 = f"chunk_{project.id}_{hash1}"

    # Existing chunk 1
    CodeChunk.objects.create(
        project=project,
        chunk_id=doc_id1,
        filepath="app/a.py",
        text=text1,
        start_line=1,
        end_line=2,
        content_hash=hash1,
        embedding=[0.1] * 768,
    )

    # Obsolete chunk to be deleted
    CodeChunk.objects.create(
        project=project,
        chunk_id=f"chunk_{project.id}_old_obsolete",
        filepath="app/old.py",
        text="old text",
        start_line=1,
        end_line=5,
        content_hash="old_obsolete",
        embedding=[0.1] * 768,
    )

    text2 = "File: app/b.py\ndef func_b_new(): pass"
    chunks = [
        {"filepath": "app/a.py", "symbol_type": "Function", "start_line": 1, "end_line": 2, "text": text1},
        {"filepath": "app/b.py", "symbol_type": "Function", "start_line": 5, "end_line": 10, "text": text2},
    ]

    with patch("coresite.services.rag_service.embed_code_documents_batch", return_value=([[0.5] * 768], "gemini-embedding-001")) as mock_embed:
        count, model_used, diff_stats = index_project_chunks(project.id, chunks)
        assert count == 2
        assert diff_stats["cached"] == 1
        assert diff_stats["new"] == 1
        assert diff_stats["deleted"] == 1
        assert mock_embed.call_count == 1
        assert CodeChunk.objects.filter(project=project).count() == 2


@pytest.mark.django_db
def test_index_project_chunks_line_shift_metadata_update():
    user = User.objects.create_user(username="testuser4", password="testpassword")
    project = Project.objects.create(name="TestProj4", owner=user)

    text_old_lines = "File: app/a.py\nLines: 10-20\n----------------------------------------\ndef func_a(): pass"
    text_new_lines = "File: app/a.py\nLines: 15-25\n----------------------------------------\ndef func_a(): pass"

    content_hash = compute_chunk_content_hash(text_new_lines, "app/a.py")
    doc_id = f"chunk_{project.id}_{content_hash}"

    chunk_obj = CodeChunk.objects.create(
        project=project,
        chunk_id=doc_id,
        filepath="app/a.py",
        text=text_old_lines,
        start_line=10,
        end_line=20,
        content_hash=content_hash,
        embedding=[0.1] * 768,
    )

    chunks = [
        {"filepath": "app/a.py", "symbol_type": "Function", "start_line": 15, "end_line": 25, "text": text_new_lines},
    ]

    with patch("coresite.services.rag_service.embed_code_documents_batch") as mock_embed:
        count, model_used, diff_stats = index_project_chunks(project.id, chunks)
        assert count == 1
        assert diff_stats["cached"] == 1
        assert diff_stats["updated_lines"] == 1
        assert diff_stats["new"] == 0
        mock_embed.assert_not_called()
        chunk_obj.refresh_from_db()
        assert chunk_obj.start_line == 15
        assert chunk_obj.end_line == 25


# ==========================================
# 5. Code Retrieval Tests
# ==========================================

@pytest.mark.django_db
def test_retrieve_relevant_code_empty_project():
    snippets = retrieve_relevant_code(999, "query text")
    assert snippets == ""


@pytest.mark.django_db
def test_retrieve_relevant_code_success():
    user = User.objects.create_user(username="testuser5", password="testpassword")
    project = Project.objects.create(name="TestProj5", owner=user)

    CodeChunk.objects.create(
        project=project,
        chunk_id="c1",
        filepath="coresite/auth.py",
        text="def authenticate():\n    return True",
        symbol_type="function",
        start_line=15,
        end_line=25,
        content_hash="h1",
        embedding=[1.0] + [0.0] * 767,
    )

    with patch("coresite.services.rag_service.embed_code_query", return_value=[1.0] + [0.0] * 767):
        result = retrieve_relevant_code(project.id, "authenticate user", model="gemini-embedding-001", top_k=1)
        assert "--- Code Snippet from coresite/auth.py (Lines 15-25) ---" in result
        assert "def authenticate():" in result


@pytest.mark.django_db
def test_retrieve_relevant_code_with_metadata_structured():
    user = User.objects.create_user(username="testuser6", password="testpassword")
    project = Project.objects.create(name="TestProj6", owner=user)

    CodeChunk.objects.create(
        project=project,
        chunk_id="c2",
        filepath="coresite/auth.py",
        text="def authenticate():\n    return True",
        symbol_type="function",
        start_line=15,
        end_line=25,
        content_hash="h2",
        embedding=[1.0] + [0.0] * 767,
    )

    with patch("coresite.services.rag_service.embed_code_query", return_value=[1.0] + [0.0] * 767):
        text, chunks = retrieve_relevant_code_with_metadata(project.id, "auth query", model="gemini-embedding-001", top_k=1)
        assert "--- Code Snippet from coresite/auth.py (Lines 15-25) ---" in text
        assert len(chunks) == 1
        assert chunks[0]["filepath"] == "coresite/auth.py"
        assert chunks[0]["start_line"] == 15
        assert chunks[0]["end_line"] == 25
        assert chunks[0]["symbol_type"] == "function"
        assert chunks[0]["distance"] == 0.0
        assert "def authenticate():" in chunks[0]["code"]
