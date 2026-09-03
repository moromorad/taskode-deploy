import os
import time
from typing import Any, Optional
from google import genai
from google.genai import types
import numpy as np

from coresite.models import CodeChunk

# Initialize Google GenAI client (reads GEMINI_API_KEY from environment)
genai_client = genai.Client()

PRIMARY_MODEL = "gemini-embedding-001"


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
# 2. Vector Storage & Indexing (PostgreSQL pgvector / SQLite)
# ==========================================

def index_project_chunks(
    project_id: int,
    chunks: list[dict[str, Any]],
    preferred_model: str = PRIMARY_MODEL,
    on_progress: Optional[Any] = None,
) -> tuple[int, str, dict[str, int]]:
    """
    Incrementally embeds and stores code chunks directly in the database:
    1. Identifies cached (unchanged), new/modified, and obsolete chunks.
    2. Re-embeds ONLY new or modified chunks.
    3. Updates line metadata for line shifts without re-embedding.
    4. Deletes obsolete chunks from the database.
    Returns (total_active_chunks, model_used, diff_stats).
    """
    diff_stats = {"cached": 0, "new": 0, "deleted": 0, "updated_lines": 0}
    if not chunks:
        return 0, preferred_model, diff_stats

    # 1. Fetch existing indexed chunks in this project
    existing_chunks = {
        c.chunk_id: c for c in CodeChunk.objects.filter(project_id=project_id)
    }

    # 2. Categorize incoming chunks
    chunks_to_embed = []
    current_chunk_ids = set()

    for chunk in chunks:
        content_hash = compute_chunk_content_hash(chunk["text"], chunk["filepath"])
        chunk_id = f"chunk_{project_id}_{content_hash}"
        current_chunk_ids.add(chunk_id)

        start_line = chunk.get("start_line", 0)
        end_line = chunk.get("end_line", 0)
        symbol_type = chunk.get("symbol_type", "")

        if chunk_id in existing_chunks:
            # Cache hit: unchanged code!
            diff_stats["cached"] += 1
            existing = existing_chunks[chunk_id]
            # If line numbers shifted, update metadata in DB without re-embedding
            if existing.start_line != start_line or existing.end_line != end_line:
                existing.start_line = start_line
                existing.end_line = end_line
                existing.save(update_fields=["start_line", "end_line"])
                diff_stats["updated_lines"] += 1
        else:
            # New or modified code -> needs embedding
            diff_stats["new"] += 1
            chunks_to_embed.append({
                "chunk_id": chunk_id,
                "text": chunk["text"],
                "filepath": chunk["filepath"],
                "symbol_type": symbol_type,
                "start_line": start_line,
                "end_line": end_line,
                "content_hash": content_hash,
            })

    # 3. Prune obsolete / deleted chunks
    obsolete_ids = set(existing_chunks.keys()) - current_chunk_ids
    if obsolete_ids:
        deleted_count, _ = CodeChunk.objects.filter(
            project_id=project_id, chunk_id__in=obsolete_ids
        ).delete()
        diff_stats["deleted"] = deleted_count
        print(f"[RAG VectorStore] 🗑️ Pruned {deleted_count} obsolete chunks for project {project_id}.")

    # 4. Embed ONLY new or modified chunks
    model_used = preferred_model
    if chunks_to_embed:
        print(f"[RAG VectorStore] ⚡ Diff: Embedding {len(chunks_to_embed)} new/modified chunks (Reusing {diff_stats['cached']} cached)...")
        vectors, model_used = embed_code_documents_batch(
            [{"filepath": c["filepath"], "text": c["text"]} for c in chunks_to_embed],
            batch_size=50,
            model=preferred_model,
            on_progress=on_progress,
        )
        new_records = [
            CodeChunk(
                project_id=project_id,
                chunk_id=c["chunk_id"],
                filepath=c["filepath"],
                text=c["text"],
                symbol_type=c["symbol_type"],
                start_line=c["start_line"],
                end_line=c["end_line"],
                content_hash=c["content_hash"],
                embedding=vectors[idx],
            )
            for idx, c in enumerate(chunks_to_embed)
        ]
        CodeChunk.objects.bulk_create(new_records)
    else:
        print(f"[RAG VectorStore] ⚡ All {len(chunks)} chunks are up to date! (0 new embeddings required).")
        if on_progress:
            on_progress(1, 1, model_used, f"⚡ All {len(chunks)} chunks cached. 0 new embeddings needed.")

    total_active_chunks = len(current_chunk_ids)
    return total_active_chunks, model_used, diff_stats


# ==========================================
# 3. Code Retrieval
# ==========================================

def retrieve_relevant_code_with_metadata(
    project_id: int, query_text: str, model: str = PRIMARY_MODEL, top_k: int = 4
) -> tuple[str, list[dict]]:
    """
    Performs cosine similarity search using pgvector (PostgreSQL) with fallback to NumPy (SQLite).
    Returns a tuple of:
    - concatenated prompt context string
    - list of structured chunk dicts containing filepath, lines, code, symbol_type, and distance.
    """
    chunk_qs = CodeChunk.objects.filter(project_id=project_id)
    if not chunk_qs.exists():
        print(f"[RAG Retrieval] ⚠️ No chunks found for project {project_id}.")
        return "", []

    query_vector = embed_code_query(query_text, model=model)

    from django.db import connection
    if connection.vendor == "postgresql":
        from pgvector.django import CosineDistance
        matched_chunks = list(
            chunk_qs.annotate(distance=CosineDistance("embedding", query_vector))
            .order_by("distance")[:top_k]
        )
    else:
        # SQLite / in-memory fallback with NumPy
        chunks = list(chunk_qs)
        q_vec = np.array(query_vector, dtype=float)
        q_norm = np.linalg.norm(q_vec)
        for c in chunks:
            c_vec = np.array(c.embedding, dtype=float)
            c_norm = np.linalg.norm(c_vec)
            if c_norm == 0 or q_norm == 0:
                c.distance = 1.0
            else:
                sim = np.dot(c_vec, q_vec) / (c_norm * q_norm)
                c.distance = float(1.0 - sim)
        matched_chunks = sorted(chunks, key=lambda x: getattr(x, "distance", 1.0))[:top_k]

    snippets = []
    chunk_list = []

    for c in matched_chunks:
        line_info = f" (Lines {c.start_line}-{c.end_line})" if c.start_line and c.end_line else ""
        snippets.append(
            f"--- Code Snippet from {c.filepath}{line_info} ---\n"
            f"{c.text}\n"
        )
        dist_val = getattr(c, "distance", None)
        chunk_list.append({
            "filepath": c.filepath,
            "start_line": c.start_line,
            "end_line": c.end_line,
            "symbol_type": c.symbol_type,
            "distance": round(float(dist_val), 4) if dist_val is not None else None,
            "code": c.text,
        })

    print(f"[RAG Retrieval] 🎯 Retrieved {len(snippets)} relevant code snippets from database.")
    return "\n\n".join(snippets), chunk_list


def retrieve_relevant_code(project_id: int, query_text: str, model: str = PRIMARY_MODEL, top_k: int = 4) -> str:
    """Convenience wrapper returning concatenated prompt context string."""
    formatted_text, _ = retrieve_relevant_code_with_metadata(project_id, query_text, model=model, top_k=top_k)
    return formatted_text

