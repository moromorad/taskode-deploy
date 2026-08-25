import os
import time
from typing import Any, Optional
from google import genai
from google.genai import types
import numpy as np
import chromadb

# Initialize Google GenAI client (reads GEMINI_API_KEY from environment)
genai_client = genai.Client()

CHROMA_HOST = os.getenv("CHROMA_HOST", "chromadb")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))

PRIMARY_MODEL = "gemini-embedding-001"


def get_chroma_client():
    """
    Returns an HTTP client for ChromaDB container with fallback to persistent on-disk client.
    """
    try:
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        client.heartbeat()
        return client
    except Exception:
        return chromadb.PersistentClient(path="./chroma_data")


def normalize_vector(vec: list[float]) -> list[float]:
    """Applies L2 normalization to a vector."""
    arr = np.array(vec, dtype=float)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return vec
    return (arr / norm).tolist()


# ==========================================
# 1. Embedding Generation (gemini-embedding-001)
# ==========================================

def embed_code_documents_batch(
    chunks: list[dict[str, Any]],
    batch_size: int = 50,
    model: str = PRIMARY_MODEL,
    output_dim: int = 768,
    on_progress: Optional[Any] = None,
) -> tuple[list[list[float]], str]:
    """
    Embeds code chunk dictionaries in batches of up to `batch_size` (e.g. 50 chunks per API call).
    Uses gemini-embedding-001 native multi-document batching to drastically conserve RPM quotas.
    Raises exception immediately if embedding fails (no fallback).
    Returns (list_of_vectors, model_used).
    """
    if not chunks:
        return [], model

    all_vectors: list[list[float]] = []
    total_batches = (len(chunks) + batch_size - 1) // batch_size

    for batch_num, i in enumerate(range(0, len(chunks), batch_size), 1):
        batch = chunks[i : i + batch_size]
        log_msg = f"[RAG Embed Batch] Processing batch {batch_num}/{total_batches} ({len(batch)} chunks) using {model}..."
        print(log_msg)
        if on_progress:
            on_progress(batch_num, total_batches, model, log_msg)

        try:
            raw_contents = [c.get("text", "") for c in batch]
            result = genai_client.models.embed_content(
                model=model,
                contents=raw_contents,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=output_dim,
                ),
            )
            embeddings = getattr(result, "embeddings", None)
            if not embeddings:
                # In case single object is returned
                emb = getattr(result, "embedding", None)
                embeddings = [emb] if emb else []

            batch_vectors = [
                normalize_vector(emb.values if hasattr(emb, "values") else emb)
                for emb in embeddings
            ]
            all_vectors.extend(batch_vectors)

            # Rate pacing: small 0.5s pause between multi-batch requests
            if batch_num < total_batches:
                time.sleep(0.5)

        except Exception as e:
            err_msg = f"[RAG Embed Batch] ❌ Model {model} failed during batch {batch_num}/{total_batches}: {e}"
            print(err_msg)
            if on_progress:
                on_progress(batch_num, total_batches, model, err_msg)
            raise e

    return all_vectors, model


def embed_code_document(filepath: str, code_chunk: str, model: str = PRIMARY_MODEL, output_dim: int = 768) -> tuple[list[float], str]:
    """
    Convenience helper for embedding a single code chunk.
    Returns (embedding_vector, model_used).
    """
    vectors, model_used = embed_code_documents_batch(
        [{"filepath": filepath, "text": code_chunk}],
        batch_size=1,
        model=model,
        output_dim=output_dim,
    )
    return vectors[0], model_used


def embed_code_query(query: str, model: str = PRIMARY_MODEL, output_dim: int = 768) -> list[float]:
    """
    Generates embedding for a user prompt query using gemini-embedding-001.
    """
    print(f"[RAG Query] 🔍 Embedding user prompt using {model} (Query: '{query[:60]}...')...")
    result = genai_client.models.embed_content(
        model=model,
        contents=query,
        config=types.EmbedContentConfig(
            task_type="CODE_RETRIEVAL_QUERY",
            output_dimensionality=output_dim,
        ),
    )
    embeddings = getattr(result, "embeddings", None)
    if embeddings and len(embeddings) > 0:
        raw_vec = embeddings[0].values if hasattr(embeddings[0], "values") else embeddings[0]
    elif getattr(result, "embedding", None):
        raw_vec = result.embedding.values if hasattr(result.embedding, "values") else result.embedding
    else:
        raise ValueError(f"No embeddings returned for query by {model}")

    return normalize_vector(raw_vec)


import hashlib


def compute_chunk_content_hash(text: str, filepath: str) -> str:
    """
    Computes a deterministic SHA-256 hash of a chunk's code and structural context.
    Strips volatile 'Lines: ...' lines to ensure line-number shifts in other parts
    of the file do not invalidate unchanged embeddings.
    """
    cleaned_lines = [l for l in text.splitlines() if not l.strip().startswith("Lines: ")]
    canonical_text = f"{filepath}::" + "\n".join(cleaned_lines)
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()[:16]


# ==========================================
# 2. Vector Storage & Indexing
# ==========================================

