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
  grid-template-columns: minmax(0, 1fr);
  gap: 0.75rem;
  color: var(--st-text-color, #111827);
  font-family: var(--st-font, sans-serif);
}
#proof-card-fields,
#proof-action-items {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}
#proof-card-fields,
#proof-action-items { grid-column: 1 / -1; }
#proof-action-items > .proof-heading { grid-column: 1 / -1; }
#proof-action-items > .proof-actions-list { grid-column: 1 / -1; }
.proof-actions-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
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
.confidence-badge {
  display: inline-flex;
  align-items: center;
  margin-bottom: 0.55rem;
  border-radius: 999px;
  padding: 0.2rem 0.55rem;
  font-size: 0.76rem;
  font-weight: 650;
}
.confidence-badge.verified {
  color: #166534;
  background: #dcfce7;
}
.confidence-badge.needs-review {
  color: #92400e;
  background: #fef3c7;
}
.proof-review-queue {
  grid-column: 1 / -1;
  display: grid;
  gap: 0.75rem;
  border: 1px solid #f59e0b99;
  border-radius: 0.75rem;
  padding: 0.85rem;
  background: #f59e0b14;
}
.proof-review-queue .proof-heading {
  grid-column: 1 / -1;
  margin: 0;
}
.proof-review-item { border-color: #f59e0b88; }
.proof-review-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.65rem;
}
.proof-review-controls button {
  border: 1px solid var(--st-border-color, #d1d5db);
  border-radius: 0.45rem;
  padding: 0.35rem 0.65rem;
  background: transparent;
  color: var(--st-text-color, #111827);
  font: inherit;
  cursor: pointer;
  transition: background-color 150ms ease, transform 150ms ease;
}
.proof-review-controls button:hover { background: #f59e0b33; }
.proof-review-controls button:active { transform: scale(0.97); }
@media (max-width: 700px) {
  #proof-card-fields,
  #proof-action-items,
  .proof-actions-list { grid-template-columns: minmax(0, 1fr); }
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
  instance.actions.classList.add("proof-actions-list");

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

  const items = (data.card?.action_items || []).map((item, index) => {
    const hasConfidence = typeof item.confidence === "number"
      && Number.isFinite(item.confidence);
    const hasQuote = typeof item.quote === "string" && item.quote.trim().length > 0;
    const useLegacyFallback = item.traceability_scored !== true
      && (!hasConfidence || !hasQuote);
    const confidence = useLegacyFallback
      ? 0.8
      : hasConfidence
        ? Math.min(1, Math.max(0, item.confidence))
        : 0.8;
    const status = item.status === "dismissed"
      ? "dismissed"
      : item.human_approved || useLegacyFallback
        ? "verified"
        : item.status || (confidence >= 0.7 ? "verified" : "needs_review");
    return { ...item, confidence, status, index };
  }).filter(item => item.status !== "dismissed");
  const verifiedItems = items.filter(
    item => item.status === "verified" || item.confidence >= 0.7,
  );
  const reviewItems = items.filter(
    item => item.status === "needs_review" && item.confidence < 0.7,
  );

  appendActionSection(
    instance.actions,
    "Verified Action Items (Automated)",
    verifiedItems,
    false,
    instance,
  );

  const reviewQueue = document.createElement("section");
  reviewQueue.className = "proof-review-queue";
  const reviewHeading = document.createElement("h4");
  reviewHeading.className = "proof-heading";
  reviewHeading.textContent = "Human-in-the-Loop Review Queue (Safety Net)";
  reviewQueue.append(reviewHeading);
  if (reviewItems.length === 0) {
    const empty = document.createElement("p");
    empty.className = "proof-empty";
    empty.textContent = "No action items need review.";
    reviewQueue.append(empty);
  }
  reviewItems.forEach(item => {
    reviewQueue.append(createActionCard(item, true, instance));
  });
  instance.actions.append(reviewQueue);
}

function appendActionSection(parent, title, items, needsReview, instance) {
  const heading = document.createElement("h4");
  heading.className = "proof-heading";
  heading.textContent = title;
  parent.append(heading);
  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.className = "proof-empty";
    empty.textContent = "None";
    parent.append(empty);
    return;
  }
  items.forEach(item => {
    parent.append(createActionCard(item, needsReview, instance));
  });
}

function createActionCard(item, needsReview, instance) {
  const text = item.text || item.action_needed || "Action item";
  const card = createCard(text, `Owner: ${item.owner_team || "Not available"}`);
  if (needsReview) card.classList.add("proof-review-item");

  const badge = document.createElement("span");
  badge.className = `confidence-badge ${needsReview ? "needs-review" : "verified"}`;
  const confidence = `${Math.round(item.confidence * 100)}% Confident`;
  badge.textContent = needsReview
    ? `${confidence} - Needs Review`
    : item.human_approved && item.confidence < 0.7
      ? `${confidence} - Human approved`
      : confidence;
  card.insertBefore(badge, card.firstChild);

  const quote = typeof item.quote === "string" ? item.quote.trim() : "";
  const timestamp = parseTimestamp(item.timestamp_sec ?? item.timestamp) ?? 0;
  appendProofButton(card, {
    id: `action-${item.index}`,
    quote,
    timestamp,
    timestampFallback: 0,
  }, instance);
  const proofButton = card.querySelector(".proof-button");
  if (quote && quote.toLowerCase() !== "none") {
    proofButton.title = `Supporting transcript quote: ${quote}`;
  }

  if (needsReview) {
    const controls = document.createElement("div");
    controls.className = "proof-review-controls";
    controls.append(
      createReviewButton("Approve", "approve", item.index, instance),
      createReviewButton("Dismiss", "dismiss", item.index, instance),
    );
    card.append(controls);
  }
  return card;
}

function createReviewButton(label, action, itemIndex, instance) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", () => {
    instance.setTriggerValue("action_review", {
      action,
      itemIndex,
      requestId: crypto.randomUUID(),
    });
  });
  return button;
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
