import { state } from "./state.js";
import { ageText, element } from "./core.js";

// Order from best to worst, so that a panel with several datasets shows the worst state
const SEVERITY = { static: 0, live: 1, delayed: 2, stale: 3 };

/**
 * Rates the newest value of one dataset against the current time.
 * @param {{newest: string, expected_age: number|null}} item The freshness of the dataset from the payload.
 * @param {number} now The current time in milliseconds.
 * @returns {string} live up to the expected age, delayed up to delayed_factor times it, else stale; static without expected age.
 */
function rate(item, now) {
  if (item.expected_age === null) return "static";
  // Age and expected age in seconds
  const age = (now - Date.parse(item.newest)) / 1000;
  if (age <= item.expected_age) return "live";
  return age <= item.expected_age * state.layout.delayed_factor ? "delayed" : "stale";
}

/** Writes the worst state of the datasets of a panel with the age of the newest value of that dataset into a label. */
export function updateFreshness(label, items) {
  const now = Date.now();
  let worst = null;
  for (const item of items) {
    const state = rate(item, now);
    if (worst === null || SEVERITY[state] > SEVERITY[worst.state]) worst = { state, item };
  }
  label.replaceChildren();
  if (worst === null) return;
  label.append(element("span", `state-${worst.state}`, worst.state), `, newest value ${ageText((now - Date.parse(worst.item.newest)) / 1000)} ago`);
}

/** Creates a freshness label that the timer keeps current. */
export function freshnessLabel(items) {
  const label = element("span");
  state.freshnessLabels.push({ panel: state.drawingPanel.id, label, items });
  updateFreshness(label, items);
  return label;
}

/** Creates a caption holding the freshness of the newest value of a panel; empty for a panel without freshness. */
export function freshnessCaption(items) {
  const caption = element("p", "caption");
  if (items && items.length > 0) caption.append(freshnessLabel(items));
  return caption;
}
