from datetime import datetime
from fastapi import FastAPI, HTTPException, status
import os
from pydantic import BaseModel, ConfigDict
from schemas import TaskCreate, TaskResponse


# 1. Setup Django environment before importing Django's ASGI app
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

# 2. Get Django's ASGI application
from django.core.asgi import get_asgi_application
django_app = get_asgi_application()

from coresite.models import Task, Weather

# 3. Initialize FastAPI
app = FastAPI(
    title="TasKode API",
    description="FastAPI high-performance ASGI endpoints for TasKode, routing everything else to Django.",
)


# CREATE TASK
@app.post("/fast/tasks/")
def add_task(task: TaskCreate) -> TaskResponse:
    task_data = task.model_dump()
    new_task = Task.objects.create(**task_data)
    return new_task 

# READ TASK
@app.get("/fast/tasks/{task_id}")
def get_task(task_id: int) -> TaskResponse:
    try:
        task = Task.objects.get(id=task_id)      
        return task
    except Task.DoesNotExist:
        raise HTTPException(status_code=404, detail="Task not found")
    
#READ ALL TASKS
@app.get("/fast/tasks")
def get_all_tasks() -> list[TaskResponse]:
    tasks = Task.objects.all()
    return tasks

    
# UPDATE TASK
@app.put("/fast/tasks/{task_id}")
def update_task(task: TaskCreate, task_id:int) -> TaskResponse:
    try:
        old_task = Task.objects.get(id=task_id)
        old_task.title = task.title
        old_task.completed = task.completed
        if task.due_date:
            old_task.due_date = task.due_date
        old_task.save()
        return old_task
    except Task.DoesNotExist:
          raise HTTPException(status_code=404, detail="Task not found")
    
# DELETE TASK
@app.delete("/fast/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id:int):
    try:
        task = Task.objects.get(id=task_id)
        task.delete()
    except Task.DoesNotExist:
        raise HTTPException(status_code=404, detail="Task not found")



# 5. Mount Django on the root path
# This means any request that doesn't match a FastAPI route above will be sent to Django.
app.mount("/", django_app)
