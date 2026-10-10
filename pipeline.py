import json, logging, re, time
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(__file__).resolve().with_name(".env")
if not env_path.is_file():
    raise SystemExit(f".env not found at {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

from groq import Groq, APIStatusError, APIConnectionError, APITimeoutError

# ----------------------------------------------------------------- config
MODEL = "openai/gpt-oss-120b"    # better quality
# MODEL = "openai/gpt-oss-20b"   # faster + cheaper; same schema support
REASONING_EFFORT = "low"         # gpt-oss only - keeps the demo quick and cheap
MAX_TOKENS = 2000                # hard cap on cost per call

# The card, as a real schema. Strict mode enforces this - no prose needed.
CALL_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "caller": {"type": "string"},
        "reason": {"type": "string"},
        "request": {"type": "string"},
        "call_type": {"type": "string", "enum": [
            "appointment", "refill", "billing", "complaint",
            "lab_results", "clinical_question", "other"]},
        "sentiment": {"type": "string",
                      "enum": ["calm", "frustrated", "angry"]},
        "urgency": {"type": "string",
                    "enum": ["routine", "soon", "urgent"]},
        "emergency_flag": {"type": "boolean"},
        "summary": {"type": "string"},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_needed": {"type": "string"},
                    "action_required": {"type": "string"},
                    "owner_team": {"type": "string", "enum": [
                        "scheduling", "pharmacy", "billing",
                        "complaints", "nursing", "human_review"]},
                    "quote": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "due_hint": {"type": "string"},
                },
                "required": ["action_needed", "action_required", "owner_team",
                             "quote", "timestamp", "due_hint"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["caller", "reason", "request", "call_type", "sentiment",
                 "urgency", "emergency_flag", "summary", "action_items"],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """You extract structured facts from healthcare phone calls.

The transcript has one turn per line, formatted like:
[00:36] AGENT: some words

FIELD DEFINITIONS:
- caller: who was actually on the line (patient, patient's son, insurer...).
  NOT a department.
- reason: why they called, one line.
- request: what they actually asked for. If they never asked for anything,
  the literal word "none".
- action_needed: the patient-facing outcome - what the caller must be told
  or given.
- action_required: the internal step the hospital must take to get there.
- owner_team: the team that owns that internal step.

RULES - follow every one:
1. Extract ONLY what is explicitly said. Never add what you assume.
2. Every action item MUST include supporting words in "quote". Copy them
   character for character from the transcript. Do not fix grammar, do not
   paraphrase. If a sentence is split across two transcript lines, you may
   join it - the words must still be continuous as spoken.
3. NEVER invent a due date. Put a date in "due_hint" ONLY if one was
   actually spoken. Otherwise use an empty string "".
4. If the caller never asked for anything, return "action_items": [],
   set "request" to "none", and set "action_needed" nowhere else.
   An empty list is a CORRECT answer. Never invent a request to fill it.
5. Never leave a required string empty. If a field genuinely has no value,
   write the word "none" in it - not "".
6. Set "emergency_flag": true for chest pain, cannot breathe, heavy
   bleeding, stroke, unconscious, seizure, suicidal, overdose, or any
   request for immediate help.
7. If two teams could own an item, choose the more specific one.
8. "urgency" is one of routine / soon / urgent ONLY - never "yes" or a
   description.
9. The summary is 2-3 sentences. No preamble like "The caller called to"."""


TEAM_OF = {
    "appointment":       "scheduling",
    "refill":            "pharmacy",
    "billing":           "billing",
    "complaint":         "complaints",
    "lab_results":       "nursing",
    "clinical_question": "nursing",
    "other":             "human_review",
}

# Written in NORMALISED form (lowercase, no apostrophes) - see _normalise().
# "can't breathe" becomes "can t breathe", so both spellings are listed.
EMERGENCY_WORDS = (
    "chest pain", "can t breathe", "cannot breathe", "short of breath",
    "trouble breathing", "heart attack", "bleeding", "stroke",
    "unconscious", "collapsed", "seizure", "suicidal", "overdose",
    "ambulance",
)

MIN_QUOTE_WORDS = 3        # shorter than this proves nothing

# ------------------------------------------------------------------ logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    handlers=[logging.FileHandler("run.log", encoding="utf-8"),
              logging.StreamHandler()],
)
log = logging.getLogger(__name__)

