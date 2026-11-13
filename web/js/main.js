import { state } from "./state.js";
import { element, link, linkedText, loadJson, panelTitle, utcText } from "./core.js";
import { updateFreshness } from "./freshness.js";
import { computedQuantities, infoButton, textInfoButton } from "./info.js";
import { REPLAY_MAX_VIEW_S, replayBar, setReplayTime } from "./replay.js";
import { renderValue } from "./value.js";
import { renderTimeseries } from "./stack.js";
import { renderFeeds } from "./feeds.js";
import { renderImages } from "./images.js";
import { renderSunDisk } from "./sundisk.js";
import { renderChains, renderMessages } from "./events.js";
import { renderButterfly, renderEpoch, renderGrouped, renderSolarCycle } from "./history.js";
import { releaseThreeViewers } from "./viewer3d.js";
import { renderFieldLines, renderFieldMap } from "./field.js";
import { renderHeliosphere } from "./heliosphere.js";
import { renderMagneticConnection } from "./magnetic_connection.js";
import { renderAurora } from "./aurora.js";

// Drawing function of each panel type, by the type name of the payload
const RENDERERS = {
  value: renderValue,
  stack: renderTimeseries,
  feeds: renderFeeds,
  images: renderImages,
  messages: renderMessages,
  epoch: renderEpoch,
  chains: renderChains,
  sundisk: renderSunDisk,
  butterfly: renderButterfly,
  fieldlines: renderFieldLines,
  fieldmap: renderFieldMap,
  heliosphere: renderHeliosphere,
  magnetic_connection: renderMagneticConnection,
  solarcycle: renderSolarCycle,
  grouped: renderGrouped,
  aurora: renderAurora,
};

/** Links a Creative Commons license name to its deed; other license texts stay plain. */
function licenseLink(source) {
  // "CC BY", any number of license elements such as -SA or -NC-ND, each a hyphen and two capitals (group 1), a space
  // and the version as digit, dot, digit (group 2)
  const match = source.match(/CC (BY(?:-[A-Z]{2})*) (\d\.\d)/);
  if (match === null) return null;
  return link(`https://creativecommons.org/licenses/${match[1].toLowerCase()}/${match[2]}/`, "license");
}

/** Lists the sources and formulas of the dashboard, numbered as the footnotes of the panels refer to them. */
function renderCredits(credits) {
  const list = document.getElementById("credits");
  list.replaceChildren();
  credits.forEach((credit, index) => {
    const item = element("li");
    item.id = `credit-${index + 1}`;
    item.append(linkedText(credit.source));
    const license = licenseLink(credit.source);
    if (license !== null) item.append(" (", license, ")");
    for (const text of [credit.acknowledgement, credit.citation]) {
      if (!text) continue;
      const line = element("div");
      line.append(linkedText(text));
      item.append(line);
    }
    list.append(item);
  });
}

/** Opens the sources section and scrolls to one source, marking it for a moment. */
function showCredit(number) {
  document.getElementById("sources").open = true;
  const item = document.getElementById(`credit-${number}`);
  item.scrollIntoView({ behavior: "smooth", block: "center" });
  item.classList.add("highlight");
  // The mark fades after 2.5 seconds, once the smooth scroll has ended
  setTimeout(() => item.classList.remove("highlight"), 2500);
}

/** Creates the footnote numbers of a panel, each leading to its source in the sources section. */
function footnotes(credits) {
  const notes = element("sup", "footnotes");
  credits.forEach((position, index) => {
    const number = position + 1;
    const anchor = element("a", "", String(number));
    // The fragment of the address selects the page, so the footnote scrolls instead of navigating
    anchor.href = `#credit-${number}`;
    anchor.addEventListener("click", (event) => {
      event.preventDefault();
      showCredit(number);
    });
    if (index > 0) notes.append(",");
    notes.append(anchor);
  });
  return notes;
}

