"use strict";

// Base URL of the dashboard documents written by site_export; a live API serving the same documents replaces it
const DATA_URL = "data/";
// Time between two checks for a newer build of the documents
const RELOAD_CHECK_MS = 5 * 60 * 1000;
// Time between two updates of the freshness labels, whose ages grow while the page is open
const FRESHNESS_UPDATE_MS = 30 * 1000;
// Share of the spacing of two values a bar fills, so that neighbouring bars stay apart
const BAR_FILL = 0.9;
const PLOTLY_CONFIG = { responsive: true, displaylogo: false, showSendToCloud: false };
// Order from best to worst, so that a panel with several datasets shows the worst state
const SEVERITY = { static: 0, live: 1, delayed: 2, stale: 3 };

// Freshness labels on the page with the datasets they rate, updated by a timer
let freshnessLabels = [];
let layout = null;
// The loaded panel documents of every page, by panel ID
let payloadsById = {};

/**
 * Reads one dashboard document; the only place that knows where the documents come from.
 * @param {string} path The path of the document below DATA_URL.
 * @returns {Promise<object>} The parsed document.
 */
async function loadJson(path) {
  const response = await fetch(DATA_URL + path, { cache: "no-cache" });
  if (!response.ok) {
    throw new Error(`${path}: HTTP ${response.status}`);
  }
  return response.json();
}

/**
 * Creates an element with a class and text content.
 * @param {string} tag The tag name.
 * @param {string} className The class names; empty for none.
 * @param {string} text The text content; empty for none.
 * @returns {HTMLElement} The element.
 */
function element(tag, className = "", text = "") {
  const created = document.createElement(tag);
  if (className) created.className = className;
  if (text) created.textContent = text;
  return created;
}

/** Writes an age in its largest whole unit: seconds below a minute, minutes below an hour, hours below two days, else days. */
function ageText(seconds) {
  const whole = Math.max(Math.floor(seconds), 0);
  if (whole < 60) return `${whole} s`;
  if (whole < 3600) return `${Math.floor(whole / 60)} min`;
  if (whole < 2 * 86400) return `${Math.floor(whole / 3600)} h`;
  return `${Math.floor(whole / 86400)} d`;
}

/** Writes an ISO 8601 UTC time as `{date} {hours}:{minutes}` without time zone, the form Plotly reads as plain time. */
function utcText(iso, withSeconds = false) {
  return new Date(iso).toISOString().slice(0, withSeconds ? 19 : 16).replace("T", " ");
}

/**
 * Rates the newest value of one dataset against the current time, like the Streamlit dashboard does.
 * @param {{newest: string, expected_age: number|null}} item The freshness of the dataset from the payload.
 * @param {number} now The current time in milliseconds.
 * @returns {string} live up to the expected age, delayed up to delayed_factor times it, else stale; static without expected age.
 */
function rate(item, now) {
  if (item.expected_age === null) return "static";
  const age = (now - Date.parse(item.newest)) / 1000;
  if (age <= item.expected_age) return "live";
  return age <= item.expected_age * layout.delayed_factor ? "delayed" : "stale";
}

