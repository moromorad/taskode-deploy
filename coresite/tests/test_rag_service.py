from unittest.mock import MagicMock, patch
import pytest

from coresite.services.rag_service import (
    compute_chunk_content_hash,
    embed_code_document,
    embed_code_documents_batch,
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
# 2. Embedding Generation Tests (gemini-embedding-001)
# ==========================================

def test_embed_code_document_primary_model():
    mock_response = MagicMock()
    mock_embedding = MagicMock()
    mock_embedding.values = [3.0, 4.0]
    mock_response.embeddings = [mock_embedding]

    with patch("coresite.services.rag_service.genai_client.models.embed_content", return_value=mock_response) as mock_embed:
        vec, model_used = embed_code_document("coresite/models.py", "class Project(models.Model): pass")
        assert pytest.approx(vec[0]) == 0.6
        assert pytest.approx(vec[1]) == 0.8
        assert model_used == "gemini-embedding-001"
        mock_embed.assert_called_once()
        call_kwargs = mock_embed.call_args.kwargs
        assert call_kwargs["model"] == "gemini-embedding-001"
        assert "class Project(models.Model): pass" in call_kwargs["contents"]


def test_embed_code_documents_batch_slicing():
    chunks = [
        {"filepath": f"file_{i}.py", "text": f"code_{i}"}
        for i in range(5)
    ]

    def side_effect(*args, **kwargs):
        resp = MagicMock()
        items = kwargs.get("contents", [])
        resp.embeddings = [MagicMock(values=[3.0, 4.0]) for _ in items]
        return resp

    with patch("coresite.services.rag_service.genai_client.models.embed_content", side_effect=side_effect) as mock_embed:
        vectors, model_used = embed_code_documents_batch(chunks, batch_size=2, model="gemini-embedding-001")
        assert len(vectors) == 5
        assert model_used == "gemini-embedding-001"
        assert mock_embed.call_count == 3  # 5 items with batch_size 2 -> 3 batch API requests!
        for v in vectors:
            assert pytest.approx(v[0]) == 0.6
            assert pytest.approx(v[1]) == 0.8


def test_embed_code_documents_batch_fails_raises_error():
    chunks = [{"filepath": "app.py", "text": "def test(): pass"}]

    with patch("coresite.services.rag_service.genai_client.models.embed_content", side_effect=Exception("429 Quota Exceeded")):
        with pytest.raises(Exception) as exc_info:
            embed_code_documents_batch(chunks, model="gemini-embedding-001")
        assert "429 Quota Exceeded" in str(exc_info.value)


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

def test_compute_chunk_content_hash_deterministic():
    text1 = "File: app/models.py\nType: Class\nLines: 10-25\n----------------------------------------\nclass Task:\n    pass"
    text2 = "File: app/models.py\nType: Class\nLines: 15-30\n----------------------------------------\nclass Task:\n    pass"
    text3 = "File: app/models.py\nType: Class\nLines: 10-25\n----------------------------------------\nclass Project:\n    pass"

    hash1 = compute_chunk_content_hash(text1, "app/models.py")
    hash2 = compute_chunk_content_hash(text2, "app/models.py")
    hash3 = compute_chunk_content_hash(text3, "app/models.py")

    # Hash 1 and 2 must match because only volatile line numbers shifted!
    assert hash1 == hash2
    # Hash 3 must differ because the code body changed
    assert hash1 != hash3


# ==========================================
# 3. Vector Indexing Tests
# ==========================================

def test_index_project_chunks_empty():
    count, model, diff_stats = index_project_chunks(1, [])
    assert count == 0
    assert model == "gemini-embedding-001"
    assert diff_stats["cached"] == 0
    assert diff_stats["new"] == 0


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
    mock_collection.get.return_value = {"ids": [], "metadatas": []}
    mock_chroma = MagicMock()
    mock_chroma.get_or_create_collection.return_value = mock_collection

    with patch("coresite.services.rag_service.get_chroma_client", return_value=mock_chroma):
        with patch("coresite.services.rag_service.embed_code_documents_batch", return_value=([[0.1, 0.2]], "gemini-embedding-001")):
            count, model_used, diff_stats = index_project_chunks(42, chunks)
            assert count == 1
            assert model_used == "gemini-embedding-001"
            assert diff_stats["new"] == 1
            assert diff_stats["cached"] == 0
            mock_chroma.get_or_create_collection.assert_called_once_with(
                name="project_42",
                metadata={"hnsw:space": "cosine"},
            )
            mock_collection.upsert.assert_called_once()


def test_index_project_chunks_incremental_all_cached():
    text = "File: coresite/models.py\nclass Task:\n    pass"
    content_hash = compute_chunk_content_hash(text, "coresite/models.py")
    doc_id = f"chunk_42_{content_hash}"

    chunks = [
        {
            "filepath": "coresite/models.py",
            "symbol_type": "Class",
            "start_line": 10,
            "end_line": 20,
            "text": text,
        },
    ]

    mock_collection = MagicMock()
    # Existing collection already has this doc_id!
    mock_collection.get.return_value = {
        "ids": [doc_id],
        "metadatas": [{"filepath": "coresite/models.py", "start_line": 10, "end_line": 20}],
    }
    mock_chroma = MagicMock()
    mock_chroma.get_or_create_collection.return_value = mock_collection

    with patch("coresite.services.rag_service.get_chroma_client", return_value=mock_chroma):
        with patch("coresite.services.rag_service.embed_code_documents_batch") as mock_embed:
            count, model_used, diff_stats = index_project_chunks(42, chunks)
            assert count == 1
            assert diff_stats["cached"] == 1
            assert diff_stats["new"] == 0
            assert diff_stats["deleted"] == 0
            # 0 API calls! embed_code_documents_batch is never called!
            mock_embed.assert_not_called()
            mock_collection.upsert.assert_not_called()


def test_index_project_chunks_incremental_partial_diff():
    # Chunk 1 (unchanged)
    text1 = "File: app/a.py\ndef func_a(): pass"
    hash1 = compute_chunk_content_hash(text1, "app/a.py")
    doc_id1 = f"chunk_42_{hash1}"

    # Chunk 2 (new)
    text2 = "File: app/b.py\ndef func_b_new(): pass"

    # Obsolete chunk in ChromaDB
    obsolete_id = "chunk_42_old_obsolete_hash"

    chunks = [
        {"filepath": "app/a.py", "symbol_type": "Function", "start_line": 1, "end_line": 2, "text": text1},
        {"filepath": "app/b.py", "symbol_type": "Function", "start_line": 5, "end_line": 10, "text": text2},
    ]

    mock_collection = MagicMock()
    mock_collection.get.return_value = {
        "ids": [doc_id1, obsolete_id],
        "metadatas": [
            {"filepath": "app/a.py", "start_line": 1, "end_line": 2},
            {"filepath": "app/old.py", "start_line": 1, "end_line": 5},
        ],
    }
    mock_chroma = MagicMock()
    mock_chroma.get_or_create_collection.return_value = mock_collection

    with patch("coresite.services.rag_service.get_chroma_client", return_value=mock_chroma):
        with patch("coresite.services.rag_service.embed_code_documents_batch", return_value=([[0.5, 0.5]], "gemini-embedding-001")) as mock_embed:
            count, model_used, diff_stats = index_project_chunks(42, chunks)
            assert count == 2
            assert diff_stats["cached"] == 1
            assert diff_stats["new"] == 1
            assert diff_stats["deleted"] == 1
            # Only chunk 2 was sent to Gemini
            assert mock_embed.call_count == 1
            assert len(mock_embed.call_args[0][0]) == 1
            assert mock_embed.call_args[0][0][0]["filepath"] == "app/b.py"
            # Obsolete chunk was pruned
            mock_collection.delete.assert_called_once_with(ids=[obsolete_id])


def test_index_project_chunks_line_shift_metadata_update():
    text_old_lines = "File: app/a.py\nLines: 10-20\n----------------------------------------\ndef func_a(): pass"
    text_new_lines = "File: app/a.py\nLines: 15-25\n----------------------------------------\ndef func_a(): pass"

    # Same content hash because code is identical
    content_hash = compute_chunk_content_hash(text_new_lines, "app/a.py")
    doc_id = f"chunk_42_{content_hash}"

    chunks = [
        {"filepath": "app/a.py", "symbol_type": "Function", "start_line": 15, "end_line": 25, "text": text_new_lines},
    ]

    mock_collection = MagicMock()
    mock_collection.get.return_value = {
        "ids": [doc_id],
        "metadatas": [{"filepath": "app/a.py", "start_line": 10, "end_line": 20}],
    }
    mock_chroma = MagicMock()
    mock_chroma.get_or_create_collection.return_value = mock_collection

    with patch("coresite.services.rag_service.get_chroma_client", return_value=mock_chroma):
        with patch("coresite.services.rag_service.embed_code_documents_batch") as mock_embed:
            count, model_used, diff_stats = index_project_chunks(42, chunks)
            assert count == 1
            assert diff_stats["cached"] == 1
            assert diff_stats["updated_lines"] == 1
            assert diff_stats["new"] == 0
            # 0 API calls
            mock_embed.assert_not_called()
            # Metadata updated with new lines
            mock_collection.update.assert_called_once()


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
