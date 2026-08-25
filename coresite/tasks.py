from typing import Any, Optional
from ast import parse
from celery import shared_task
from django.core.cache import cache
from .models import Weather, Project
import requests
from .services.utils import get_weather_category
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from .services.github_parser import fetch_file_content, fetch_repo_tree
from .services.rag_chunker import chunk_with_bpe_guardrails, extract_ast_code_blocks
from .services.rag_service import index_project_chunks

MAX_RECORDS: int = 1000
_IN_MEMORY_RAG_STATUS: dict[int, dict] = {}


@shared_task
def fetch_weather_and_cleanup() -> None:
    url: str = "https://api.open-meteo.com/v1/forecast?latitude=30.0626&longitude=31.2497&current=temperature_2m,weather_code&timezone=Africa%2FCairo"
    response: dict[str, Any] = requests.get(url).json()
    
    current_weather: dict[str, Any] = response.get('current', {})
    current_temp: Optional[float] = current_weather.get('temperature_2m')
    weathercode: Optional[int] = current_weather.get('weather_code')
    current_time: Optional[str] = current_weather.get('time')
    
    
    weather_desc: str = get_weather_category(weathercode)
    
   
    if current_temp is not None:
        Weather.objects.create(
            temp=current_temp,
            weather=weather_desc,
            time=parse_datetime(current_time) if current_time else timezone.now(),
            weather_code = weathercode
        )
        print(f"Weather Created: {current_temp} {weather_desc} {parse_datetime(current_time)}")
    
    
    MAX_RECORDS: int = 1000
    

    old_records = Weather.objects.all()[MAX_RECORDS:].values_list('id', flat=True)
    
    if old_records:
        Weather.objects.filter(id__in=old_records).delete()


def update_project_progress(
    project_id: int,
    progress: int,
    stage: str,
    current_step: str,
    new_log: Optional[str] = None,
    model: Optional[str] = None,
    chunk_count: int = 0,
):
    """Stores progress state and rolling console logs in Redis cache with a 10m TTL."""
    cache_key = f"project_rag_status:{project_id}"
    data = None
    try:
        data = cache.get(cache_key)
    except Exception:
        pass

    if not data:
        data = _IN_MEMORY_RAG_STATUS.get(project_id) or {
            "status": "indexing",
            "progress": 0,
            "stage": stage,
            "current_step": current_step,
            "logs": [],
            "model": model,
            "chunk_count": chunk_count,
            "last_updated": None,
        }
    
    data["progress"] = progress
    data["stage"] = stage
    data["current_step"] = current_step
    data["status"] = "completed" if stage == "completed" else ("failed" if stage == "failed" else "indexing")
    data["last_updated"] = timezone.now().isoformat()
    if model:
        data["model"] = model
    if chunk_count:
        data["chunk_count"] = chunk_count
    if new_log:
        data["logs"].append(new_log)
        data["logs"] = data["logs"][-50:]  # Keep last 50 entries
    
    _IN_MEMORY_RAG_STATUS[project_id] = data
    try:
        cache.set(cache_key, data, timeout=600)
    except Exception:
        pass


