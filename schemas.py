from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class SubTask(BaseModel):
    title: str = Field(description="The title of the subtask")
    completed: bool = Field(default=False, description="Whether the subtask is completed")


class TaskCreate(BaseModel):
    title: str = Field(description="A short, descriptive title for the ticket")
    description: str = Field(default="", description="MUST NOT BE EMPTY. Detailed technical instructions on how to implement the task, referencing specific files and functions from the AST.")
    ticket_type: Literal["bug", "feature", "chore"] = Field(default="feature", description="The type of ticket: bug, feature, or chore")
    due_date: Optional[datetime] = Field(default=None, description="The date and time when the task is due in ISO 8601 format with timezone offset. Leave null if no deadline.")
    completed: bool = Field(default=False, description="Whether the task is already completed")
    subtasks: List[SubTask] = Field(default_factory=list, description="MUST NOT BE EMPTY. A list of technical subtasks required to complete this ticket")


class TaskCreateList(BaseModel):
    tasks: List[TaskCreate] = Field(description="A list of tasks to create")