FAILED = []        # everything that went to a human instead of auto-routing


class Reason(str, Enum):
    BAD_FILE        = "file missing, empty or too short"
    API_DOWN        = "api unusable after retries"
    BAD_SHAPE       = "model returned an unusable shape"
    QUOTE_NOT_FOUND = "quote not found in the transcript"
    QUOTE_TOO_SHORT = "quote too short to be evidence"
    NO_REQUEST      = "caller never asked for anything"
    EMERGENCY       = "emergency - a human now"


# ---------------------------------------------------------------- text prep
_PREFIX = re.compile(
    r"\[\d{1,2}:\d{2}\]\s*(?:AGENT|PATIENT|SPEAKER\s*\d+|UNKNOWN)\s*:\s*",
    re.IGNORECASE,
)
_NONWORD = re.compile(r"[^a-z0-9\s]+")


def _normalise(text: str) -> str:
    """Lowercase, drop the [00:36] SPEAKER: prefixes, drop punctuation,
    collapse whitespace. Makes quotes comparable across line breaks."""
    text = _PREFIX.sub(" ", text)
    text = _NONWORD.sub(" ", text.lower())
    return " ".join(text.split())


# ------------------------------------------------------- 1. load the file
def load_transcript(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"no transcript at {p}")
    text = p.read_text(encoding="utf-8").strip()
    if len(text) < 50:
        raise ValueError(f"{p} is empty or far too short ({len(text)} chars)")
    return text


