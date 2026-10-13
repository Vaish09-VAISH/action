"""Audio player component that can seek to evidence timestamps."""

import streamlit as st

_PLAYER_HTML = """
<section class="audio-proof-player" aria-label="Call audio player">
  <audio id="proof-audio" controls preload="metadata"></audio>
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
    const status = parentElement.querySelector("#proof-audio-status");
    if (!audio || !status) return;

    instance = {
      audioRef: { current: audio },
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
      if (instance.pendingSeconds === null) return;
      const seconds = instance.pendingSeconds;
      instance.pendingSeconds = null;
      jumpToTimestamp(seconds);
    });
    audio.addEventListener("error", () => {
      status.textContent = "The audio could not be loaded.";
    });
    audio.addEventListener("playing", () => {
      if (instance.pendingSeconds === null) status.textContent = "";
    });
  }

  if (data.src && instance.source !== data.src) {
    instance.source = data.src;
    instance.pendingSeconds = null;
    instance.audioRef.current.src = data.src;
    instance.audioRef.current.load();
    instance.status.textContent = "";
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
