
from google import genai
from schemas import TaskCreateList, TaskCreate
from datetime import datetime, timezone
import zoneinfo

def get_weather_category(weather_code: int) -> str:
    """
    Maps an Open-Meteo (WMO) weather code to its official string description.
    
    Args:
        weather_code (int): The numeric weather code.
        
    Returns:
        str: A string describing the weather condition.
    """
    wmo_codes = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Drizzle: Light intensity",
        53: "Drizzle: Moderate intensity",
        55: "Drizzle: Dense intensity",
        56: "Freezing Drizzle: Light intensity",
        57: "Freezing Drizzle: Dense intensity",
        61: "Rain: Slight intensity",
        63: "Rain: Moderate intensity",
        65: "Rain: Heavy intensity",
        66: "Freezing Rain: Light intensity",
        67: "Freezing Rain: Heavy intensity",
        71: "Snow fall: Slight intensity",
        73: "Snow fall: Moderate intensity",
        75: "Snow fall: Heavy intensity",
        77: "Snow grains",
        80: "Rain showers: Slight",
        81: "Rain showers: Moderate",
        82: "Rain showers: Violent",
        85: "Snow showers: Slight",
        86: "Snow showers: Heavy",
        95: "Thunderstorm: Slight or moderate",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail"
    }
    
    # Return the description, or a default message if the code isn't recognized
    return wmo_codes.get(weather_code, "Unknown weather code")



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