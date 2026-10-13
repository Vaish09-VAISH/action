import os
import wave
from pathlib import Path

from dotenv import load_dotenv

# Loads .env from the same folder as this script.
env_path = Path(__file__).resolve().with_name(".env")
if not env_path.is_file():
    raise FileNotFoundError(f".env not found: {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

groq_key = os.getenv("GROQ_API_KEY")
deepgram_key = os.getenv("DEEPGRAM_API_KEY")
if not groq_key or not deepgram_key:
    raise RuntimeError("GROQ_API_KEY or DEEPGRAM_API_KEY is missing from .env")

# Test Groq
from groq import Groq

client = Groq(api_key=groq_key)
reply = client.chat.completions.create(
    messages=[{"role": "user", "content": "Reply with the single word: OK"}],
     model="openai/gpt-oss-120b",
)
print("GROQ: OK ->", reply.choices[0].message.content.strip())

# Test Deepgram with a temporary silent WAV
from deepgram import DeepgramClient

test_wav = Path(__file__).resolve().with_name("_test.wav")
try:
    with wave.open(str(test_wav), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 16000 * 2)

    dg = DeepgramClient(api_key=deepgram_key)
    with test_wav.open("rb") as audio_file:
        dg.listen.v1.media.transcribe_file(
            request=audio_file.read(),
            model="nova-3",
            language="en",
        )
    print("DEEPGRAM: OK")
finally:
    test_wav.unlink(missing_ok=True)