@shared_task
def index_project_codebase(project_id: int, github_token: str = None) -> str:
    """
    Asynchronously indexes a project's codebase:
    1. Downloads source files from GitHub.
    2. Parses AST functions/classes and applies BPE guardrails.
    3. Generates embeddings in batches and stores in ChromaDB.
    4. Streams real-time progress and logs to Redis cache.
    5. Marks the Project record as indexed.
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        err_msg = f"[RAG Indexing] ❌ Project ID {project_id} not found."
        print(err_msg)
        update_project_progress(project_id, 0, "failed", "Project not found", err_msg)
        return f"Project {project_id} not found."

    token = github_token or project.github_token or None
    if not project.github_repo:
        warn_msg = f"[RAG Indexing] ⚠️ Project '{project.name}' has no github_repo configured."
        print(warn_msg)
        update_project_progress(project_id, 0, "failed", "No GitHub repo configured", warn_msg)
        return f"Project {project.name} has no github_repo configured."

    start_log = f"[RAG Indexing] 🚀 Starting codebase indexing for project '{project.name}' ({project.github_repo})..."
    print(f"\n{start_log}")
    update_project_progress(project.id, 5, "discovering", "Connecting to GitHub repository...", start_log)

    code_files = fetch_repo_tree(project.github_repo, token)
    if not code_files:
        empty_log = f"[RAG Indexing] ⚠️ No code files found in repository {project.github_repo}."
        print(empty_log)
        update_project_progress(project.id, 0, "failed", "No code files found", empty_log)
        return f"No code files found in {project.github_repo}."

    files_log = f"[RAG Indexing] 📁 Found {len(code_files)} code files. Processing up to 30 files..."
    print(files_log)
    update_project_progress(project.id, 15, "ast_parsing", f"Found {len(code_files)} files. Extracting AST...", files_log)

    all_raw_blocks = []
    target_files = code_files[:30]
    for idx, filepath in enumerate(target_files, 1):
        code = fetch_file_content(project.github_repo, filepath, token)
        if not code:
            continue
        blocks = extract_ast_code_blocks(code, filepath)
        if blocks:
            parse_log = f"[RAG Indexing]   ↳ Parsed {filepath}: {len(blocks)} AST blocks found."
            print(parse_log)
            pct = 15 + int((idx / len(target_files)) * 30)  # 15% -> 45%
            update_project_progress(project.id, pct, "ast_parsing", f"Parsed {filepath} ({idx}/{len(target_files)})", parse_log)
        all_raw_blocks.extend(blocks)

    if not all_raw_blocks:
        no_ast_log = f"[RAG Indexing] ⚠️ No AST code blocks extracted for '{project.name}'."
        print(no_ast_log)
        update_project_progress(project.id, 0, "failed", "No AST blocks found", no_ast_log)
        return f"No AST code blocks extracted for {project.name}."

    guard_log = f"[RAG Indexing] ✂️ Applying BPE token guardrails over {len(all_raw_blocks)} AST blocks..."
    print(guard_log)
    update_project_progress(project.id, 48, "guardrails", "Applying BPE token guardrails...", guard_log)

    final_chunks = chunk_with_bpe_guardrails(all_raw_blocks)
    if not final_chunks:
        no_chunk_log = f"[RAG Indexing] ⚠️ No valid chunks remaining after BPE guardrails for '{project.name}'."
        print(no_chunk_log)
        update_project_progress(project.id, 0, "failed", "No valid chunks remaining", no_chunk_log)
        return f"No valid chunks after guardrails for {project.name}."

    chunks_log = f"[RAG Indexing] 🧠 Generated {len(final_chunks)} semantic chunks. Embedding & storing in ChromaDB..."
    print(chunks_log)
    update_project_progress(project.id, 50, "batch_embedding", f"Embedding {len(final_chunks)} chunks...", chunks_log, chunk_count=len(final_chunks))

    def on_batch_progress(chunk_num: int, total_chunks: int, model: str, log_msg: str):
        if total_chunks > 0:
            chunk_pct = 50 + int((chunk_num / total_chunks) * 40)  # 50% -> 90%
            step_text = f"Embedding chunks ({chunk_num}/{total_chunks}) using {model}..."
            update_project_progress(project.id, chunk_pct, "batch_embedding", step_text, log_msg, model=model)

    indexed_count, model_used = index_project_chunks(
        project.id,
        final_chunks,
        on_progress=on_batch_progress,
    )

    # Update project state in DB
    project.is_indexed = True
    project.last_indexed_at = timezone.now()
    project.collection_name = f"project_{project.id}"
    project.embedding_model = model_used
    project.save(update_fields=["is_indexed", "last_indexed_at", "collection_name", "embedding_model"])

    success_msg = f"Successfully indexed {indexed_count} chunks for '{project.name}' into ChromaDB using {model_used}."
    success_log = f"[RAG Indexing] ✅ {success_msg}\n"
    print(success_log)
    update_project_progress(
        project.id,
        100,
        "completed",
        "Indexing complete and ready for AI generation!",
        success_log,
        model=model_used,
        chunk_count=indexed_count,
    )
    return success_msg
