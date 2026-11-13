import { state } from "./state.js";
import { ageText, element, panelTitle } from "./core.js";
import { freshnessLabel } from "./freshness.js";
import { textInfoButton } from "./info.js";

/** Returns what each feed state means, for the legend of the feed status panel. */
function feedStateLines(overdueFactor) {
  return [
    "The fetch column rates the newest fetch of each kind of data; the newest value column how old its newest measurement is.",
    "ok: the last fetch got everything it asked for.",
    "gaps: the last fetch finished, but parts of the time span could not be fetched; the next fetch tries them again.",
    "failed: the last fetch stopped with an error.",
    `overdue: no new fetch started for ${overdueFactor} times the usual interval.`,
    "running: a fetch is in progress.",
    "pending: a fetch waits for its turn.",
  ];
}

// Order of the feed states in the list, problems first
const FEED_STATE_ORDER = { failed: 0, overdue: 1, gaps: 2, running: 3, pending: 4, ok: 5 };

/** Returns the position of each dataset on the dashboard, by its first panel from the first page on: the way from the Sun to the Earth. */
function datasetOrder() {
  const order = new Map();
  for (const page of state.layout.pages) {
    for (const entry of page.rows.flat()) {
      for (const about of state.payloadsById[entry.id]?.about ?? []) {
        if (!order.has(about.dataset)) order.set(about.dataset, order.size);
      }
    }
  }
  return order;
}

/** Sorts the datasets with problems first, then by where they first appear on the dashboard, unshown ones last by title. */
function sortedStatuses(statuses) {
  const order = datasetOrder();
  return [...statuses].sort(
    (first, second) =>
      // An unknown state sorts after the known ones; || moves on to the next key while two statuses are equal
      (FEED_STATE_ORDER[first.state] ?? 9) - (FEED_STATE_ORDER[second.state] ?? 9) ||
      (order.get(first.dataset) ?? Infinity) - (order.get(second.dataset) ?? Infinity) ||
      first.title.localeCompare(second.title),
  );
}

/** Draws the status of every fetched dataset as a table: one row per dataset with its state, its newest value and when it was fetched. */
export function renderFeeds(panel, payload) {
  const heading = panelTitle(payload.title);
  heading.append(textInfoButton("States of the data updates", feedStateLines(payload.overdue_factor)));
  panel.append(heading);
  const scroll = element("div", "scroll");
  if (payload.datasets.length === 0) {
    scroll.append(element("p", "note", "Nothing fetched yet."));
    panel.append(scroll);
    return;
  }
  const table = element("table", "updates");
  const header = element("tr");
  for (const column of ["Data", "Fetch", "Newest value", "Last fetched"]) header.append(element("th", "", column));
  // tHead and tBodies give the sections the table holds
  table.append(element("thead"), element("tbody"));
  table.tHead.append(header);
  const now = Date.now();
  for (const status of sortedStatuses(payload.datasets)) {
    const line = element("tr");
    const name = element("td");
    name.append(status.title);
    if (status.problem) name.append(element("div", "caption problem", status.problem));
    const newest = element("td");
    if (status.freshness) newest.append(freshnessLabel([status.freshness]));
    else newest.append("none yet");
    const schedule = status.interval === null ? "once at start" : `every ${ageText(status.interval)}`;
    line.append(name, element("td", `state-${status.state}`, status.state), newest, element("td", "", `${ageText((now - Date.parse(status.last_run)) / 1000)} ago, ${schedule}`));
    table.tBodies[0].append(line);
  }
  scroll.append(table);
  panel.append(scroll);
}