def index_project_chunks(
    project_id: int,
    chunks: list[dict[str, Any]],
    preferred_model: str = PRIMARY_MODEL,
    on_progress: Optional[Any] = None,
) -> tuple[int, str, dict[str, int]]:
    """
    Incrementally embeds and stores code chunks in ChromaDB:
    1. Reuses existing collection if present.
    2. Identifies cached (unchanged), new/modified, and obsolete chunks.
    3. Re-embeds ONLY new or modified chunks.
    4. Updates line metadata for line shifts without re-embedding.
    5. Deletes obsolete chunks from ChromaDB.
    Returns (total_active_chunks, model_used, diff_stats).
    """
    diff_stats = {"cached": 0, "new": 0, "deleted": 0, "updated_lines": 0}
    if not chunks:
        return 0, preferred_model, diff_stats

    chroma = get_chroma_client()
    collection_name = f"project_{project_id}"

    collection = chroma.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    # 1. Fetch existing indexed chunks in this collection
    existing_ids = set()
    existing_metas = {}
    try:
        existing_data = collection.get(include=["metadatas"])
        if existing_data and "ids" in existing_data:
            existing_ids = set(existing_data["ids"])
            for id_, meta in zip(existing_data["ids"], existing_data.get("metadatas", [])):
                existing_metas[id_] = meta or {}
    except Exception as e:
        print(f"[RAG Chroma] Note: could not fetch existing metadata for {collection_name}: {e}")

    # 2. Categorize incoming chunks
    chunks_to_embed = []
    current_doc_ids = set()

    for chunk in chunks:
        content_hash = compute_chunk_content_hash(chunk["text"], chunk["filepath"])
        doc_id = f"chunk_{project_id}_{content_hash}"
        current_doc_ids.add(doc_id)

        meta = {
            "filepath": chunk["filepath"],
            "symbol_type": chunk.get("symbol_type", ""),
            "start_line": chunk.get("start_line", 0),
            "end_line": chunk.get("end_line", 0),
            "content_hash": content_hash,
        }

        if doc_id in existing_ids:
            # Cache hit: unchanged code!
            diff_stats["cached"] += 1
            old_meta = existing_metas.get(doc_id, {})
            # If line numbers shifted, update metadata in ChromaDB without re-embedding
            if (old_meta.get("start_line") != meta["start_line"] or 
                old_meta.get("end_line") != meta["end_line"]):
                try:
                    collection.update(ids=[doc_id], metadatas=[meta])
                    diff_stats["updated_lines"] += 1
                except Exception:
                    pass
        else:
            # New or modified code -> needs embedding
            diff_stats["new"] += 1
            chunks_to_embed.append({
                "id": doc_id,
                "text": chunk["text"],
                "filepath": chunk["filepath"],
                "metadata": meta,
            })

    # 3. Prune obsolete / deleted chunks
    obsolete_ids = list(existing_ids - current_doc_ids)
    if obsolete_ids:
        try:
            collection.delete(ids=obsolete_ids)
            diff_stats["deleted"] = len(obsolete_ids)
            print(f"[RAG Chroma] 🗑️ Pruned {len(obsolete_ids)} obsolete chunks from '{collection_name}'.")
        except Exception as e:
            print(f"[RAG Chroma] ⚠️ Failed to prune obsolete chunks: {e}")

    # 4. Embed ONLY new or modified chunks
    model_used = preferred_model
    if chunks_to_embed:
        print(f"[RAG Chroma] ⚡ Diff: Embedding {len(chunks_to_embed)} new/modified chunks (Reusing {diff_stats['cached']} cached)...")
        vectors, model_used = embed_code_documents_batch(
            [{"filepath": c["filepath"], "text": c["text"]} for c in chunks_to_embed],
            batch_size=50,
            model=preferred_model,
            on_progress=on_progress,
        )
        collection.upsert(
            ids=[c["id"] for c in chunks_to_embed],
            documents=[c["text"] for c in chunks_to_embed],
            embeddings=vectors,
            metadatas=[c["metadata"] for c in chunks_to_embed],
        )
    else:
        print(f"[RAG Chroma] ⚡ All {len(chunks)} chunks are up to date! (0 new embeddings required).")
        if on_progress:
            on_progress(1, 1, model_used, f"⚡ All {len(chunks)} chunks cached. 0 new embeddings needed.")

    total_active_chunks = len(current_doc_ids)
    return total_active_chunks, model_used, diff_stats


# ==========================================
# 3. Code Retrieval
# ==========================================

def retrieve_relevant_code(project_id: int, query_text: str, model: str = PRIMARY_MODEL, top_k: int = 4) -> str:
    """
    Performs cosine similarity search in ChromaDB using the project's embedding model.
    """
    chroma = get_chroma_client()
    collection_name = f"project_{project_id}"

    try:
        collection = chroma.get_collection(name=collection_name)
    except Exception:
        print(f"[RAG Retrieval] ⚠️ Collection '{collection_name}' not found in ChromaDB.")
        return ""

    query_vector = embed_code_query(query_text, model=model)

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=top_k,
    )

    if not results or not results.get("documents") or not results["documents"][0]:
        print(f"[RAG Retrieval] ⚠️ No matching code snippets found in '{collection_name}'.")
        return ""

    snippets = []
    for doc, metadata in zip(results["documents"][0], results["metadatas"][0]):
        filepath = metadata.get("filepath", "unknown")
        start_line = metadata.get("start_line", "")
        end_line = metadata.get("end_line", "")
        line_info = f" (Lines {start_line}-{end_line})" if start_line and end_line else ""

        snippets.append(
            f"--- Code Snippet from {filepath}{line_info} ---\n"
            f"{doc}\n"
        )

    print(f"[RAG Retrieval] 🎯 Retrieved {len(snippets)} relevant code snippets from ChromaDB.")
    return "\n\n".join(snippets)
