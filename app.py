import streamlit as st
from pathlib import Path
import pipeline
from run_audio import transcribe

st.set_page_config(
    page_title="AI Call Summary",
    layout="wide"
)

st.title("AI Healthcare Call Summary")
from pathlib import Path

AUDIO_DIR = Path("audio/audio")

audio_files = sorted(AUDIO_DIR.glob("*.mp3"))

if not audio_files:
    st.error("No audio files found in the audio/audio folder.")
    st.stop()

selected_audio = st.selectbox(
    "Select call audio",
    audio_files,
    format_func=lambda path: path.name
)

audio_path = selected_audio

left, right = st.columns(2)

# ---------------- LEFT: AUDIO ----------------
with left:
    st.header("🎙️ Call Audio")

    if audio_path.exists():
        with open(audio_path, "rb") as audio_file:
            audio_bytes = audio_file.read()

        st.audio(audio_bytes, format="audio/mpeg")

        st.write(f"**File:** {audio_path.name}")
    else:
        st.error(f"Audio file not found: {audio_path}")


# ---------------- RIGHT: AI RESULT ----------------
with right:
    st.header("🤖 AI Summary")

    if st.button("Generate Summary", key="generate_summary"):
        with st.spinner("Transcribing and generating summary..."):
            try:
                transcript_path = transcribe(audio_path)
                result = pipeline.process(str(transcript_path))

                if result.get("status") != "ok":
                    st.error(
                        f"Could not build a card: "
                        f"{result.get('reason', 'Unknown error')}"
                    )
                else:
                    card = result["card"]

                    st.subheader("Summary")
                    st.write(card.get("summary", "Not available"))

                    st.divider()

                    st.write(
                        f"**Call type:** "
                        f"{card.get('call_type', 'Not available')}"
                    )
                    st.write(
                        f"**Mood:** "
                        f"{card.get('sentiment', 'Not available')}"
                    )
                    st.write(
                        f"**Urgency:** "
                        f"{card.get('urgency', 'Not available')}"
                    )
                    st.write(
                        f"**Emergency:** "
                        f"{card.get('emergency_flag', 'Not available')}"
                    )

                    st.divider()
                    st.subheader("Action Items")

                    routed = result.get("routed", [])
                    if routed:
                        for item in routed:
                            st.write(
                                f"**SEND:** "
                                f"{item.get('action', 'Not available')}"
                            )
                            st.write(
                                f"Owner: "
                                f"{item.get('owner_team', 'Not available')}"
                            )
                    else:
                        st.write("None")

                    st.divider()
                    st.subheader("Needs a Person")

                    review = result.get("review", [])
                    if review:
                        for item in review:
                            st.warning(
                                f"{item.get('action', 'Review required')} — "
                                f"{item.get('reason', '')}"
                            )
                    else:
                        st.write("None")
            except Exception as e:
                st.error(f"Summary generation failed: {e}")