
from google import genai
from schemas import TaskCreateList, TaskCreate
from datetime import datetime, timezone
import zoneinfo



def text_to_tasks(
    text: str,
    user_timezone: str = "UTC",
    ast_outline: str = None,
    code_snippets: str = None,
) -> TaskCreate:  # pragma: no cover
    tz = zoneinfo.ZoneInfo(user_timezone)
    current_time_str = datetime.now(tz).strftime("%A, %Y-%m-%d %H:%M:%S %z")

    instructions = ""

    if ast_outline:
        instructions += (
            f"--- REPOSITORY OUTLINE ---\n{ast_outline}\n--------------------------\n\n"
        )

    if code_snippets:
        instructions += (
            f"--- RELEVANT CODE IMPLEMENTATION SNIPPETS ---\n{code_snippets}\n----------------------------------------------\n\n"
        )

    instructions += (
        f"You are a Senior Tech Lead generating detailed developer tickets.\n"
        f"The user is in the timezone: {user_timezone}.\n"
        f"CURRENT DATE & TIME: {current_time_str}\n\n"
        f"CRITICAL RULES:\n"
        f"1. AST & CODE MAPPING: Analyze the REPOSITORY OUTLINE and RELEVANT CODE IMPLEMENTATION SNIPPETS. Assign subtasks to specific existing files, functions, and classes matching real method signatures whenever possible.\n"
        f"2. You MUST write a technical summary in the `description` field.\n"
        f"3. You MUST populate the `subtasks` array with step-by-step instructions. DO NOT leave it empty.\n"
        f"4. Calculate deadlines from the CURRENT DATE and format strictly as an ISO 8601 string with the timezone offset (e.g., 'YYYY-MM-DDTHH:MM:SS+03:00').\n"
        f"5. You must output EXACTLY ONE task object. Do not wrap it in a list or a 'tasks' dictionary.\n\n"
        f"OUTPUT EXACTLY IN THIS JSON FORMAT:\n"
        """{
          "title": "Add register_bulk_routes method",
          "description": "Implement bulk route registration in src/flask/app.py.",
          "ticket_type": "feature",
          "due_date": "2026-08-18T17:00:00+03:00",
          "completed": false,
          "subtasks": [
            {"title": "Add method signature", "completed": false},
            {"title": "Write unit tests", "completed": false}
          ]
        }"""
    )

    print(instructions)
    client = genai.Client()
    interaction = client.interactions.create(
        model="gemini-3.6-flash",
        input=f"{instructions}\n\nUser Input: {text}",
        response_format= {
            "type": "text",
            "mime_type": "application/json",
        },
    )

    result = TaskCreate.model_validate_json(interaction.output_text)
    return result