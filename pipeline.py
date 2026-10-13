import json, logging, re, time
from enum import Enum
from pathlib import Path
from typing import Any

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
        "evidence": {
            "type": "object",
            "properties": {
                field: {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "string"},
                        "quote": {"type": "string"},
                    },
                    "required": ["timestamp", "quote"],
                    "additionalProperties": False,
                }
                for field in ("summary", "sentiment", "urgency", "emergency")
            },
            "required": ["summary", "sentiment", "urgency", "emergency"],
            "additionalProperties": False,
        },
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "action_required": {"type": "string"},
                    "owner_team": {"type": "string", "enum": [
                        "scheduling", "pharmacy", "billing",
                        "complaints", "nursing", "human_review"]},
                    "quote": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "timestamp_sec": {"type": "number", "minimum": 0},
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "status": {
                        "type": "string",
                        "enum": ["verified", "needs_review"],
                    },
                    "due_hint": {"type": "string"},
                },
                "required": ["text", "action_required", "owner_team",
                             "quote", "timestamp", "timestamp_sec",
                             "confidence", "status", "due_hint"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["caller", "reason", "request", "call_type", "sentiment",
                 "urgency", "emergency_flag", "summary", "evidence",
                 "action_items"],
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
- text: the patient-facing outcome - what the caller must be told
  or given.
- action_required: the internal step the hospital must take to get there.
- owner_team: the team that owns that internal step.

RULES - follow every one:
1. Extract ONLY what is explicitly said. Never add what you assume.
2. Every action item MUST include supporting words in "quote". Copy them
   character for character from the transcript. Do not fix grammar, do not
   paraphrase. If a sentence is split across two transcript lines, you may
   join it - the words must still be continuous as spoken.
   Copy the matching [MM:SS] prefix into its "timestamp".
   Also set "timestamp_sec" to that timestamp in seconds. Set "confidence"
   to a number from 0.0 to 1.0 based on whether the quote directly supports
   the action, and set "status" to "verified" only when confidence is at
   least 0.7; otherwise use "needs_review". Server-side checks recalculate
   confidence and status from quote traceability.
2a. Add "evidence" for summary, sentiment, urgency, and emergency. Each
   entry has "quote" (an exact, short supporting quote from the transcript)
   and "timestamp" (the [MM:SS] prefix from the line containing that quote).
   If you cannot find supporting words, use quote "none" and timestamp "".
3. NEVER invent a due date. Put a date in "due_hint" ONLY if one was
   actually spoken. Otherwise use an empty string "".
4. If the caller never asked for anything, return "action_items": [],
   set "request" to "none", and set "text" nowhere else.
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
    evidence = raw.setdefault("evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    for field in ("summary", "sentiment", "urgency", "emergency"):
        item = evidence.setdefault(field, {})
        if not isinstance(item, dict):
            raise ValueError(f"evidence.{field} must be an object")
        item.setdefault("timestamp", "")
        item.setdefault("quote", "")
    for item in raw["action_items"]:
        for k in ("action_required", "owner_team"):
            if not item.get(k):
                raise ValueError(f"action item missing '{k}': {item}")
        item["text"] = item.get("text") or item.get("action_needed")
        if not item["text"]:
            raise ValueError(f"action item missing 'text': {item}")
        item["action_needed"] = item["text"]
        item["quote"] = item.get("quote") or ""
        item.setdefault("timestamp", "")
        item["due_hint"] = item.get("due_hint") or None      # "" -> None
    return raw


_TRANSCRIPT_TIMESTAMP = re.compile(r"^\[(\d{1,2}):(\d{2})\]")


def _timestamp_seconds(timestamp: Any) -> float | None:
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        return max(0.0, float(timestamp))
    if not isinstance(timestamp, str) or not timestamp.strip():
        return None
    value = timestamp.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
    if not match or int(match.group(2)) > 59:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _timestamp_for_quote(quote: str, transcript: str) -> float | None:
    normalized_quote = _normalise(quote)
    if not normalized_quote or normalized_quote == "none":
        return None
    for line in transcript.splitlines():
        timestamp = _TRANSCRIPT_TIMESTAMP.match(line)
        if timestamp and normalized_quote in _normalise(line):
            return int(timestamp.group(1)) * 60 + int(timestamp.group(2))
    return None


def _attach_audio_timestamps(card: dict, transcript: str) -> None:
    evidence = card["evidence"]
    for field in ("summary", "sentiment", "urgency", "emergency"):
        item = evidence[field]
        timestamp = _timestamp_for_quote(item.get("quote", ""), transcript)
        if timestamp is None:
            timestamp = _timestamp_seconds(item.get("timestamp"))
        item["timestamp"] = timestamp if timestamp is not None else 0.0

    for item in card["action_items"]:
        timestamp = _timestamp_for_quote(item["quote"], transcript)
        if timestamp is None:
            timestamp = _timestamp_seconds(
                item.get("timestamp_sec", item.get("timestamp"))
            )
        item["timestamp_sec"] = timestamp if timestamp is not None else 0.0
        item["timestamp"] = item["timestamp_sec"]


# ------------------------- 4. the quote check - catches invented words
def verify_quote(quote: str, transcript: str) -> bool:
    exact_quote = quote.strip()
    if len(_normalise(exact_quote).split()) < MIN_QUOTE_WORDS:
        return False
    spoken_text = " ".join(
        _PREFIX.sub("", line).strip() for line in transcript.splitlines()
    )
    return exact_quote in spoken_text


def _is_emergency(card: dict, transcript: str) -> bool:
    if card.get("emergency_flag"):
        return True
    flat = _normalise(transcript)
    return any(w in flat for w in EMERGENCY_WORDS)


# ------------------------------------------------------------- 5. the gate
def score_and_route(card: dict, transcript: str):
    routed, review = [], []
    emergency = _is_emergency(card, transcript)

    for item in card.get("action_items", []):
        quote_is_traceable = (
            len(_normalise(item.get("quote", "")).split()) >= MIN_QUOTE_WORDS
            and verify_quote(item.get("quote", ""), transcript)
        )
        confidence = (
            0.94 if quote_is_traceable and not emergency
            else 0.52 if quote_is_traceable
            else 0.35
        )
        item["confidence"] = confidence
        item["status"] = "verified" if confidence >= 0.7 else "needs_review"
        item["traceability_scored"] = True

        if emergency:
            review.append({
                **item,
                "verified": False,
                "reason": Reason.EMERGENCY.value,
            })
        elif len(_normalise(item.get("quote", "")).split()) < MIN_QUOTE_WORDS:
            review.append({
                **item,
                "verified": False,
                "reason": Reason.QUOTE_TOO_SHORT.value,
            })
        elif not verify_quote(item["quote"], transcript):
            review.append({
                **item,
                "verified": False,
                "reason": Reason.QUOTE_NOT_FOUND.value,
            })
        else:
            routed.append({**item, "verified": True})

    if not card.get("action_items"):
        review.append({
            "action_needed": "(none)",
            "quote": "",
            "timestamp": 0.0,
            "verified": False,
            "reason": (
                Reason.EMERGENCY.value if emergency else Reason.NO_REQUEST.value
            ),
            "summary": card.get("summary", ""),
        })
    return routed, review


# --------------------------------- 6. one function that never crashes
def process_transcript(transcript: str, source_name: str = "live-call") -> dict:
    transcript = transcript.strip()
    if len(transcript) < 50:
        reason = f"{source_name} is empty or far too short ({len(transcript)} chars)"
        log.error("skipping %s: %s", source_name, reason)
        FAILED.append({"file": source_name, "reason": Reason.BAD_FILE.value,
                       "detail": reason})
        return {"status": "skipped", "reason": Reason.BAD_FILE.value}

    try:
        card = check_shape(call_model(transcript))
        _attach_audio_timestamps(card, transcript)
    except Exception as e:
        log.error("no card for %s: %s", source_name, e)
        FAILED.append({"file": source_name, "reason": Reason.BAD_SHAPE.value,
                       "detail": str(e)})
        return {"status": "skipped", "reason": Reason.BAD_SHAPE.value}

    routed, review = score_and_route(card, transcript)
    FAILED.extend([{"file": source_name, **r} for r in review])
    log.info("%s -> %d sent, %d to a human",
             source_name, len(routed), len(review))
    return {"status": "ok", "card": card, "routed": routed, "review": review}


def process(path: str) -> dict:
    try:
        transcript = load_transcript(path)
    except (FileNotFoundError, ValueError) as e:
        log.error("skipping %s: %s", path, e)
        FAILED.append({"file": str(path), "reason": Reason.BAD_FILE.value,
                       "detail": str(e)})
        return {"status": "skipped", "reason": Reason.BAD_FILE.value}
    return process_transcript(transcript, Path(path).name)


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
