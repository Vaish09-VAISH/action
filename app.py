import base64
import hashlib
import logging
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


def _handle_action_review() -> None:
    component_state = st.session_state.get("audio-evidence")
    request = getattr(component_state, "action_review", None)
    if not request:
        return

    active_source = st.session_state.get("active_summary_source")
    summary_key = {
        "staff_notes": "staff_notes_summary",
        "live": "live_summary",
        "local_mic": "local_mic_summary",
    }.get(active_source)
    if summary_key is None and (
        st.session_state.get("file_summary_audio_name") == active_source
    ):
        summary_key = "file_summary"
    result = st.session_state.get(summary_key) if summary_key else None
    items = result.get("card", {}).get("action_items", []) if result else []
    item_index = request.get("itemIndex")
    if (
        not isinstance(item_index, int)
        or isinstance(item_index, bool)
        or item_index < 0
        or item_index >= len(items)
    ):
        return

    item = items[item_index]
    if request.get("action") == "approve":
        item["status"] = "verified"
        item["human_approved"] = True
    elif request.get("action") == "dismiss":
        items.pop(item_index)


def _send_verified_action(result: dict, transcript_file: str, key_suffix: str) -> None:
    import agent

    actions = [
        item
        for item in result.get("card", {}).get("action_items", [])
        if item.get("status") == "verified"
        and item.get("owner_team") in agent.DEPARTMENTS
    ]
    if not actions:
        return

    selected_index = st.selectbox(
        "Verified action item to send",
        range(len(actions)),
        format_func=lambda index: (
            f"{actions[index].get('owner_team')}: "
            f"{actions[index].get('text') or actions[index].get('action_required')}"
        ),
        key=f"department_action_{key_suffix}",
    )
    if st.button("Send to Department", key=f"send_department_{key_suffix}"):
        action = actions[selected_index]
        message = agent.create_department_message(
            transcript_file=transcript_file,
            department=action["owner_team"],
            action_item=action.get("text") or action.get("action_required", ""),
        )
        st.session_state.setdefault("department_messages", []).append(message)
        logging.getLogger(__name__).info(
            "%s -> 1 sent, 0 to a human",
            transcript_file,
        )
        st.success(
            f"Added action to the {action['owner_team']} department queue."
        )


st.title("AI Healthcare Call Summary")

import agent

st.session_state.setdefault("department_messages", [])
agent.render_department_queue(st.session_state["department_messages"])

input_method = st.radio(
    "Call Input Method",
    [
        "Recorded Call",
        "No Recording — Staff Notes",
        "Local Mic — Deepgram + Groq",
    ],
    horizontal=True,
    key="call_input_method",
)

if input_method == "Local Mic — Deepgram + Groq":
    st.subheader("Local Microphone Call Summary")
    st.caption(
        "Local microphone prototype: audio is transcribed with Deepgram and "
        "summarized with Groq. It does not connect to a telephone provider."
    )
    st.info(
        "Use test audio only. Do not record a patient or caller without their "
        "explicit consent. If recording is declined, use Staff Notes instead."
    )
    if st.checkbox(
        "I have consent to record and process this test audio.",
        key="local_mic_recording_consent",
    ):
        from streamlit_mic_recorder import mic_recorder

        audio_record = mic_recorder(
            start_prompt="Start microphone call",
            stop_prompt="Stop recording",
            format="wav",
            key="local_mic_deepgram_groq",
        )
        if audio_record and audio_record.get("bytes"):
            audio_bytes = audio_record["bytes"]
            audio_digest = hashlib.sha256(audio_bytes).hexdigest()
            st.audio(audio_bytes, format="audio/wav")
            if st.button(
                "Transcribe and Summarize",
                key=f"process_local_mic_{audio_digest[:12]}",
            ):
                try:
                    from settings import require_secrets

                    require_secrets("GROQ_API_KEY", "DEEPGRAM_API_KEY")

                    from run_audio import transcribe_bytes
                    import pipeline

                    with st.spinner(
                        "Transcribing with Deepgram and generating the summary "
                        "with Groq..."
                    ):
                        transcript = transcribe_bytes(audio_bytes, "audio/wav")
                        result = pipeline.process_transcript(
                            transcript,
                            source_name="local microphone call",
                        )
                    if result.get("status") != "ok":
                        st.error(
                            "Could not build a summary for the microphone call: "
                            f"{result.get('reason', 'Unknown error')}"
                        )
                    else:
                        st.session_state["local_mic_summary"] = {
                            "transcript": transcript,
                            "transcript_file": "local_mic_transcript.txt",
                            "audio_digest": audio_digest,
                            **result,
                        }
                        st.session_state["active_summary_source"] = "local_mic"
                except Exception as error:
                    st.error(f"Microphone call processing failed: {error}")

            local_result = st.session_state.get("local_mic_summary")
            if local_result and local_result.get("audio_digest") == audio_digest:
                st.subheader("Transcript (Deepgram)")
                st.text(local_result["transcript"])
                st.subheader("AI Summary (Groq)")
                st.info(local_result["card"].get("summary", "Not available"))
                AUDIO_EVIDENCE_COMPONENT(
                    key="audio-evidence",
                    data={
                        "card": local_result["card"],
                        "review": local_result.get("review", []),
                        "activeId": st.session_state.get("active_audio_evidence"),
                        "audioAvailable": False,
                    },
                    on_jump_change=_handle_audio_jump,
                    on_action_review_change=_handle_action_review,
                )
                _send_verified_action(
                    local_result,
                    local_result["transcript_file"],
                    "local_mic",
                )
    else:
        st.caption("Confirm consent to enable microphone recording and processing.")
    st.stop()

