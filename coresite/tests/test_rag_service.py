from unittest.mock import MagicMock, patch
import pytest

from coresite.services.rag_service import (
    embed_code_document,
    embed_code_query,
    get_chroma_client,
    index_project_chunks,
    normalize_vector,
    retrieve_relevant_code,
)


# ==========================================
# 1. Normalization & Chroma Client Tests
# ==========================================

def test_normalize_vector():
    vec = [3.0, 4.0]
    normed = normalize_vector(vec)
    assert pytest.approx(normed[0]) == 0.6
    assert pytest.approx(normed[1]) == 0.8

    zero_vec = [0.0, 0.0]
    assert normalize_vector(zero_vec) == [0.0, 0.0]


def test_get_chroma_client_http_success():
    mock_client = MagicMock()
    with patch("chromadb.HttpClient", return_value=mock_client):
        client = get_chroma_client()
        assert client == mock_client
        mock_client.heartbeat.assert_called_once()


def test_get_chroma_client_fallback_to_persistent():
    mock_persistent = MagicMock()
    with patch("chromadb.HttpClient", side_effect=Exception("Connection refused")):
        with patch("chromadb.PersistentClient", return_value=mock_persistent):
            client = get_chroma_client()
            assert client == mock_persistent


# ==========================================
# 2. Embedding Generation & Fallback Tests
# ==========================================

def test_embed_code_document_primary_model():
    mock_response = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.values = [0.1, 0.2, 0.3]
    mock_response.embeddings = [mock_embedding]

    with patch("coresite.services.rag_service.genai_client.models.embed_content", return_value=mock_response) as mock_embed:
        vec, model_used = embed_code_document("coresite/models.py", "class Project(models.Model): pass")
        assert vec == [0.1, 0.2, 0.3]
        assert model_used == "gemini-embedding-2"
        mock_embed.assert_called_once()
        call_kwargs = mock_embed.call_args.kwargs
        assert call_kwargs["model"] == "gemini-embedding-2"
        assert "title: coresite/models.py | text: class Project" in call_kwargs["contents"]


def test_embed_code_document_fallback_to_001():
    mock_response = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.values = [3.0, 4.0]
    mock_response.embeddings = [mock_embedding]

    def side_effect(*args, **kwargs):
        if kwargs.get("model") == "gemini-embedding-2":
            raise Exception("Rate limit on model 2")
        return mock_response

    with patch("coresite.services.rag_service.genai_client.models.embed_content", side_effect=side_effect):
        vec, model_used = embed_code_document("coresite/models.py", "class Project: pass")
        assert pytest.approx(vec[0]) == 0.6
        assert pytest.approx(vec[1]) == 0.8
        assert model_used == "gemini-embedding-001"


def test_embed_code_document_all_fail():
    with patch("coresite.services.rag_service.genai_client.models.embed_content", side_effect=Exception("Total failure")):
        with pytest.raises(Exception):
            embed_code_document("coresite/models.py", "code")


def test_embed_code_query_gemini_2():
    mock_response = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.values = [0.4, 0.5, 0.6]
    mock_response.embeddings = [mock_embedding]

    with patch("coresite.services.rag_service.genai_client.models.embed_content", return_value=mock_response) as mock_embed:
        res = embed_code_query("Add user login 2FA verification", model="gemini-embedding-2")
        assert res == [0.4, 0.5, 0.6]
        assert mock_embed.call_args.kwargs["model"] == "gemini-embedding-2"


def test_embed_code_query_gemini_001():
    mock_response = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.values = [3.0, 4.0]
    mock_response.embeddings = [mock_embedding]

    with patch("coresite.services.rag_service.genai_client.models.embed_content", return_value=mock_response) as mock_embed:
        res = embed_code_query("Add user login 2FA verification", model="gemini-embedding-001")
        assert pytest.approx(res[0]) == 0.6
        assert pytest.approx(res[1]) == 0.8
        assert mock_embed.call_args.kwargs["model"] == "gemini-embedding-001"


# ==========================================
# 3. Vector Indexing Tests
# ==========================================

def test_index_project_chunks_empty():
    count, model = index_project_chunks(1, [])
    assert count == 0
    assert model == "gemini-embedding-2"


def test_index_project_chunks_success():
    chunks = [
        {
            "filepath": "coresite/models.py",
            "symbol_type": "Class",
            "start_line": 10,
            "end_line": 20,
            "text": "File: coresite/models.py\nclass Task:\n    pass",
        },
    ]

    mock_collection = MagicMock()
    mock_chroma = MagicMock()
    mock_chroma.create_collection.return_value = mock_collection

    with patch("coresite.services.rag_service.get_chroma_client", return_value=mock_chroma):
        with patch("coresite.services.rag_service.embed_code_document", return_value=([0.1, 0.2], "gemini-embedding-2")):
            count, model_used = index_project_chunks(42, chunks)
            assert count == 1
            assert model_used == "gemini-embedding-2"
            mock_chroma.create_collection.assert_called_once_with(
                name="project_42",
                metadata={"hnsw:space": "cosine"},
            )
            mock_collection.add.assert_called_once()


# ==========================================
# 4. Code Retrieval Tests
# ==========================================

def test_retrieve_relevant_code_collection_not_found():
    mock_chroma = MagicMock()
    mock_chroma.get_collection.side_effect = Exception("Collection not found")

    with patch("coresite.services.rag_service.get_chroma_client", return_value=mock_chroma):
        snippets = retrieve_relevant_code(99, "query text")
        assert snippets == ""


def test_retrieve_relevant_code_empty_results():
    mock_collection = MagicMock()
    mock_collection.query.return_value = {"documents": [[]], "metadatas": [[]]}
    mock_chroma = MagicMock()
    mock_chroma.get_collection.return_value = mock_collection

    with patch("coresite.services.rag_service.get_chroma_client", return_value=mock_chroma):
        with patch("coresite.services.rag_service.embed_code_query", return_value=[0.1, 0.2]):
            snippets = retrieve_relevant_code(1, "query text")
            assert snippets == ""


def test_retrieve_relevant_code_success():
    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "documents": [
            [
                "def authenticate():\n    return True",
            ]
        ],
        "metadatas": [
            [
                {"filepath": "coresite/auth.py", "start_line": 15, "end_line": 25},
            ]
        ],
    }
    mock_chroma = MagicMock()
    mock_chroma.get_collection.return_value = mock_collection

    with patch("coresite.services.rag_service.get_chroma_client", return_value=mock_chroma):
        with patch("coresite.services.rag_service.embed_code_query", return_value=[0.1, 0.2]):
            result = retrieve_relevant_code(1, "authenticate user", model="gemini-embedding-001", top_k=1)
            assert "--- Code Snippet from coresite/auth.py (Lines 15-25) ---" in result
            assert "def authenticate():" in result
