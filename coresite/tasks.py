from typing import Any, Optional
from ast import parse
from celery import shared_task
from .models import Weather, Project
import requests
from .services.utils import get_weather_category
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from .services.github_parser import fetch_file_content, fetch_repo_tree
from .services.rag_chunker import chunk_with_bpe_guardrails, extract_ast_code_blocks
from .services.rag_service import index_project_chunks


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
            time=parse_datetime(current_time),
            weather_code = weathercode
        )
        print(f"Weather Created: {current_temp} {weather_desc} {parse_datetime(current_time)}")
    
    
    MAX_RECORDS: int = 1000
    

    old_records = Weather.objects.all()[MAX_RECORDS:].values_list('id', flat=True)
    
    if old_records:
        Weather.objects.filter(id__in=old_records).delete()




@shared_task
def index_project_codebase(project_id: int, github_token: str = None) -> str:
    """
    Asynchronously indexes a project's codebase:
    1. Downloads source files from GitHub.
    2. Parses AST functions/classes and applies BPE guardrails.
    3. Generates embeddings and stores in ChromaDB.
    4. Marks the Project record as indexed.
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        print(f"[RAG Indexing] ❌ Project ID {project_id} not found.")
        return f"Project {project_id} not found."

    token = github_token or project.github_token or None
    if not project.github_repo:
        print(f"[RAG Indexing] ⚠️ Project '{project.name}' has no github_repo configured.")
        return f"Project {project.name} has no github_repo configured."

    print(f"\n[RAG Indexing] 🚀 Starting codebase indexing for project '{project.name}' ({project.github_repo})...")
    
    code_files = fetch_repo_tree(project.github_repo, token)
    if not code_files:
        print(f"[RAG Indexing] ⚠️ No code files found in repository {project.github_repo}.")
        return f"No code files found in {project.github_repo}."

    print(f"[RAG Indexing] 📁 Found {len(code_files)} code files. Processing up to 30 files...")
    all_raw_blocks = []
    for filepath in code_files[:30]:
        code = fetch_file_content(project.github_repo, filepath, token)
        if not code:
            continue
        blocks = extract_ast_code_blocks(code, filepath)
        if blocks:
            print(f"[RAG Indexing]   ↳ Parsed {filepath}: {len(blocks)} AST blocks found.")
        all_raw_blocks.extend(blocks)

    if not all_raw_blocks:
        print(f"[RAG Indexing] ⚠️ No AST code blocks extracted for '{project.name}'.")
        return f"No AST code blocks extracted for {project.name}."

    print(f"[RAG Indexing] ✂️ Applying BPE token guardrails over {len(all_raw_blocks)} AST blocks...")
    final_chunks = chunk_with_bpe_guardrails(all_raw_blocks)
    if not final_chunks:
        print(f"[RAG Indexing] ⚠️ No valid chunks remaining after BPE guardrails for '{project.name}'.")
        return f"No valid chunks after guardrails for {project.name}."

    print(f"[RAG Indexing] 🧠 Generated {len(final_chunks)} semantic chunks. Embedding & storing in ChromaDB...")
    indexed_count, model_used = index_project_chunks(project.id, final_chunks)

    # Update project state in DB
    project.is_indexed = True
    project.last_indexed_at = timezone.now()
    project.collection_name = f"project_{project.id}"
    project.embedding_model = model_used
    project.save(update_fields=["is_indexed", "last_indexed_at", "collection_name", "embedding_model"])

    success_msg = f"Successfully indexed {indexed_count} chunks for '{project.name}' into ChromaDB using {model_used}."
    print(f"[RAG Indexing] ✅ {success_msg}\n")
    return success_msg

