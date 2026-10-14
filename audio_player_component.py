"""Audio player component that can seek to evidence timestamps."""

import base64
from pathlib import Path

import streamlit as st

_PLAYER_HTML = """
<section class="audio-proof-player" aria-label="Call audio player">
  <audio id="proof-audio" controls preload="metadata"></audio>
  <button id="proof-audio-reload" type="button">Reload audio</button>
  <p id="proof-audio-status" role="status" aria-live="polite"></p>
</section>
"""

_PLAYER_CSS = """
.audio-proof-player {
  border: 1px solid var(--st-border-color, #d1d5db);
  border-radius: 0.75rem;
  padding: 1rem;
  background: var(--st-secondary-background-color, #f8fafc);
  color: var(--st-text-color, #111827);
  font-family: var(--st-font, sans-serif);
}
#proof-audio { display: block; width: 100%; }
#proof-audio-reload {
  display: inline-block;
  margin-top: 0.65rem;
  color: var(--st-primary-color, #2563eb);
  border: 1px solid var(--st-border-color, #d1d5db);
  border-radius: 0.45rem;
  padding: 0.35rem 0.65rem;
  background: transparent;
  font: inherit;
  font-size: 0.85rem;
  cursor: pointer;
}
#proof-audio-status {
  min-height: 1.25rem;
  margin: 0.5rem 0 0;
  color: var(--st-secondary-text-color, #6b7280);
  font-size: 0.875rem;
}
"""

_PLAYER_JS = """
const instances = new WeakMap();

export default function (component) {
  const { parentElement, data } = component;
  let instance = instances.get(parentElement);

  if (!instance) {
    const audio = parentElement.querySelector("#proof-audio");
    const reloadButton = parentElement.querySelector("#proof-audio-reload");
    const status = parentElement.querySelector("#proof-audio-status");
    if (!audio || !reloadButton || !status) return;

    instance = {
      audioRef: { current: audio },
      reloadButton,
      status,
      source: null,
      seekId: null,
      pendingSeconds: null,
    };
    instances.set(parentElement, instance);

    const jumpToTimestamp = (seconds) => {
      const player = instance.audioRef.current;
      const target = Number(seconds);
      if (!player || !Number.isFinite(target) || target < 0) {
        instance.status.textContent = "This timestamp is unavailable.";
        return;
      }

      if (player.readyState < HTMLMediaElement.HAVE_METADATA) {
        instance.pendingSeconds = target;
        instance.status.textContent = "Loading audio…";
        return;
      }

      const duration = Number.isFinite(player.duration) ? player.duration : target;
      player.currentTime = Math.min(target, duration);
      const playPromise = player.play();
      if (playPromise && typeof playPromise.catch === "function") {
        playPromise.catch(() => {
          instance.status.textContent =
            "Jumped to the quote. Press play if the browser blocked playback.";
        });
      }
      instance.status.textContent = `Playing from ${formatTime(target)}.`;
    };

    instance.jumpToTimestamp = jumpToTimestamp;
    audio.addEventListener("loadedmetadata", () => {
      instance.status.textContent = `Audio ready — ${formatTime(audio.duration)}.`;
      if (instance.pendingSeconds === null) return;
      const seconds = instance.pendingSeconds;
      instance.pendingSeconds = null;
      jumpToTimestamp(seconds);
    });
    audio.addEventListener("loadstart", () => {
      instance.status.textContent = "Loading audio…";
    });
    audio.addEventListener("canplay", () => {
      if (audio.paused) {
        instance.status.textContent = `Ready to play — ${formatTime(audio.duration)}.`;
      }
    });
    audio.addEventListener("error", () => {
      const code = audio.error?.code;
      const messages = {
        2: "Audio download failed.",
        3: "The audio file could not be decoded.",
        4: "This audio format is not supported.",
      };
      status.textContent =
        `${messages[code] || "The audio could not be loaded."} Use Reload audio to try again.`;
    });
    audio.addEventListener("playing", () => {
      instance.status.textContent = `Playing — ${formatTime(audio.currentTime)} of ${formatTime(audio.duration)}.`;
    });
    audio.addEventListener("timeupdate", () => {
      if (!audio.paused) {
        instance.status.textContent = `Playing — ${formatTime(audio.currentTime)} of ${formatTime(audio.duration)}.`;
      }
    });
    audio.addEventListener("pause", () => {
      if (audio.readyState >= HTMLMediaElement.HAVE_METADATA) {
        instance.status.textContent = `Paused — ${formatTime(audio.currentTime)} of ${formatTime(audio.duration)}.`;
      }
    });
    reloadButton.addEventListener("click", () => {
      instance.pendingSeconds = null;
      audio.pause();
      audio.currentTime = 0;
      audio.load();
      instance.status.textContent = "Reloading audio…";
    });
  }

  if (data.src && instance.source !== data.src) {
    instance.source = data.src;
    instance.pendingSeconds = null;
    instance.audioRef.current.src = data.src;
    instance.audioRef.current.load();
    instance.status.textContent = "Loading audio…";
  }

  if (data.seekId && data.seekId !== instance.seekId) {
    instance.seekId = data.seekId;
    instance.jumpToTimestamp(data.seekSeconds);
  }
}

function formatTime(seconds) {
  const value = Math.floor(Math.max(0, seconds));
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}
"""

AUDIO_PLAYER_COMPONENT = st.components.v2.component(
    "audio_proof_player",
    html=_PLAYER_HTML,
    css=_PLAYER_CSS,
    js=_PLAYER_JS,
)


@st.cache_data(show_spinner=False)
def audio_file_data_uri(audio_path: str) -> str:
    path = Path(audio_path)
    if path.suffix.lower() != ".mp3" or not path.is_file():
        raise FileNotFoundError(f"MP3 audio file not found: {path}")
    encoded_audio = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:audio/mpeg;base64,{encoded_audio}"
