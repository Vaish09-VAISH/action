import json, logging, re, time
from enum import Enum
from pathlib import Path
from typing import Any

from groq import Groq, APIStatusError, APIConnectionError, APITimeoutError
from settings import get_secret

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
                        "complaints", "medical_records", "nursing",
                        "human_review"]},
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


STAFF_NOTES_SYSTEM_PROMPT = """You extract a cautious call summary from staff-entered notes.

The input is staff notes, not a verbatim transcript. It may be incomplete,
ambiguous, or paraphrased.

RULES - follow every one:
1. Use only facts explicitly present in the notes. Do not infer missing facts.
2. Never present anything as a direct patient statement or verbatim quote.
3. Never invent quotations, dates, promises, medical facts, or completed
   actions. Distinguish a requested or proposed action from one confirmed done.
4. If the caller, request, timing, or intended action is unclear, say so in
   the appropriate field and do not guess.
5. Extract an action item only when the notes explicitly state an action or
   request. Keep its text faithful to the notes. If its owner is unclear, use
   "human_review". If no action is stated, return an empty action_items list.
6. Set emergency_flag true and urgency to urgent when the notes suggest a
   potentially urgent concern. Do not treat the absence of a known phrase as
   proof that the call is safe. Unclear or urgent issues require human review;
   do not recommend a treatment.
7. For every evidence field, set quote to "none" and timestamp to "".
   Notes are not verbatim evidence and have no audio timestamps.
8. For every action item, set quote to "none", timestamp to "",
   timestamp_sec to 0, confidence to 0 only as a schema placeholder (not as a
   probability), and status to "needs_review". The application removes the
   confidence value before displaying staff-note actions.
9. Set due_hint to "" unless a specific due date is explicitly written in
   the notes. Keep the summary to 2-3 concise sentences and identify it as
   based on staff-entered notes."""


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

STAFF_NOTES_URGENT_PHRASES = (
    "chest pressure", "chest tightness", "difficulty breathing",
    "difficulty in breathing", "breathing difficulty",
    "severe allergic reaction", "anaphylaxis", "heavy bleeding",
    "uncontrolled bleeding", "face drooping", "slurred speech",
    "sudden weakness", "loss of consciousness", "passed out",
    "cannot wake", "unresponsive", "self harm", "immediate danger",
    "immediate help", "urgent medical attention", "severe pain",
)

