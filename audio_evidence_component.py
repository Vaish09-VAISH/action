"""Clickable audio-proof buttons for summary fields and action items."""

import streamlit as st

_EVIDENCE_HTML = """
<section class="audio-proof-cards" aria-label="Summary audio evidence">
  <div id="proof-card-fields"></div>
  <div id="proof-action-items"></div>
</section>
"""

_EVIDENCE_CSS = """
.audio-proof-cards {
  display: grid;
  gap: 0.75rem;
  color: var(--st-text-color, #111827);
  font-family: var(--st-font, sans-serif);
}
.proof-card {
  border: 1px solid var(--st-border-color, #d1d5db);
  border-radius: 0.75rem;
  padding: 0.85rem 1rem;
  background: var(--st-secondary-background-color, #f8fafc);
}
.proof-heading {
  margin: 0 0 0.5rem;
  font-size: 0.9rem;
  font-weight: 650;
}
.proof-value {
  margin: 0 0 0.65rem;
  line-height: 1.45;
}
.proof-button {
  display: flex;
  width: 100%;
  align-items: flex-start;
  gap: 0.6rem;
  border: 1px solid var(--st-border-color, #d1d5db);
  border-radius: 0.55rem;
  padding: 0.55rem 0.7rem;
  color: var(--st-text-color, #111827);
  background: transparent;
  text-align: left;
  font: inherit;
  cursor: pointer;
  transition: background-color 150ms ease, transform 150ms ease, box-shadow 150ms ease;
}
.proof-button:not(:disabled):hover { background: #33415555; }
.proof-button:not(:disabled):active { transform: scale(0.98); }
.proof-button:focus-visible {
  outline: 2px solid var(--st-primary-color, #2563eb);
  outline-offset: 2px;
}
.proof-button:disabled { cursor: not-allowed; opacity: 0.65; }
.proof-button.active-proof {
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--st-primary-color, #2563eb) 35%, transparent);
}
.proof-time {
  flex: 0 0 auto;
  color: var(--st-primary-color, #2563eb);
  font-size: 0.82rem;
  font-weight: 650;
  white-space: nowrap;
}
.proof-quote { line-height: 1.4; }
.proof-empty {
  margin: 0;
  color: var(--st-secondary-text-color, #6b7280);
  font-size: 0.9rem;
}
"""

_EVIDENCE_JS = """
const instances = new WeakMap();
const FIELDS = [
  ["summary", "Summary"],
  ["sentiment", "Mood"],
  ["urgency", "Urgency"],
  ["emergency", "Emergency"],
];

export default function (component) {
  const { parentElement, data, setTriggerValue } = component;
  let instance = instances.get(parentElement);

  if (!instance) {
    instance = {
      fields: parentElement.querySelector("#proof-card-fields"),
      actions: parentElement.querySelector("#proof-action-items"),
      setTriggerValue,
      activeId: null,
      highlightTimer: null,
    };
    if (!instance.fields || !instance.actions) return;
    instances.set(parentElement, instance);
  } else {
    instance.setTriggerValue = setTriggerValue;
  }

  instance.activeId = data.activeId || null;
  instance.fields.replaceChildren();
  instance.actions.replaceChildren();

  for (const [key, label] of FIELDS) {
    const value = key === "summary"
      ? data.card?.summary
      : key === "sentiment"
        ? data.card?.sentiment
        : key === "urgency"
          ? data.card?.urgency
          : String(Boolean(data.card?.emergency_flag));
    const evidence = data.card?.evidence?.[key] || {};
    const card = createCard(label, value || "Not available");
    appendProofButton(card, {
      id: `field-${key}`,
      quote: evidence.quote,
      timestamp: evidence.timestamp,
    }, instance);
    instance.fields.append(card);
  }

  const actionHeading = document.createElement("h4");
  actionHeading.className = "proof-heading";
  actionHeading.textContent = "Action Items";
  instance.actions.append(actionHeading);

  const items = data.card?.action_items || [];
  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.className = "proof-empty";
    empty.textContent = "None";
    instance.actions.append(empty);
  }
  items.forEach((item, index) => {
    const card = createCard(
      item.action_needed || `Action item ${index + 1}`,
      `Owner: ${item.owner_team || "Not available"}`,
    );
    appendProofButton(card, {
      id: `action-${index}`,
      quote: item.quote,
      timestamp: item.timestamp,
    }, instance);
    instance.actions.append(card);
  });

  const reviews = data.review || [];
  reviews.forEach((item, index) => {
    const card = createCard(
      item.action_needed || "Needs a person",
      item.reason || "Review required",
    );
    appendProofButton(card, {
      id: `review-${index}`,
      quote: item.quote,
      timestamp: item.timestamp,
    }, instance);
    instance.actions.append(card);
  });
}

function createCard(headingText, valueText) {
  const card = document.createElement("article");
  card.className = "proof-card";
  const heading = document.createElement("h4");
  heading.className = "proof-heading";
  heading.textContent = headingText;
  const value = document.createElement("p");
  value.className = "proof-value";
  value.textContent = valueText;
  card.append(heading, value);
  return card;
}

function appendProofButton(card, evidence, instance) {
  const quote = typeof evidence.quote === "string" ? evidence.quote.trim() : "";
  const seconds = parseTimestamp(evidence.timestamp);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "proof-button";
  button.disabled = !quote || quote.toLowerCase() === "none" || seconds === null;

  const timestamp = document.createElement("span");
  timestamp.className = "proof-time";
  timestamp.textContent = `🔊 [${formatTime(seconds ?? 0)}]`;
  const quoteText = document.createElement("span");
  quoteText.className = "proof-quote";
  quoteText.textContent = quote && quote.toLowerCase() !== "none"
    ? quote
    : "No timestamped quote available";
  button.append(timestamp, quoteText);

  const id = evidence.id;
  if (id && id === instance.activeId) button.classList.add("active-proof");
  button.addEventListener("click", () => {
    if (button.disabled) return;
    instance.activeId = id;
    for (const active of card.parentElement.querySelectorAll(".active-proof")) {
      active.classList.remove("active-proof");
    }
    button.classList.add("active-proof");
    clearTimeout(instance.highlightTimer);
    instance.highlightTimer = setTimeout(
      () => button.classList.remove("active-proof"),
      1200,
    );
    instance.setTriggerValue("jump", {
      id,
      seconds,
      requestId: crypto.randomUUID(),
    });
  });
  card.append(button);
}

function parseTimestamp(value) {
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) return value;
  if (typeof value !== "string" || !value.trim()) return null;
  const input = value.trim();
  if (/^\\d+(?:\\.\\d+)?$/.test(input)) return Number(input);
  const match = /^(\\d{1,2}):(\\d{2})$/.exec(input);
  if (!match || Number(match[2]) > 59) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function formatTime(seconds) {
  const value = Math.floor(Math.max(0, seconds));
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}
"""

AUDIO_EVIDENCE_COMPONENT = st.components.v2.component(
    "audio_evidence_cards",
    html=_EVIDENCE_HTML,
    css=_EVIDENCE_CSS,
    js=_EVIDENCE_JS,
)