/** Writes the worst state of the datasets of a panel with the age of the newest value of that dataset into a label. */
function updateFreshness(label, items) {
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
function freshnessLabel(items) {
  const label = element("span");
  freshnessLabels.push({ label, items });
  updateFreshness(label, items);
  return label;
}

/** Creates the heading of a panel, with its help lines as tooltip where given. */
function panelTitle(title, helpLines = []) {
  const heading = element("div", "panel-title", title);
  if (helpLines.length > 0) {
    const help = element("span", "help", "?");
    help.title = helpLines.join("\n");
    heading.append(help);
  }
  return heading;
}

/** Draws a tile with the newest value, its level, its time with freshness and its source. */
function renderValue(panel, payload) {
  const view = payload.view;
  panel.append(panelTitle(view.title, view.help_lines), element("div", "tile-value", view.text));
  if (view.level !== null) {
    const level = element("span", "level", view.level.label);
    level.style.background = view.level.color;
    panel.append(level);
  }
  const time = element("p", "caption");
  if (view.time === null) {
    time.textContent = "No data stored";
  } else {
    time.append(`${utcText(view.time)} UTC, `, freshnessLabel(payload.freshness));
  }
  panel.append(time, element("p", "caption", view.source));
}

/** Tells whether a value is no measurement: the fill value or outside the bounds of the series. */
function isInvalid(value, valid) {
  return (valid.fill !== null && value === valid.fill) || (valid.low !== null && value < valid.low) || (valid.high !== null && value > valid.high);
}

/** Returns the width of bars in milliseconds from the median spacing of the times; null with fewer than two times. */
function barWidth(times) {
  if (times.length < 2) return null;
  const spacing = [];
  for (let index = 1; index < times.length; index++) spacing.push(Date.parse(times[index]) - Date.parse(times[index - 1]));
  spacing.sort((first, second) => first - second);
  return spacing[Math.floor(spacing.length / 2)] * BAR_FILL;
}

/** Reads a color token of the page theme, so that the charts follow light and dark mode. */
function themeColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Builds one Plotly trace of a series, without its invalid values on request. */
function seriesTrace(series, hideInvalid) {
  const x = series.times.map((time) => utcText(time, true));
  const y = hideInvalid ? series.values.map((value) => (value !== null && isInvalid(value, series.valid) ? null : value)) : series.values;
  const color = series.color ?? undefined;
  const hovertemplate = `%{y:.3g} ${series.unit}`.trim();
  if (series.style === "bars") {
    return { type: "bar", x, y, name: series.label, width: barWidth(series.times), marker: { color }, hovertemplate };
  }
  const mode = series.style === "line" ? "lines" : "markers";
  return { type: "scattergl", mode, x, y, name: series.label, line: { color, width: 1.5 }, marker: { color }, hovertemplate };
}

/** Draws a chart with its series, the threshold lines of its scale and the view ending now. */
function drawChart(chart, payload, hideInvalid) {
  const logAxis = payload.y_axis === "log";
  const now = Date.now();
  const yAxis = { type: payload.y_axis, title: { text: payload.y_title }, gridcolor: themeColor("--grid") };
  if (payload.y_range !== null) {
    // Plotly takes the range of a log axis as exponents of ten
    yAxis.range = logAxis ? payload.y_range.map(Math.log10) : payload.y_range;
  }
  const levelLines = payload.levels.map((level) => ({
    type: "line",
    xref: "paper",
    x0: 0,
    x1: 1,
    y0: level.value,
    y1: level.value,
    line: { dash: "dot", width: 1, color: level.color },
  }));
  // Annotations on a log axis are placed by the exponent of ten, unlike shapes
  const levelLabels = payload.levels.map((level) => ({
    xref: "paper",
    x: 1,
    y: logAxis ? Math.log10(level.value) : level.value,
    text: level.label,
    showarrow: false,
    xanchor: "right",
    yanchor: "bottom",
    font: { size: 10, color: level.color },
  }));
  const plotLayout = {
    title: { text: payload.title, font: { size: 14 } },
    height: 300,
    margin: { l: 50, r: 10, t: 40, b: 30 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: themeColor("--text") },
    hovermode: "x unified",
    showlegend: payload.series.length > 1,
    legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "right", x: 1 },
    xaxis: { type: "date", range: [utcText(new Date(now - payload.view * 1000), true), utcText(new Date(now), true)], gridcolor: themeColor("--grid") },
    yaxis: yAxis,
    shapes: levelLines,
    annotations: levelLabels,
    // Keeps zoom and pan of the viewer when the chart is drawn again
    uirevision: "keep",
  };
  Plotly.react(chart, payload.series.map((series) => seriesTrace(series, hideInvalid)), plotLayout, PLOTLY_CONFIG);
}

