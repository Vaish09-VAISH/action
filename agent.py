import os
import json
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq


# Load .env
env_path = Path(__file__).resolve().with_name(".env")

if not env_path.is_file():
    raise FileNotFoundError(f".env not found at {env_path}")

load_dotenv(dotenv_path=env_path, override=True)


MODEL = "openai/gpt-oss-120b"

client = Groq(
    api_key=os.environ["GROQ_API_KEY"]
)


# --------------------------------------------------
# STEP 3: The form we want from the AI
# --------------------------------------------------

REQUIRED_FIELDS = [
    "caller",
    "reason",
    "request",
    "urgency",
    "action_needed",
    "evidence_quotes",
]


# --------------------------------------------------
# STEP 3: AI instructions
# --------------------------------------------------

PROMPT = """
You are a careful hospital call-summary assistant.

Read the following call transcript and extract the important information.

Return ONLY valid JSON.

The JSON must contain exactly these fields:

{
   {
  "caller": "...",
  "reason": "...",
  "request": "...",
  "action call": "...",
  "action required": "...",
  "mood": [...]
}
}
Rules:

1. Do not invent information.
2. If the caller's name is not given, write "Unknown".
3. If the caller does not make a clear request, write "Unclear".
4. Keep the summary based only on the transcript.
5. evidence_quotes must contain exact words spoken by the PATIENT.
6. Do not paraphrase evidence_quotes.
7. If there is no useful patient quote, return an empty list.
8. Return JSON only.

TRANSCRIPT:
{transcript}
"""


# --------------------------------------------------
# STEP 3: extract()
# --------------------------------------------------

def extract(ai_response):
    """
    Convert the AI's response from JSON text
    into a Python dictionary.
    """

    data = json.loads(ai_response)

    # Check that all required fields exist
    for field in REQUIRED_FIELDS:
        if field not in data:
            raise ValueError(f"Missing required field: {field}")

    # Check evidence_quotes is a list
    if not isinstance(data["evidence_quotes"], list):
        raise ValueError("evidence_quotes must be a list")

    return data
def summarize(transcript):

    last_error = None

    for attempt in range(3):

        print(f"\nAI attempt {attempt + 1}/3")

        try:
            response = client.chat.completions.create(
                model=MODEL,
                temperature=0,
                messages=[
                    {
                        "role": "user",
                        "content": PROMPT.replace("{transcript}", transcript)
                    }
                ],
            )

            ai_response = response.choices[0].message.content.strip()
            print("\n--- RAW AI RESPONSE ---")
            print(ai_response)
            print("-----------------------\n")
            result = extract(ai_response)

            print("Valid response received.")

            return result

        except (ValueError, json.JSONDecodeError) as error:

            last_error = error

            print(f"Invalid AI response: {error}")

            if attempt < 2:
                print("Retrying...")

    raise RuntimeError(
        f"AI failed after 3 attempts. Last error: {last_error}"
    )
if __name__ == "__main__":

    call_file = Path("samples/01_scheduling_call.txt")

    transcript = call_file.read_text(encoding="utf-8")

    result = summarize(transcript)

    print(json.dumps(result, indent=2))