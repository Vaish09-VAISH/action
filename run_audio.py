"""run_audio.py - audio file -> speaker-labelled transcript -> filled-in card.

    python run_audio.py "audio/01_scheduling.mp3"

Sends the audio to Deepgram ONCE, caches the transcript in transcripts/,
so re-running never costs another cent.
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

# ---- keys --------------------------------------------------------------
env_path = Path(__file__).resolve().with_name(".env")
if not env_path.is_file():
    raise SystemExit(f".env not found at {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

from deepgram import DeepgramClient

# ---- flip these two if the speakers come out backwards -----------------
SPEAKER_NAMES = {0: "AGENT", 1: "PATIENT"}

TRANSCRIPT_DIR = Path(__file__).resolve().parent / "transcripts"
TRANSCRIPT_DIR.mkdir(exist_ok=True)


def mmss(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _format_transcript(resp):
    utterances = getattr(resp.results, "utterances", None)
    if not utterances:
        raise ValueError("Deepgram returned no utterances.")

    lines = []
    for utterance in utterances:
        speakers = [
            word.speaker
            for word in utterance.words
            if getattr(word, "speaker", None) is not None
        ]
        name = "SPEAKER"
        if speakers:
            top = max(set(speakers), key=speakers.count)
            name = SPEAKER_NAMES.get(top, f"SPEAKER {top}")
        lines.append(
            f"[{mmss(utterance.start)}] {name}: {utterance.transcript.strip()}"
        )
    return "\n".join(lines) + "\n"


def transcribe_bytes(audio_bytes, content_type):
    """Transcribe in-memory audio without writing audio or transcript files."""
    if not audio_bytes:
        raise ValueError("The live recording contains no audio.")

    dg = DeepgramClient()
    response = dg.listen.v1.media.transcribe_file(
        request=audio_bytes,
        model="nova-3",
        language="en",
        diarize_model="latest",
        utterances=True,
        punctuate=True,
        smart_format=True,
        request_options={
            "additional_headers": {"content-type": content_type},
        },
    )
    return _format_transcript(response)


def transcribe(audio_path):
    """Return a speaker-labelled transcript file. Cached after the first run."""
    out = TRANSCRIPT_DIR / f"{audio_path.stem}.txt"
    if out.exists():
        print(f"  cached transcript found -> {out.name}  (no new Deepgram charge)")
        return out

    print(f"  sending {audio_path.name} ({audio_path.stat().st_size/1e6:.1f} MB) to Deepgram ...")
    dg = DeepgramClient()                      # reads DEEPGRAM_API_KEY from .env
    with open(audio_path, "rb") as fh:
        resp = dg.listen.v1.media.transcribe_file(
            request=fh.read(),
            model="nova-3",
            language="en",
            diarize_model="latest",            # speaker labels (diarize=True is deprecated)
            utterances=True,                   # group words into sentences
            punctuate=True,
            smart_format=True,
        )

    transcript = _format_transcript(resp)
    out.write_text(transcript, encoding="utf-8")
    print(f"  transcribed {len(resp.results.utterances)} turns -> {out.name}")
    return out


def show(path):
    print("\n" + "=" * 70 + "\nTRANSCRIPT\n" + "=" * 70)
    print(path.read_text(encoding="utf-8").strip())

    try:
        import pipeline
    except ImportError:
        print("\n(pipeline.py not found - build Step 3, then re-run to see the card.)")
        return

    print("\n" + "=" * 70 + "\nCARD\n" + "=" * 70)
    result = pipeline.process(str(path))
    if result.get("status") != "ok":
        print(f"  could not build a card: {result.get('reason')}")
        return

    card = result["card"]
    print(f"\n  {card.get('summary','')}")
    print(f"\n  type={card.get('call_type')}  mood={card.get('sentiment')}"
          f"  urgency={card.get('urgency')}  emergency={card.get('emergency_flag')}")

    print("\n  ACTION ITEMS -> sent")
    for i in result["routed"] or []:
        print(f"    [SEND]  {i['action']}")
        print(f"            -> {i['owner_team']}  |  from {i['timestamp']}"
              f"  |  \"{i['quote'][:70]}\"")
    if not result["routed"]:
        print("    (none)")

    print("\n  NEEDS A PERSON")
    for i in result["review"] or []:
        print(f"    [HOLD]  {i.get('action','(none)')}")
        print(f"            reason: {i.get('reason')}")
    if not result["review"]:
        print("    (none)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python run_audio.py "audio/01_scheduling.mp3"')
    audio = Path(sys.argv[1])
    if not audio.exists():
        raise SystemExit(f"No such file: {audio}")
    show(transcribe(audio))