/** Draws a chart panel with a switch to hide invalid values where a fill value or bound is known. */
function renderTimeseries(panel, payload) {
  const chart = element("div", "chart");
  const knownValidity = payload.series.some((series) => Object.values(series.valid).some((bound) => bound !== null));
  if (knownValidity) {
    const toggle = element("label", "toggle");
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.checked = payload.hide_invalid;
    checkbox.addEventListener("change", () => drawChart(chart, payload, checkbox.checked));
    toggle.title = ["Hides fill values and values outside the valid range:", ...payload.series.map((series) => `${series.label}: ${series.validity}`)].join("\n");
    toggle.append(checkbox, " Hide invalid values");
    panel.append(toggle);
  }
  const hasData = payload.series.some((series) => series.values.length > 0);
  if (!hasData) {
    panel.append(panelTitle(payload.title), element("p", "note", "No data stored."), element("p", "caption", payload.source));
    return;
  }
  const caption = element("p", "caption");
  caption.append(freshnessLabel(payload.freshness), ` -- ${payload.source}`);
  panel.append(chart, caption);
  drawChart(chart, payload, knownValidity && payload.hide_invalid);
  chart.redraw = () => drawChart(chart, payload, knownValidity && panel.querySelector("input")?.checked);
}

/** Writes one table cell: times without time zone, booleans as yes or no, nothing for missing values. */
function cellText(value, columnIndex) {
  if (value === null) return "";
  if (columnIndex === 0) return utcText(value);
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

/** Draws a table of the newest documents, newest first. */
function renderTable(panel, payload) {
  if (payload.title) panel.append(panelTitle(payload.title));
  const caption = element("p", "caption");
  if (payload.freshness.length > 0) caption.append(freshnessLabel(payload.freshness), " -- ");
  caption.append(payload.source);
  panel.append(caption);
  if (payload.rows.length === 0) {
    panel.append(element("p", "note", "No data stored."));
    return;
  }
  const table = element("table");
  const header = element("tr");
  for (const column of payload.columns) header.append(element("th", "", column));
  table.append(element("thead"), element("tbody"));
  table.tHead.append(header);
  for (const row of payload.rows) {
    const line = element("tr");
    row.forEach((value, columnIndex) => line.append(element("td", "", cellText(value, columnIndex))));
    table.tBodies[0].append(line);
  }
  const scroll = element("div", "scroll");
  scroll.append(table);
  panel.append(scroll);
}

/** Draws the status of every feed: state, age of the newest job and interval, the outcomes in the window and what went wrong. */
function renderFeeds(panel, payload) {
  panel.append(panelTitle(payload.title));
  const scroll = element("div", "scroll");
  if (payload.feeds.length === 0) scroll.append(element("p", "note", "No feed has run yet."));
  const builtAt = Date.parse(layout.generated_at);
  for (const feed of payload.feeds) {
    const block = element("div", "feed");
    const schedule = feed.interval === null ? "on start only" : `every ${ageText(feed.interval)}`;
    const first = element("div");
    first.append(element("strong", "", feed.feed), ": ", element("span", `state-${feed.state}`, feed.state));
    first.append(`, last run ${ageText((builtAt - Date.parse(feed.last_run)) / 1000)} before the build, ${schedule}`);
    const counts = `Last ${ageText(payload.window)}: ${feed.runs} runs, ${feed.completed} complete, ${feed.with_gaps} with gaps, ${feed.failed} failed`;
    block.append(first, element("div", "caption", counts));
    if (feed.problem) block.append(element("div", "caption", feed.problem));
    scroll.append(block);
  }
  panel.append(scroll);
}

// Drawing function of each panel type, by the type name of the payload
const RENDERERS = { value: renderValue, timeseries: renderTimeseries, table: renderTable, feeds: renderFeeds };

/** Links a Creative Commons license name to its deed; other license texts stay plain. */
function licenseLink(source) {
  const match = source.match(/CC (BY(?:-[A-Z]{2})*) (\d\.\d)/);
  if (match === null) return null;
  const link = element("a", "", "license");
  link.href = `https://creativecommons.org/licenses/${match[1].toLowerCase()}/${match[2]}/`;
  return link;
}

/** Lists the sources of the shown datasets with their terms and requested acknowledgements. */
function renderCredits(credits) {
  const list = document.getElementById("credits");
  list.replaceChildren();
  for (const credit of credits) {
    const item = element("li", "", credit.source);
    const link = licenseLink(credit.source);
    if (link !== null) item.append(" (", link, ")");
    if (credit.acknowledgement) item.append(element("div", "", credit.acknowledgement));
    if (credit.citation) item.append(element("div", "", credit.citation));
    list.append(item);
  }
}

/** Turns a page title into the URL fragment that selects its tab. */
function pageSlug(title) {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

/** Returns the page named by the URL fragment, else the first page. */
function selectedPage() {
  const slug = window.location.hash.slice(1);
  return layout.pages.find((page) => pageSlug(page.title) === slug) ?? layout.pages[0];
}

/** Draws the tabs of the pages, marking the selected one; a dashboard of one page gets no tabs. */
function renderTabs(selected) {
  const tabs = document.getElementById("pages");
  tabs.replaceChildren();
  tabs.hidden = layout.pages.length < 2;
  for (const page of layout.pages) {
    const tab = element("a", "tab", page.title);
    tab.href = `#${pageSlug(page.title)}`;
    if (page === selected) tab.setAttribute("aria-current", "page");
    tabs.append(tab);
  }
}

/** Draws the selected page row by row from the loaded panel documents. */
function renderPage() {
  const selected = selectedPage();
  renderTabs(selected);
  freshnessLabels = [];
  const dashboard = document.getElementById("dashboard");
  dashboard.replaceChildren();
  for (const row of selected.rows) {
    const line = element("div", "row");
    for (const entry of row) {
      const panel = element("section", `panel panel-${entry.type}`);
      panel.style.flexGrow = entry.width;
      const payload = payloadsById[entry.id];
      const render = RENDERERS[entry.type];
      if (payload.error !== undefined || render === undefined) {
        panel.append(element("p", "note", payload.error !== undefined ? `Panel not available: ${payload.error.message}` : `Unknown panel type ${entry.type}`));
      } else {
        render(panel, payload);
      }
      line.append(panel);
    }
    dashboard.append(line);
  }
}

/** Reads the layout and the documents of every panel of every page and draws the selected page. */
async function renderDashboard() {
  layout = await loadJson("dashboard.json");
  const entries = layout.pages.flatMap((page) => page.rows.flat());
  const payloads = await Promise.all(entries.map((entry) => loadJson(`panels/${entry.id}.json`).catch((error) => ({ error }))));
  payloadsById = Object.fromEntries(entries.map((entry, index) => [entry.id, payloads[index]]));
  renderPage();
  renderCredits(layout.credits);
  document.getElementById("build-info").textContent = `Built ${utcText(layout.generated_at)} UTC from the stored data.`;
}

/** Draws the dashboard again when a newer build of the documents is published. */
async function reloadIfRebuilt() {
  try {
    const latest = await loadJson("dashboard.json");
    if (latest.generated_at !== layout.generated_at) await renderDashboard();
  } catch (error) {
    console.warn("Checking for a newer build failed:", error);
  }
}

/** Draws every chart again in the colors of the current theme. */
function redrawCharts() {
  for (const chart of document.querySelectorAll(".chart")) chart.redraw?.();
}

/** Draws the dashboard and starts the timers keeping freshness labels and documents current. */
async function start() {
  try {
    await renderDashboard();
  } catch (error) {
    document.getElementById("build-info").textContent = `The dashboard documents cannot be read: ${error.message}`;
    return;
  }
  setInterval(() => freshnessLabels.forEach(({ label, items }) => updateFreshness(label, items)), FRESHNESS_UPDATE_MS);
  setInterval(reloadIfRebuilt, RELOAD_CHECK_MS);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redrawCharts);
  window.addEventListener("hashchange", renderPage);
}

start();