if input_method == "No Recording — Staff Notes":
    st.caption(
        "Enter factual notes from the call. "
        "Nothing is processed until you click Process Staff Notes."
    )
    staff_notes = st.text_area(
        "Staff notes",
        placeholder="Enter factual details discussed during the call...",
        height=200,
        key="staff_notes_input",
    )
    if st.button("Process Staff Notes", key="process_staff_notes"):
        if not staff_notes.strip():
            st.error("Enter staff notes before processing.")
        else:
            try:
                import pipeline

                result = pipeline.process_staff_notes(staff_notes)
                if result.get("status") != "ok":
                    st.error(
                        "Could not process staff notes: "
                        f"{result.get('reason', 'Unknown error')}"
                    )
                else:
                    st.session_state["staff_notes_summary"] = result
                    st.session_state["active_summary_source"] = "staff_notes"
            except Exception as error:
                st.error(f"Staff-notes processing failed: {error}")

    if st.session_state.get("active_summary_source") == "staff_notes":
        notes_result = st.session_state.get("staff_notes_summary")
        if notes_result:
            st.header("🤖 AI Summary")
            st.subheader("Summary from Staff Notes")
            st.caption(f"Input source: {notes_result['source']}")
            review_reason = notes_result.get("review_reason", "")
            if notes_result.get("potentially_urgent"):
                st.error(
                    "Urgent concern detected in the notes. Follow the existing "
                    "human-review/safety process immediately."
                )
            elif notes_result.get("needs_human_review"):
                st.warning(
                    "Human review required: "
                    f"{review_reason or 'Staff-entered notes are not a verbatim transcript.'} "
                    "Review the extracted information before taking action."
                )
            st.info(notes_result["card"].get("summary", "Not available"))
            AUDIO_EVIDENCE_COMPONENT(
                key="audio-evidence",
                data={
                    "card": notes_result["card"],
                    "review": notes_result.get("review", []),
                    "activeId": st.session_state.get("active_audio_evidence"),
                    "audioAvailable": False,
                },
                on_jump_change=_handle_audio_jump,
                on_action_review_change=_handle_action_review,
            )
    st.stop()

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
                    from settings import require_secrets

                    require_secrets("GROQ_API_KEY", "DEEPGRAM_API_KEY")

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
                result["transcript_file"] = "live_call_transcript.txt"
                st.session_state["live_summary"] = result
                live_summary_error = None
        except Exception as error:
            live_summary_error = f"Live-call summary failed: {error}"
        st.session_state["live_summary_error"] = live_summary_error

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

st.header("🎙️ Call Audio")
st.subheader("Audio Proof & Timestamp Jump")
if active_source == "live" and st.session_state.get("live_audio_url"):
    audio_url = st.session_state["live_audio_url"]
    audio_label = "Live call"
else:
    encoded_audio = base64.b64encode(selected_audio.read_bytes()).decode("ascii")
    audio_url = f"data:audio/mpeg;base64,{encoded_audio}"
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

st.header("🤖 AI Summary")
if live_summary_error:
    st.error(live_summary_error)

if st.button("Generate Summary", key="generate_summary"):
    with st.spinner("Transcribing and generating summary..."):
        try:
            from settings import require_secrets

            require_secrets("GROQ_API_KEY", "DEEPGRAM_API_KEY")

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
                st.session_state["file_summary"]["transcript_file"] = (
                    transcript_path.name
                )
                st.session_state["file_summary_audio_name"] = selected_audio.name
                st.session_state["active_summary_source"] = selected_audio.name
                displayed_result = result
                displayed_label = f"Summary for {selected_audio.name}"
        except Exception as error:
            st.error(f"Summary generation failed: {error}")

if displayed_result:
    st.subheader(displayed_label)
    summary_text = displayed_result["card"].get("summary", "Not available")
    st.info(summary_text)
    AUDIO_EVIDENCE_COMPONENT(
        key="audio-evidence",
        data={
            "card": displayed_result["card"],
            "review": displayed_result.get("review", []),
            "activeId": st.session_state.get("active_audio_evidence"),
        },
        on_jump_change=_handle_audio_jump,
        on_action_review_change=_handle_action_review,
    )
    transcript_file = displayed_result.get(
        "transcript_file",
        "live_call_transcript.txt"
        if active_source == "live"
        else f"{Path(selected_audio.name).stem}.txt",
    )
    _send_verified_action(displayed_result, transcript_file, "summary")
else:
    st.caption(
        "Generate a summary for the selected audio, or stop a live call to "
        "see its summary here."
    )
