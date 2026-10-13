"""In-memory receiver for live microphone audio."""

from dataclasses import dataclass, field
from time import monotonic
from uuid import UUID
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

MAX_CHUNK_BYTES = 5 * 1024 * 1024
MAX_RECORDING_BYTES = 32 * 1024 * 1024
MAX_TOTAL_RECORDING_BYTES = 128 * 1024 * 1024
MAX_ACTIVE_RECORDINGS = 8
RECORDING_IDLE_SECONDS = 15 * 60
ALLOWED_AUDIO_TYPES = {
    "audio/mp4",
    "audio/ogg",
    "audio/webm",
    "audio/x-m4a",
}


@dataclass
class _Recording:
    chunks: list[bytes] = field(default_factory=list)
    total_bytes: int = 0
    next_chunk_index: int = 0
    last_activity: float = field(default_factory=monotonic)
    content_type: str = ""
    completed: bool = False


_recordings: dict[str, _Recording] = {}


def _expire_idle_recordings() -> None:
    now = monotonic()
    expired = [
        recording_id
        for recording_id, recording in _recordings.items()
        if now - recording.last_activity > RECORDING_IDLE_SECONDS
    ]
    for recording_id in expired:
        recording = _recordings.pop(recording_id)
        recording.chunks.clear()


def get_completed_recording(recording_id: str) -> tuple[bytes, str]:
    """Return completed audio for transcription while retaining it for playback."""
    _expire_idle_recordings()
    recording = _recordings.get(str(UUID(recording_id)))
    if recording is None or not recording.completed:
        raise ValueError("The live recording was not found or has expired.")
    audio = b"".join(recording.chunks)
    content_type = recording.content_type
    recording.last_activity = monotonic()
    if not audio:
        raise ValueError("The live recording did not contain any audio.")
    return audio, content_type


async def serve_audio_file(request: Request) -> FileResponse | JSONResponse:
    filename = request.path_params.get("filename", "")
    audio_dir = (Path(__file__).resolve().parent / "audio" / "audio").resolve()
    audio_path = (audio_dir / filename).resolve()
    if (
        audio_path.parent != audio_dir
        or audio_path.suffix.lower() != ".mp3"
        or not audio_path.is_file()
    ):
        return JSONResponse({"error": "Audio file not found."}, status_code=404)
    return FileResponse(
        audio_path,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


async def serve_live_audio(request: Request) -> Response:
    recording_header = request.path_params.get("recording_id", "")
    try:
        recording_id = str(UUID(recording_header))
    except (ValueError, AttributeError):
        return JSONResponse({"error": "Audio recording not found."}, status_code=404)

    _expire_idle_recordings()
    recording = _recordings.get(recording_id)
    if recording is None or not recording.completed:
        return JSONResponse(
            {"error": "Audio recording not found or has expired."},
            status_code=404,
        )
    return Response(
        content=b"".join(recording.chunks),
        media_type=recording.content_type,
        headers={"Cache-Control": "no-store"},
    )


async def receive_live_stream_chunk(request: Request) -> JSONResponse:
    recording_header = request.headers.get("x-recording-id", "")
    try:
        recording_id = str(UUID(recording_header))
    except (ValueError, AttributeError):
        return JSONResponse({"error": "Invalid recording ID."}, status_code=400)

    _expire_idle_recordings()
    is_finished = request.headers.get("x-recording-finished", "").lower() == "true"
    is_aborted = request.headers.get("x-recording-aborted", "").lower() == "true"

    if is_aborted:
        recording = _recordings.pop(recording_id, None)
        if recording is not None:
            recording.chunks.clear()
        return JSONResponse(
            {"status": "discarded"}
        )

    if is_finished:
        recording = _recordings.get(recording_id)
        if recording is None:
            return JSONResponse({"error": "Recording was not found or has expired."}, status_code=404)
        if not recording.chunks:
            _recordings.pop(recording_id)
            return JSONResponse({"error": "No audio chunks were received."}, status_code=422)
        content_type = request.headers.get("x-audio-content-type", "").lower()
        if content_type not in ALLOWED_AUDIO_TYPES:
            _recordings.pop(recording_id)
            recording.chunks.clear()
            return JSONResponse({"error": "Unsupported audio format."}, status_code=415)
        recording.completed = True
        recording.content_type = content_type
        recording.last_activity = monotonic()
        return JSONResponse(
            {
                "status": "completed",
                "recording_id": recording_id,
                "chunks_received": recording.next_chunk_index,
                "bytes_received": recording.total_bytes,
            }
        )

    try:
        chunk_index = int(request.headers.get("x-chunk-index", ""))
        content_length = int(request.headers.get("content-length", ""))
    except ValueError:
        return JSONResponse(
            {"error": "Chunk index and content length are required."},
            status_code=400,
        )

    if chunk_index < 0 or content_length <= 0:
        return JSONResponse({"error": "Audio chunk is empty or invalid."}, status_code=400)
    if content_length > MAX_CHUNK_BYTES:
        return JSONResponse({"error": "Audio chunk is too large."}, status_code=413)

    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in ALLOWED_AUDIO_TYPES:
        return JSONResponse({"error": "Unsupported audio format."}, status_code=415)

    body = bytearray()
    async for body_part in request.stream():
        if len(body) + len(body_part) > MAX_CHUNK_BYTES:
            return JSONResponse({"error": "Audio chunk is too large."}, status_code=413)
        body.extend(body_part)
    if len(body) != content_length:
        return JSONResponse({"error": "Audio chunk length did not match."}, status_code=400)

    total_recording_bytes = sum(
        active_recording.total_bytes for active_recording in _recordings.values()
    )
    if total_recording_bytes + len(body) > MAX_TOTAL_RECORDING_BYTES:
        return JSONResponse(
            {"error": "The server has reached its in-memory audio limit."},
            status_code=429,
        )

    recording = _recordings.get(recording_id)
    if recording is None:
        if chunk_index != 0:
            return JSONResponse(
                {"error": "The first chunk must have index 0."},
                status_code=409,
            )
        if len(_recordings) >= MAX_ACTIVE_RECORDINGS:
            return JSONResponse(
                {"error": "The server has reached its active recording limit."},
                status_code=429,
            )
        recording = _Recording()
        _recordings[recording_id] = recording

    if recording.completed:
        return JSONResponse({"error": "Recording is already complete."}, status_code=409)

    if chunk_index != recording.next_chunk_index:
        return JSONResponse(
            {"error": "Audio chunks arrived out of order."},
            status_code=409,
        )
    if recording.total_bytes + len(body) > MAX_RECORDING_BYTES:
        removed = _recordings.pop(recording_id)
        removed.chunks.clear()
        return JSONResponse(
            {"error": "Recording exceeded the in-memory size limit."},
            status_code=413,
        )
    recording.chunks.append(bytes(body))
    recording.total_bytes += len(body)
    recording.next_chunk_index += 1
    recording.last_activity = monotonic()

    return JSONResponse(
        {
            "status": "accepted",
            "chunks_received": recording.next_chunk_index,
            "bytes_received": recording.total_bytes,
        },
        status_code=202,
    )