def _parse_loose(content: str) -> dict:
    """Strict mode should give clean JSON, but strip fences if they appear."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    return json.loads(text)


def call_model(transcript: str) -> dict:
    client, last = Groq(), None
    for attempt in (1, 2, 3):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=0,
                max_completion_tokens=MAX_TOKENS,
                reasoning_effort=REASONING_EFFORT,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "call_card",
                                    "strict": True,
                                    "schema": CALL_CARD_SCHEMA},
                },
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user",   "content": transcript}],
            )
            return _parse_loose(resp.choices[0].message.content)
        except (APIConnectionError, APITimeoutError) as e:
            last, wait = e, 2 ** attempt
            log.warning("attempt %d failed (%s) - waiting %ds",
                        attempt, type(e).__name__, wait)
            time.sleep(wait)
        except APIStatusError as e:
            if e.status_code == 429:
                last, wait = e, 5 * attempt
                log.warning("rate limited - waiting %ds", wait)
                time.sleep(wait)
            else:
                # If strict mode itself is rejected, fall back to plain JSON
                # mode and let check_shape() do the enforcing.
                if "response_format" in str(e).lower():
                    log.warning("strict mode rejected - falling back to json_object")
                    response_format = {"type": "json_object"}
                raise
        except json.JSONDecodeError as e:
            last = e
            log.warning("attempt %d: not valid JSON", attempt)
    raise RuntimeError(f"model unusable after 3 attempts: {last}")

# ------------------------------------- 3. check the shape before trusting it
REQUIRED = {"caller", "reason", "request", "call_type", "sentiment",
            "urgency", "emergency_flag", "summary", "action_items"}


def check_shape(raw: dict) -> dict:
    missing = REQUIRED - raw.keys()
    if missing:
        raise ValueError(f"missing fields: {sorted(missing)}")
    if not isinstance(raw["action_items"], list):
        raise ValueError("action_items must be a list")
    for item in raw["action_items"]:
        for k in ("action_needed", "action_required", "owner_team", "quote"):
            if not item.get(k):
                raise ValueError(f"action item missing '{k}': {item}")
        item.setdefault("timestamp", "")
        item["due_hint"] = item.get("due_hint") or None      # "" -> None
    return raw



# ------------------------- 4. the quote check - catches invented words
def verify_quote(quote: str, transcript: str) -> bool:
    q = _normalise(quote)
    if len(q.split()) < MIN_QUOTE_WORDS:
        return False
    return q in _normalise(transcript)


def _is_emergency(card: dict, transcript: str) -> bool:
    if card.get("emergency_flag"):
        return True
    flat = _normalise(transcript)
    return any(w in flat for w in EMERGENCY_WORDS)


# ------------------------------------------------------------- 5. the gate
def score_and_route(card: dict, transcript: str):
    if _is_emergency(card, transcript):
                items = card.get("action_items") or [{"action_needed": "(none)"}]
        

    routed, review = [], []
    for item in card.get("action_items", []):
        if len(_normalise(item.get("quote", "")).split()) < MIN_QUOTE_WORDS:
                    review.append({"action_needed": "(none)", "verified": False,
                       "reason": Reason.NO_REQUEST.value,
                       "summary": card.get("summary", "")})

        
        else:
         review.append({"action_needed": "(none)", "verified": False,
                       "reason": Reason.NO_REQUEST.value,
                       "summary": card.get("summary", "")})


    if not card.get("action_items"):
        review.append({"action": "(none)", "verified": False,
                       "reason": Reason.NO_REQUEST.value,
                       "summary": card.get("summary", "")})
    return routed, review


# --------------------------------- 6. one function that never crashes
def process(path: str) -> dict:
    try:
        transcript = load_transcript(path)
    except (FileNotFoundError, ValueError) as e:
        log.error("skipping %s: %s", path, e)
        FAILED.append({"file": str(path), "reason": Reason.BAD_FILE.value,
                       "detail": str(e)})
        return {"status": "skipped", "reason": Reason.BAD_FILE.value}

    try:
        card = check_shape(call_model(transcript))
    except Exception as e:
        log.error("no card for %s: %s", path, e)
        FAILED.append({"file": str(path), "reason": Reason.BAD_SHAPE.value,
                       "detail": str(e)})
        return {"status": "skipped", "reason": Reason.BAD_SHAPE.value}

    routed, review = score_and_route(card, transcript)
    FAILED.extend([{"file": str(path), **r} for r in review])
    log.info("%s -> %d sent, %d to a human",
             Path(path).name, len(routed), len(review))
    return {"status": "ok", "card": card, "routed": routed, "review": review}


# --------------------------------------------- standalone: print the card
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python pipeline.py transcripts/01_scheduling.txt")
    out = process(sys.argv[1])
    if out.get("status") != "ok":
        raise SystemExit(f"no card: {out.get('reason')}")

    card = out["card"]
    print(f"  caller:   {card['caller']}")
    print(f"  reason:   {card['reason']}")
    print(f"  request:  {card['request']}")
    print(f"  summary:  {card['summary']}")
    print(f"\n  type={card['call_type']}  mood={card['sentiment']}"
          f"  urgency={card['urgency']}  emergency={card['emergency_flag']}")

    print("\nTO-DO ITEMS -> sent")
    for i in out["routed"] or [{"action_needed": "(none)"}]:
        print(f"  [SEND]  {i['action_needed']}")
        print(f"          hospital must: {i['action_required']}")
        print(f"          -> {i['owner_team']}  |  from {i['timestamp']}")
        print(f"          \"{i['quote'][:70]}\"")

    print("\nNEEDS A PERSON")
    for i in out["review"] or [{"action_needed": "(none)"}]:
        print(f"  [HOLD]  {i.get('action_needed','(none)')}"
              f"   -- {i.get('reason','')}")
    print()