/** Turns a page title into the URL fragment that selects its tab. */
function pageSlug(title) {
  // Every run of characters other than lowercase letters and digits becomes one hyphen; a hyphen at the start or the
  // end is dropped
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

/** Returns the page named by the URL fragment, else the first page. */
function selectedPage() {
  const slug = window.location.hash.slice(1);
  return state.layout.pages.find((page) => pageSlug(page.title) === slug) ?? state.layout.pages[0];
}

/** Draws the tabs of the pages, marking the selected one; a dashboard of one page gets no tabs. */
function renderTabs(selected) {
  const tabs = document.getElementById("pages");
  tabs.replaceChildren();
  tabs.hidden = state.layout.pages.length < 2;
  for (const page of state.layout.pages) {
    const tab = element("a", "tab", page.title);
    tab.href = `#${pageSlug(page.title)}`;
    if (page === selected) tab.setAttribute("aria-current", "page");
    tabs.append(tab);
  }
}

// The panels of the shown page with their layout entries, and its replay bar; null without replay
let shownPanels = [];
let replayBarElement = null;

/** Drops the freshness labels and replay listeners of one panel before it is drawn again. */
function forgetPanel(id) {
  state.freshnessLabels = state.freshnessLabels.filter((label) => label.panel !== id);
  state.replayListeners = state.replayListeners.filter((listener) => listener.panel !== id);
}

/**
 * Draws one panel into its element; the element keeps its height meanwhile, so that the page below does not jump.
 * @param {object} entry The layout entry of the panel.
 * @param {HTMLElement} panel The element of the panel.
 */
function renderPanel(entry, panel) {
  forgetPanel(entry.id);
  if (panel.offsetHeight > 0) panel.style.minHeight = `${panel.offsetHeight}px`;
  panel.replaceChildren();
  // The hooks a renderer sets on its panel element belong to the previous drawing
  delete panel.replaySpan;
  delete panel.isPlaying;
  const payload = state.payloadsById[entry.id];
  const render = RENDERERS[entry.type];
  if (payload.error !== undefined || render === undefined) {
    panel.append(element("p", "note", payload.error !== undefined ? `Panel not available: ${payload.error.message}` : `Unknown panel type ${entry.type}`));
  } else {
    state.drawingPanel = { id: entry.id, charts: 0 };
    try {
      render(panel, payload);
    } catch (error) {
      // A panel that fails to draw shows a note, so that the panels after it are still drawn
      console.error(`Panel ${entry.id} could not be drawn`, error);
      panel.replaceChildren(element("p", "note", "Panel could not be drawn."));
    }
    const heading = panel.querySelector(".panel-title") ?? panel.insertBefore(panelTitle(""), panel.firstChild);
    if (entry.credits.length > 0) heading.append(footnotes(entry.credits));
    if (payload.about.length > 0 || computedQuantities(payload).length > 0) heading.append(infoButton(payload));
  }
  // The held height is released in the next frame, once the new content is laid out
  requestAnimationFrame(() => (panel.style.minHeight = ""));
}

/**
 * Returns the span the replay of the shown page runs over: the span of its images, so that they move from the first
 * step; on pages without images the span of its charts of short views. Null for a page without either.
 */
function pageReplaySpan() {
  // Panels with image sequences set replaySpan to the first and last time of their frames
  const imageSpans = shownPanels.filter(({ panel }) => panel.replaySpan).map(({ panel }) => panel.replaySpan);
  // Charts of long histories are not replayed; a replay of years would pass too fast to follow
  const chartSpans = shownPanels
    .map(({ entry }) => ({ entry, payload: state.payloadsById[entry.id] }))
    .filter(({ entry, payload }) => entry.type === "stack" && payload.error === undefined && payload.view <= REPLAY_MAX_VIEW_S)
    .map(({ payload }) => [Date.now() - payload.view * 1000, Date.now()]);
  const spans = imageSpans.length > 0 ? imageSpans : chartSpans;
  // The replay covers the union of all spans: from the earliest start to the latest end
  return spans.length > 0 ? [Math.min(...spans.map((pair) => pair[0])), Math.max(...spans.map((pair) => pair[1]))] : null;
}

/**
 * Draws the selected page row by row from the loaded panel documents.
 * @param {boolean} keep Whether the shown page is drawn again in place, in other colors or with a new arrangement:
 *   charts keep zoom and pan, the page its scroll position and the replay its time. Otherwise a newly selected page
 *   starts with the newest data.
 */
function renderPage(keep = false) {
  const selected = selectedPage();
  const scroll = window.scrollY;
  clearInterval(state.replayTimer);
  state.replayTimer = null;
  state.videoTimers.forEach(clearInterval);
  state.videoTimers = [];
  if (!keep) {
    // Charts of the previous page are released, as a newly selected page draws its own
    state.chartsByKey.forEach((chart) => Plotly.purge(chart));
    state.chartsByKey = new Map();
    releaseThreeViewers();
    state.replayTime = null;
  }
  renderTabs(selected);
  state.freshnessLabels = [];
  state.replayListeners = [];
  shownPanels = [];
  const dashboard = document.getElementById("dashboard");
  dashboard.replaceChildren();
  for (const row of selected.rows) {
    const line = element("div", "row");
    let previous = null;
    for (const entry of row) {
      const panel = element("section", `panel panel-${entry.type}`);
      panel.style.flexGrow = entry.width;
      if (entry.below && previous !== null) {
        // A panel below the previous one shares its column, which takes over the width of that panel
        let column = previous.parentElement;
        if (!column.classList.contains("column")) {
          column = element("div", "column");
          column.style.flexGrow = previous.style.flexGrow;
          previous.replaceWith(column);
          column.append(previous);
        }
        column.append(panel);
      } else {
        line.append(panel);
      }
      previous = panel;
      renderPanel(entry, panel);
      shownPanels.push({ entry, panel });
    }
    dashboard.append(line);
  }
  const span = pageReplaySpan();
  replayBarElement = null;
  if (span !== null) {
    // A kept replay time is held inside the span of the newly drawn page
    if (state.replayTime !== null) state.replayTime = Math.min(Math.max(state.replayTime, span[0]), span[1]);
    replayBarElement = replayBar(span);
    dashboard.prepend(replayBarElement);
    if (state.replayTime !== null) setReplayTime(state.replayTime);
  }
  if (keep) window.scrollTo(0, scroll);
}

/** Reads the documents of every panel of every page into payloadsById. */
async function loadPayloads() {
  const entries = state.layout.pages.flatMap((page) => page.rows.flat());
  // A document that cannot be read is kept as its error, which its panel shows instead of the page failing
  const payloads = await Promise.all(entries.map((entry) => loadJson(`panels/${entry.id}.json`).catch((error) => ({ error }))));
  state.payloadsById = Object.fromEntries(entries.map((entry, index) => [entry.id, payloads[index]]));
}

/** Writes when the documents were built and the legend of the freshness labels. */
function renderBuildInfo() {
  const info = document.getElementById("build-info");
  info.replaceChildren(`Updated ${utcText(state.layout.generated_at)} UTC `);
  info.append(
    textInfoButton("What live, delayed and stale mean", [
      "Each panel rates the newest value of its data against your clock.",
      "The expected age of a dataset is the interval in which it is fetched, plus the time between two of its measurements, plus a margin for the publication by the provider.",
      "live: the newest value is not older than the expected age.",
      `delayed: the newest value is up to ${state.layout.delayed_factor} times older than the expected age.`,
      "stale: the newest value is older still; the provider may have paused or the feed fails.",
      "static: the dataset is loaded once and not updated regularly, such as a catalog.",
    ]),
  );
}

/** Reads the layout and the documents of every panel of every page and draws the selected page. */
async function renderDashboard() {
  state.layout = await loadJson("dashboard.json");
  await loadPayloads();
  renderPage();
  renderCredits(state.layout.credits);
  renderBuildInfo();
}

/** Lists the panel IDs of every page, to tell whether a new build changed the arrangement of the panels. */
function layoutShape(dashboardLayout) {
  return JSON.stringify(dashboardLayout.pages.map((page) => [page.title, page.rows.map((row) => row.map((entry) => [entry.id, entry.below]))]));
}

/**
 * Takes over a newer build of the documents in place: only panels whose document changed are drawn again, a playing
 * video keeps playing with its frames until it stops, and a running replay goes on.
 */
async function updateIfRebuilt() {
  try {
    const latest = await loadJson("dashboard.json");
    if (latest.generated_at === state.layout.generated_at) return;
    const arrangementChanged = layoutShape(latest) !== layoutShape(state.layout);
    const previous = state.payloadsById;
    state.layout = latest;
    await loadPayloads();
    if (arrangementChanged) {
      renderPage(true);
    } else {
      for (const { entry, panel } of shownPanels) {
        if (JSON.stringify(previous[entry.id]) === JSON.stringify(state.payloadsById[entry.id])) continue;
        // A playing video keeps its document; the next build after it stops brings it up to date
        if (panel.isPlaying?.()) {
          state.payloadsById[entry.id] = previous[entry.id];
          continue;
        }
        renderPanel(entry, panel);
      }
      const span = pageReplaySpan();
      if (span !== null) replayBarElement?.setSpan(span);
    }
    renderCredits(state.layout.credits);
    renderBuildInfo();
  } catch (error) {
    console.warn("Checking for a newer build failed:", error);
  }
}

/** Sets the theme of the page, light or dark. */
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
}

/** Creates the button switching between light and dark; the page starts in the theme of the system and keeps a choice only while open. */
function themeButton() {
  const button = element("button", "theme-button");
  button.type = "button";
  const show = () => {
    button.textContent = document.documentElement.dataset.theme === "dark" ? "Light mode" : "Dark mode";
  };
  button.addEventListener("click", () => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    show();
    renderPage(true);
  });
  show();
  return button;
}

/** Draws the dashboard and starts the timers keeping freshness labels and documents current. */
async function start() {
  setTheme(window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.querySelector(".page-header").append(themeButton());
  const dialog = document.getElementById("about");
  // A click on the backdrop lands on the dialog itself, whose content fills it completely
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  try {
    await renderDashboard();
  } catch (error) {
    document.getElementById("build-info").textContent = `The dashboard documents cannot be read: ${error.message}`;
    return;
  }
  // The ages of the freshness labels grow while the page is open: updated every 30 seconds
  setInterval(() => state.freshnessLabels.forEach(({ label, items }) => updateFreshness(label, items)), 30 * 1000);
  // A newer build of the documents is looked for once a minute
  setInterval(updateIfRebuilt, 60 * 1000);
  window.addEventListener("hashchange", () => renderPage());
}

start();
