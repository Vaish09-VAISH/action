"""check_audio.py - inspect everything in audio/ without crashing.
Tells you what each file ACTUALLY is, not what its name claims."""

from pathlib import Path
import wave

audio = Path("audio")
if not audio.exists():
    raise SystemExit("No audio/ folder. Run:  mkdir audio")

files = sorted(f for f in audio.iterdir() if f.is_file())
if not files:
    raise SystemExit("audio/ is empty - record your 5 calls into this folder.")


def sniff(head: bytes) -> str:
    """What is this file really?"""
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "WAV"
    if head[:4] == b"RIFF":
        return "RIFF but NOT WAVE (broken export?)"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "MP3"
    if head[4:8] == b"ftyp":
        return "M4A/MP4"
    if head[:4] == b"OggS":
        return "OGG"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "WebM/MKV"
    return "not a recognised audio container"


print(f"{len(files)} file(s) in audio/\n")
print(f"{'file':30s} {'bytes':>10s}  {'actual format':34s} {'length':>8s}")
print("-" * 90)

problems = []

for f in files:
    size = f.stat().st_size
    try:
        with open(f, "rb") as fh:
            head = fh.read(16)
    except OSError as e:
        problems.append(f"{f.name}: could not be opened ({e})")
        print(f"{f.name:30s} {size:>10,}  {'UNREADABLE':34s} {'-':>8s}")
        continue

    kind = sniff(head)
    length = "-"

    if size == 0:
        kind = "EMPTY FILE"
    elif size < 1000:
        kind = "far too small to be audio"

    if kind == "WAV":
        try:
            with wave.open(str(f)) as w:
                secs = w.getnframes() / w.getframerate()
                length = f"{secs:5.1f}s"
                if not (120 <= secs <= 210):
                    problems.append(f"{f.name}: {secs:.1f}s  (aim for 2-3 minutes)")
                if w.getnchannels() > 1:
                    problems.append(f"{f.name}: {w.getnchannels()} channels (mono expected)")
        except Exception as e:
            length = "?"
            problems.append(f"{f.name}: WAV unreadable - {type(e).__name__}: {e}")

    print(f"{f.name:30s} {size:>10,}  {kind:34s} {length:>8s}")

    if kind in ("EMPTY FILE", "far too small to be audio"):
        problems.append(f"{f.name}: no actual recording in it - record this call again")
    elif kind not in ("WAV", "MP3", "M4A/MP4", "OGG", "WebM/MKV"):
        problems.append(f"{f.name}: {kind} - not usable as audio")
    if f.name.startswith("00_"):
        problems.append(f"{f.name}: looks like a leftover test file - delete it")

print("-" * 90)
if problems:
    print("\nTO FIX")
    for p in problems:
        print("  -", p)
else:
    print("\nAll files look like real recordings.")
    print("Now play each one and confirm you can clearly hear BOTH speakers.")
