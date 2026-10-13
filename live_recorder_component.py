"""Streamlit custom component for recording and uploading microphone chunks."""

import streamlit as st

_COMPONENT_HTML = """
<section class="live-card" aria-label="Live audio recorder">
  <button id="live-toggle" type="button">🎙️ Simulate Live Call</button>
  <p id="live-status" role="status" aria-live="polite">
    Microphone is off. Start a live call to begin sending audio.
  </p>
  <p id="live-count" class="live-count">0 audio chunks sent</p>
</section>
"""

_COMPONENT_CSS = """
.live-card {
  border: 1px solid var(--st-border-color, #d1d5db);
  border-radius: 0.75rem;
  padding: 1rem;
  background: var(--st-secondary-background-color, #f8fafc);
  color: var(--st-text-color, #111827);
  font-family: var(--st-font, sans-serif);
}
#live-toggle {
  border: 0;
  border-radius: 0.5rem;
  padding: 0.65rem 1rem;
  font: inherit;
  font-weight: 600;
  color: white;
  background: #2563eb;
  cursor: pointer;
}
#live-toggle:hover { background: #1d4ed8; }
#live-toggle:focus-visible {
  outline: 3px solid #93c5fd;
  outline-offset: 2px;
}
#live-status { margin: 0.75rem 0 0.25rem; }
.live-count {
  margin: 0;
  color: var(--st-secondary-text-color, #6b7280);
  font-size: 0.875rem;
}
"""

_COMPONENT_JS = """
const instances = new WeakMap();

export default function (component) {
  const { parentElement, data, setTriggerValue } = component;
  let instance = instances.get(parentElement);

  if (!instance) {
    const button = parentElement.querySelector("#live-toggle");
    const status = parentElement.querySelector("#live-status");
    const count = parentElement.querySelector("#live-count");
    if (!button || !status || !count) return;

    instance = {
      button,
      status,
      count,
      isRecording: false,
      isStopping: false,
      uploadedChunks: 0,
      recorder: null,
      stream: null,
      recordingId: null,
      chunkIndex: 0,
      uploadQueue: Promise.resolve(),
      uploadError: null,
      finishPromise: null,
      audioContentType: "",
    };
    instances.set(parentElement, instance);

    const update = (message) => {
      instance.button.textContent = instance.isRecording
        ? "🔴 Stop Live Call"
        : "🎙️ Simulate Live Call";
      instance.button.disabled = instance.isStopping;
      instance.status.textContent = message;
      instance.count.textContent =
        `${instance.uploadedChunks} audio chunk${instance.uploadedChunks === 1 ? "" : "s"} sent`;
    };

    const finishRecording = () => {
      if (instance.finishPromise) return instance.finishPromise;
      if (!instance.recordingId) return Promise.resolve();

      instance.finishPromise = (async () => {
        const recordingId = instance.recordingId;
        try {
          await instance.uploadQueue;
          if (instance.uploadError) {
            await fetch(data.endpoint, {
              method: "POST",
              headers: {
                "X-Recording-ID": recordingId,
                "X-Recording-Aborted": "true",
              },
            });
            throw instance.uploadError;
          }
          const response = await fetch(data.endpoint, {
            method: "POST",
            headers: {
              "X-Recording-ID": recordingId,
              "X-Recording-Finished": "true",
              "X-Audio-Content-Type": instance.audioContentType,
            },
          });
          if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            throw new Error(result.error || `Server returned HTTP ${response.status}`);
          }
          const result = await response.json();
          setTriggerValue("completed", {
            recordingId: result.recording_id,
            contentType: instance.audioContentType,
          });
        } catch (error) {
          instance.uploadError = error;
        } finally {
          if (instance.stream) {
            instance.stream.getTracks().forEach((track) => track.stop());
          }
          instance.stream = null;
          instance.recorder = null;
          instance.recordingId = null;
          instance.isRecording = false;
          instance.isStopping = false;
          instance.finishPromise = null;
          const message = instance.uploadError
            ? `Live call stopped. ${instance.uploadError.message}`
            : "Recording stopped. Generating the summary…";
          update(message);
        }
      })();
      return instance.finishPromise;
    };

    instance.button.addEventListener("click", async () => {
      if (instance.isStopping) return;
      if (instance.isRecording) {
        instance.isStopping = true;
        instance.status.textContent = "Stopping and clearing audio from server memory…";
        instance.button.disabled = true;
        if (instance.recorder && instance.recorder.state !== "inactive") {
          instance.recorder.stop();
        } else {
          await finishRecording();
        }
        return;
      }

      instance.uploadedChunks = 0;
      instance.chunkIndex = 0;
      instance.uploadQueue = Promise.resolve();
      instance.uploadError = null;
      instance.finishPromise = null;
      instance.audioContentType = "";
      try {
        if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
          throw new Error("This browser does not support microphone recording.");
        }
        instance.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const formats = [
          "audio/webm;codecs=opus",
          "audio/ogg;codecs=opus",
          "audio/mp4",
        ];
        const mimeType = formats.find((format) => MediaRecorder.isTypeSupported(format));
        instance.recorder = new MediaRecorder(
          instance.stream,
          mimeType ? { mimeType } : undefined,
        );
        instance.audioContentType =
          instance.recorder.mimeType.split(";", 1)[0].toLowerCase();
        instance.recordingId = crypto.randomUUID();
        instance.isRecording = true;
        update("Recording from your microphone. Audio is sent every 5 seconds.");

        instance.recorder.addEventListener("dataavailable", (event) => {
          if (!event.data || event.data.size === 0) return;
          const index = instance.chunkIndex++;
          const recordingId = instance.recordingId;
          const contentType = event.data.type || instance.recorder.mimeType;
          instance.uploadQueue = instance.uploadQueue.then(async () => {
            if (instance.uploadError) return;
            const response = await fetch(data.endpoint, {
              method: "POST",
              headers: {
                "Content-Type": contentType,
                "X-Recording-ID": recordingId,
                "X-Chunk-Index": String(index),
              },
              body: event.data,
            });
            if (!response.ok) {
              const result = await response.json().catch(() => ({}));
              throw new Error(result.error || `Server returned HTTP ${response.status}`);
            }
            instance.uploadedChunks += 1;
            update("Recording from your microphone. Audio is sent every 5 seconds.");
          }).catch((error) => {
            instance.uploadError = error;
            if (instance.recorder && instance.recorder.state !== "inactive") {
              instance.recorder.stop();
            }
          });
        });
        instance.recorder.addEventListener("stop", finishRecording, { once: true });
        instance.recorder.addEventListener("error", () => {
          instance.uploadError = new Error("The browser could not record microphone audio.");
          if (instance.recorder && instance.recorder.state !== "inactive") {
            instance.recorder.stop();
          } else {
            finishRecording();
          }
        }, { once: true });
        instance.recorder.start(5000);
      } catch (error) {
        if (instance.stream) {
          instance.stream.getTracks().forEach((track) => track.stop());
        }
        instance.stream = null;
        instance.recorder = null;
        instance.recordingId = null;
        instance.isRecording = false;
        update(`Could not start the live call. ${error.message}`);
      }
    });
  }
}
"""

LIVE_STREAM_COMPONENT = st.components.v2.component(
    "live_audio_stream",
    html=_COMPONENT_HTML,
    css=_COMPONENT_CSS,
    js=_COMPONENT_JS,
)
