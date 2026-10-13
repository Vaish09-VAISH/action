from pathlib import Path
from urllib.parse import quote

import streamlit as st

from audio_evidence_component import AUDIO_EVIDENCE_COMPONENT
from audio_player_component import AUDIO_PLAYER_COMPONENT
from live_recorder_component import LIVE_STREAM_COMPONENT

st.set_page_config(
    page_title="AI Call Summary",
    layout="wide"
)


def _handle_audio_jump() -> None:
    component_state = st.session_state["audio-evidence"]
    request = component_state.jump
    if request:
        st.session_state["audio_seek_request"] = request
        st.session_state["active_audio_evidence"] = request["id"]


st.title("AI Healthcare Call Summary")

AUDIO_DIR = Path(__file__).resolve().parent / "audio" / "audio"
audio_files = sorted(AUDIO_DIR.glob("*.mp3"))

if not audio_files:
    st.error("No audio files found in the audio/audio folder.")
    st.stop()

selected_audio = st.selectbox(
    "Select call audio",
    audio_files,
    format_func=lambda path: path.name
)

st.subheader("Live Audio Stream")
st.caption(
    "Record from your microphone and send 5-second chunks to this app. "
    "Audio stays in server memory until summary generation and playback expire."
)
live_result = LIVE_STREAM_COMPONENT(
    key="live-audio-stream",
    data={"endpoint": "/api/live-stream-chunk"},
    on_completed_change=lambda: None,
)

live_summary_error = st.session_state.get("live_summary_error")
completed_call = live_result.completed
if completed_call:
    recording_id = completed_call.get("recordingId")
    if recording_id and recording_id != st.session_state.get(
        "processed_live_recording_id"
    ):
        st.session_state["processed_live_recording_id"] = recording_id
        st.session_state["active_summary_source"] = "live"
        st.session_state["live_audio_url"] = (
            f"/api/live-audio/{quote(recording_id, safe='')}"
        )
        try:
            from live_stream import get_completed_recording

            audio_bytes, content_type = get_completed_recording(recording_id)
            try:
                with st.spinner(
                    "Transcribing the live call and generating its summary..."
                ):
                    env_path = Path(__file__).resolve().with_name(".env")
                    if not env_path.is_file():
                        raise FileNotFoundError(
                            "Create a .env file next to app.py with your "
                            "GROQ_API_KEY and DEEPGRAM_API_KEY to generate summaries."
                        )

                    from run_audio import transcribe_bytes
                    import pipeline

                    transcript = transcribe_bytes(audio_bytes, content_type)
                    result = pipeline.process_transcript(
                        transcript,
                        source_name="live call",
                    )
            finally:
                del audio_bytes

            if result.get("status") != "ok":
                live_summary_error = (
                    "Could not build a summary for the live call: "
                    f"{result.get('reason', 'Unknown error')}"
                )
            else:
                st.session_state["live_summary"] = result
        except Exception as error:
            live_summary_error = f"Live-call summary failed: {error}"
        st.session_state["live_summary_error"] = live_summary_error

left, right = st.columns(2)

with left:
    st.header("🎙️ Call Audio")
    st.subheader("Audio Proof & Timestamp Jump")
    active_source = st.session_state.get("active_summary_source")
    if active_source == "live" and st.session_state.get("live_audio_url"):
        audio_url = st.session_state["live_audio_url"]
        audio_label = "Live call"
    else:
        audio_url = f"/api/audio/{quote(selected_audio.name, safe='')}"
        audio_label = selected_audio.name

    st.caption(f"Audio proof: {audio_label}")
    seek_request = st.session_state.get("audio_seek_request", {})
    AUDIO_PLAYER_COMPONENT(
        key="audio-proof-player",
        data={
            "src": audio_url,
            "seekSeconds": seek_request.get("seconds", 0),
            "seekId": seek_request.get("requestId"),
        },
    )

with right:
    st.header("🤖 AI Summary")
    if live_summary_error:
        st.error(live_summary_error)

    if st.button("Generate Summary", key="generate_summary"):
        with st.spinner("Transcribing and generating summary..."):
            try:
                env_path = Path(__file__).resolve().with_name(".env")
                if not env_path.is_file():
                    raise FileNotFoundError(
                        "Create a .env file next to app.py with your "
                        "GROQ_API_KEY and DEEPGRAM_API_KEY to generate summaries."
                    )

                import pipeline
                from run_audio import transcribe

                transcript_path = transcribe(selected_audio)
                result = pipeline.process(str(transcript_path))

                if result.get("status") != "ok":
                    st.error(
                        "Could not build a card: "
                        f"{result.get('reason', 'Unknown error')}"
                    )
                else:
                    st.session_state["file_summary"] = result
                    st.session_state["file_summary_audio_name"] = selected_audio.name
                    st.session_state["active_summary_source"] = selected_audio.name
            except Exception as error:
                st.error(f"Summary generation failed: {error}")

    active_source = st.session_state.get("active_summary_source")
    if active_source == "live":
        displayed_result = st.session_state.get("live_summary")
        displayed_label = "Live call summary"
    elif (
        active_source == selected_audio.name
        and st.session_state.get("file_summary_audio_name") == selected_audio.name
    ):
        displayed_result = st.session_state.get("file_summary")
        displayed_label = f"Summary for {selected_audio.name}"
    else:
        displayed_result = None
        displayed_label = ""

    if displayed_result:
        st.subheader(displayed_label)
        AUDIO_EVIDENCE_COMPONENT(
            key="audio-evidence",
            data={
                "card": displayed_result["card"],
                "review": displayed_result.get("review", []),
                "activeId": st.session_state.get("active_audio_evidence"),
            },
            on_jump_change=_handle_audio_jump,
        )
