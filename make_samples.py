import os
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq

# --- load .env the same way your test_keys.py does ---
env_path = Path(__file__).resolve().with_name(".env")
if not env_path.is_file():
    raise FileNotFoundError(f".env not found at {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

MODEL = MODEL = "openai/gpt-oss-120b"
client = Groq(api_key=os.environ["GROQ_API_KEY"])

SCENARIOS = {
    "01_scheduling":
        "An elderly patient calls to move next week's appointment to a later date "
        "because their son can no longer drive them. The agent offers two new slots "
        "and the patient picks one.",

    "02_billing_question":
        "A patient received a bill they do not understand and calmly asks for an "
        "itemised copy. They are polite and not upset.",

    "03_billing_complaint":
        "A patient is angry because they were charged twice for the same visit and "
        "demands a refund. Same topic as the bill question, but clearly a complaint.",

    "04_refill_insurance":
        "A patient asks for a prescription refill and mentions their insurance "
        "changed last month, so they are unsure whether it will still be covered.",

    "05_vague":
        "A patient mentions their insurer denied a claim but never says what they "
        "want done about it. Deliberately unclear - there is no clear request.",
}

PROMPT = """Write a realistic phone call transcript between a PATIENT and a hospital FRONT-DESK agent.
Cover this situation: {scenario}

Rules:
- 12 to 20 turns total, alternating speakers
- Prefix every line with a timestamp and the speaker, like this:
  [00:12] AGENT: ...
  [00:19] PATIENT: ...
- Use natural spoken English. Include small talk, one interruption and some repetition.
- Use believable but obviously fake names, dates, amounts and reference numbers.
- Output ONLY the timestamped dialogue lines. No summary, no headings, no notes."""

samples = Path(__file__).resolve().parent / "samples"
samples.mkdir(exist_ok=True)

for name, scenario in SCENARIOS.items():
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0.7,
        messages=[{"role": "user", "content": PROMPT.format(scenario=scenario)}],
    )
    text = resp.choices[0].message.content.strip()

    (samples / f"{name}_call.txt").write_text(text + "\n", encoding="utf-8")
    (samples / f"{name}_ref.txt").write_text(
        "caller: \nreason: \nrequest: \nurgency: \naction needed: \n",
        encoding="utf-8",
    )
    print(f"wrote samples/{name}_call.txt  +  samples/{name}_ref.txt")

print("\nDone. 5 calls + 5 blank answer sheets in samples/")
