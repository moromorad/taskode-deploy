import os
from typing import Any, Optional
from google import genai
from google.genai import types
import numpy as np
import chromadb

# Initialize Google GenAI client (reads GEMINI_API_KEY from environment)
genai_client = genai.Client()

CHROMA_HOST = os.getenv("CHROMA_HOST", "chromadb")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))

PRIMARY_MODEL = "gemini-embedding-2"
FALLBACK_MODEL = "gemini-embedding-001"


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
# 1. Embedding Generation with Fallback
# ==========================================

def embed_code_document(filepath: str, code_chunk: str, model: str = PRIMARY_MODEL, output_dim: int = 768) -> tuple[list[float], str]:
    """
    Generates embedding for a code chunk using the specified model.
    Falls back to FALLBACK_MODEL if PRIMARY_MODEL encounters an exception.
    Returns (embedding_vector, model_used).
    """
    models_to_try = [model] if model != PRIMARY_MODEL else [PRIMARY_MODEL, FALLBACK_MODEL]

    for current_model in models_to_try:
        try:
            if current_model == "gemini-embedding-2":
                formatted_doc = f"title: {filepath} | text: {code_chunk}"
                result = genai_client.models.embed_content(
                    model=current_model,
                    contents=formatted_doc,
                    config=types.EmbedContentConfig(output_dimensionality=output_dim),
                )
                return result.embeddings[0].values, current_model
            else:
                # gemini-embedding-001
                result = genai_client.models.embed_content(
                    model=current_model,
                    contents=code_chunk,
                    config=types.EmbedContentConfig(
                        task_type="RETRIEVAL_DOCUMENT",
                        output_dimensionality=output_dim,
                    ),
                )
                vector = result.embeddings[0].values
                return normalize_vector(vector), current_model
        except Exception as e:
            print(f"[RAG Embed] ⚠️ Model {current_model} failed ({e}).")
            if current_model == models_to_try[-1]:
                raise e
            print(f"[RAG Embed] 🔄 Falling back to {FALLBACK_MODEL}...")
            continue

    raise RuntimeError("Failed to generate embedding with any model")


def embed_code_query(query: str, model: str = PRIMARY_MODEL, output_dim: int = 768) -> list[float]:
    """
    Generates embedding for a query using the exact model that indexed the project.
    """
    print(f"[RAG Query] 🔍 Embedding user prompt using {model} (Query: '{query[:60]}...')...")
    if model == "gemini-embedding-2":
        formatted_query = f"task: code retrieval | query: {query}"
        result = genai_client.models.embed_content(
            model=model,
            contents=formatted_query,
            config=types.EmbedContentConfig(output_dimensionality=output_dim),
        )
        return result.embeddings[0].values
    else:
        # gemini-embedding-001
        result = genai_client.models.embed_content(
            model=model,
            contents=query,
            config=types.EmbedContentConfig(
                task_type="CODE_RETRIEVAL_QUERY",
                output_dimensionality=output_dim,
            ),
        )
        vector = result.embeddings[0].values
        return normalize_vector(vector)


# ==========================================
# 2. Vector Storage & Indexing
# ==========================================

def index_project_chunks(project_id: int, chunks: list[dict[str, Any]], preferred_model: str = PRIMARY_MODEL) -> tuple[int, str]:
    """
    Embeds and stores code chunks in ChromaDB.
    Returns (chunk_count, model_used).
    """
    if not chunks:
        return 0, preferred_model

    chroma = get_chroma_client()
    collection_name = f"project_{project_id}"

    try:
        chroma.delete_collection(name=collection_name)
    except Exception:
        pass

    collection = chroma.create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    ids: list[str] = []
    documents: list[str] = []
    embeddings: list[list[float]] = []
    metadatas: list[dict[str, Any]] = []
    model_used = preferred_model

    print(f"[RAG Chroma] Storing {len(chunks)} chunks in collection '{collection_name}'...")
    for idx, chunk in enumerate(chunks):
        chunk_id = f"chunk_{project_id}_{idx}"
        vector, model_used = embed_code_document(chunk["filepath"], chunk["text"], model=model_used)

        ids.append(chunk_id)
        documents.append(chunk["text"])
        embeddings.append(vector)
        metadatas.append({
            "filepath": chunk["filepath"],
            "symbol_type": chunk.get("symbol_type", ""),
            "start_line": chunk.get("start_line", 0),
            "end_line": chunk.get("end_line", 0),
        })

    collection.add(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    print(f"[RAG Chroma] ✅ Successfully indexed {len(chunks)} chunks in '{collection_name}'.")
    return len(chunks), model_used


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