UNCLEAR_MARKERS = (
    "unclear", "unknown", "not specified", "not sure",
    "unable to determine", "need clarification", "needs clarification",
    "pending clarification", "to be determined", "not documented",
    "not recorded",
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


def call_model(transcript: str, system_prompt: str = SYSTEM_PROMPT) -> dict:
    client, last = Groq(api_key=get_secret("GROQ_API_KEY")), None
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
                messages=[{"role": "system", "content": system_prompt},
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


def _validate_staff_notes_output(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("model output must be a JSON object")

    required_fields = REQUIRED | {"evidence"}
    missing = required_fields - raw.keys()
    if missing:
        raise ValueError(f"missing fields: {sorted(missing)}")

    string_fields = (
        "caller", "reason", "request", "call_type", "sentiment",
        "urgency", "summary",
    )
    for field in string_fields:
        if not isinstance(raw[field], str):
            raise ValueError(f"{field} must be a string")

    if raw["call_type"] not in {
        "appointment", "refill", "billing", "complaint",
        "lab_results", "clinical_question", "other",
    }:
        raise ValueError("call_type has an unsupported value")
    if raw["sentiment"] not in {"calm", "frustrated", "angry"}:
        raise ValueError("sentiment has an unsupported value")
    if raw["urgency"] not in {"routine", "soon", "urgent"}:
        raise ValueError("urgency has an unsupported value")
    if not isinstance(raw["emergency_flag"], bool):
        raise ValueError("emergency_flag must be a boolean")
    if not raw["summary"].strip():
        raise ValueError("summary must not be empty")

    evidence = raw["evidence"]
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    for field in ("summary", "sentiment", "urgency", "emergency"):
        item = evidence.get(field)
        if not isinstance(item, dict):
            raise ValueError(f"evidence.{field} must be an object")
        if not isinstance(item.get("quote"), str):
            raise ValueError(f"evidence.{field}.quote must be a string")
        if not isinstance(item.get("timestamp"), str):
            raise ValueError(f"evidence.{field}.timestamp must be a string")

    if not isinstance(raw["action_items"], list):
        raise ValueError("action_items must be a list")
    action_fields = {
        "text", "action_required", "owner_team", "quote", "timestamp",
        "timestamp_sec", "confidence", "status", "due_hint",
    }
    for index, item in enumerate(raw["action_items"]):
        if not isinstance(item, dict):
            raise ValueError(f"action_items[{index}] must be an object")
        missing_action_fields = action_fields - item.keys()
        if missing_action_fields:
            raise ValueError(
                f"action_items[{index}] missing fields: "
                f"{sorted(missing_action_fields)}"
            )
        for field in ("text", "action_required", "owner_team", "quote",
                      "timestamp", "status", "due_hint"):
            if not isinstance(item[field], str):
                raise ValueError(f"action_items[{index}].{field} must be a string")
        if item["owner_team"] not in {
            "scheduling", "pharmacy", "billing", "complaints",
            "medical_records", "nursing", "human_review",
        }:
            raise ValueError(f"action_items[{index}].owner_team is invalid")
        if item["status"] not in {"verified", "needs_review"}:
            raise ValueError(f"action_items[{index}].status is invalid")
        timestamp = item["timestamp_sec"]
        if (
            not isinstance(timestamp, (int, float))
            or isinstance(timestamp, bool)
            or timestamp < 0
        ):
            raise ValueError(f"action_items[{index}].timestamp_sec is invalid")
        confidence = item["confidence"]
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= confidence <= 1
        ):
            raise ValueError(f"action_items[{index}].confidence is invalid")

    return check_shape(raw)


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


def process_staff_notes(notes: str) -> dict:
    """Process staff-entered notes without transcript quote validation/routing."""
    if not isinstance(notes, str) or not notes.strip():
        return {"status": "error", "reason": "Enter staff notes before processing."}

    notes = notes.strip()
    try:
        model_output = call_model(notes, STAFF_NOTES_SYSTEM_PROMPT)
        card = _validate_staff_notes_output(model_output)
    except Exception as error:
        log.error("could not process staff-entered notes: %s", error)
        FAILED.append({
            "file": "Staff-entered notes",
            "reason": Reason.BAD_SHAPE.value,
            "detail": str(error),
        })
        return {
            "status": "error",
            "reason": f"Staff notes could not be processed: {error}",
        }

    for field in ("summary", "sentiment", "urgency", "emergency"):
        card["evidence"][field]["quote"] = "none"
        card["evidence"][field]["timestamp"] = ""

    normalized_notes = _normalise(notes)
    urgent = (
        _is_emergency(card, notes)
        or card.get("urgency") == "urgent"
        or any(
            phrase in normalized_notes
            for phrase in STAFF_NOTES_URGENT_PHRASES
        )
    )

    def is_unclear(value: Any) -> bool:
        normalized = _normalise(str(value or ""))
        return not normalized or normalized in {"none", "n a", "na"} or any(
            marker in normalized for marker in UNCLEAR_MARKERS
        )

    unclear_fields = []
    if is_unclear(card.get("caller")):
        unclear_fields.append("caller")
    if is_unclear(card.get("request")):
        unclear_fields.append("request")
    if any(marker in normalized_notes for marker in UNCLEAR_MARKERS):
        unclear_fields.append("notes contain an explicit uncertainty")

    review = []
    for item in card["action_items"]:
        item["quote"] = "none"
        item["timestamp"] = ""
        item["timestamp_sec"] = 0.0
        item["confidence"] = None
        item["confidence_available"] = False
        item["status"] = "needs_review"
        item["traceability_scored"] = True
        item["verified"] = False
        owner = _normalise(item.get("owner_team", ""))
        next_step = item.get("action_required", "")
        if owner in {"", "none", "unknown", "human review"}:
            unclear_fields.append("action owner")
        if is_unclear(next_step) or is_unclear(item.get("text")):
            unclear_fields.append("next step")
        review.append(item.copy())

    if not card["action_items"]:
        unclear_fields.append("next step")

    if urgent:
        review_reason = Reason.EMERGENCY.value
    elif unclear_fields:
        review_reason = (
            "Human review required because these notes are unclear or incomplete: "
            + ", ".join(dict.fromkeys(unclear_fields))
            + "."
        )
    else:
        review_reason = (
            "Staff-entered notes are not verbatim evidence; human review required."
        )

    for item in review:
        item["reason"] = review_reason

    if urgent or unclear_fields:
        if urgent:
            review_reason = (
                "Potentially urgent healthcare concern: "
                "immediate human review required. " + Reason.EMERGENCY.value
            )
        review.append({
            "action_needed": "(none)",
            "text": "(none)",
            "quote": "none",
            "timestamp": "",
            "timestamp_sec": 0.0,
            "confidence": None,
            "confidence_available": False,
            "status": "needs_review",
            "verified": False,
            "reason": review_reason,
            "summary": card["summary"],
        })

    log.info("Staff-entered notes -> 0 routed, %d to human review", len(review))
    return {
        "status": "ok",
        "source": "Staff-entered notes",
        "card": card,
        "routed": [],
        "review": review,
        "needs_human_review": True,
        "review_reason": review_reason,
        "potentially_urgent": urgent,
        "unclear_fields": list(dict.fromkeys(unclear_fields)),
    }


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
