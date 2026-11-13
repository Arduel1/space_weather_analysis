import { state } from "./state.js";

// Base URL of the dashboard documents written by site_export; a live API serving the same documents replaces it
export const DATA_URL = "data/";

// Color of the outlines of coronal holes in all views
export const CORONAL_HOLE_COLOR = "#3ddc97";

// A hex color #rrggbb: "#" and exactly six hex digits up to the end, either case
const HEX_COLOR = /^#[0-9a-f]{6}$/i;

// Web addresses and DOIs in provider texts, turned into links. First alternative (group 1): "http://" or "https://",
// then characters other than whitespace, < > ( ) and ", the last one also not . , ; or :, so that the punctuation of
// the sentence stays outside the link. Second alternative: "doi:" at the start of a word, then the DOI (group 2): "10.",
// 4 to 9 digits, "/" and a suffix without whitespace , ; ( ), the last character also not ) . or :
const LINK_PATTERN = /(https?:\/\/[^\s<>()"]*[^\s<>()".,;:])|\bdoi:(10\.\d{4,9}\/[^\s,;()]*[^\s,;().:])/g;

/**
 * Reads one JSON document from the data directory of the site.
 * @param {string} path The path of the document below DATA_URL.
 * @returns {Promise<object>} The parsed document.
 */
export async function loadJson(path) {
  // no-cache revalidates with the server, so that a new build is seen without a stale copy of the browser
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
export function element(tag, className = "", text = "") {
  const created = document.createElement(tag);
  if (className) created.className = className;
  if (text) created.textContent = text;
  return created;
}

/** Writes an age in its largest whole unit: seconds below a minute, minutes below an hour, hours below two days, else days. */
export function ageText(seconds) {
  const whole = Math.max(Math.floor(seconds), 0);
  if (whole < 60) return `${whole} s`;
  if (whole < 3600) return `${Math.floor(whole / 60)} min`;
  if (whole < 2 * 86400) return `${Math.floor(whole / 3600)} h`;
  return `${Math.floor(whole / 86400)} d`;
}

/** Writes an ISO 8601 UTC time as `{date} {hours}:{minutes}` without time zone, the form Plotly reads as plain time. */
export function utcText(iso, withSeconds = false) {
  // toISOString gives `{date}T{hours}:{minutes}:{seconds}.{milliseconds}Z`; the first 16 or 19 characters end at the
  // minutes or seconds
  return new Date(iso).toISOString().slice(0, withSeconds ? 19 : 16).replace("T", " ");
}

/** Writes a time step in words: months and years for calendar lengths, else the largest whole unit of ageText. */
export function cadenceWords(seconds) {
  const days = seconds / 86400;
  if (days >= 28 && days <= 31) return "1 month";
  if (days >= 365 && days <= 366) return "1 year";
  return ageText(seconds);
}

/** Creates the heading of a panel; footnotes and the info button are added when the page is drawn. */
export function panelTitle(title) {
  return element("div", "panel-title", title);
}

/** Names the span a tile compares with: the past days below two months, else the past months or years. */
export function spanWords(seconds) {
  const days = Math.round(seconds / 86400);
  if (days < 60) return `the past ${days} days`;
  // 30.44 days is the mean month of the Gregorian calendar, 365.25 days the mean year of the Julian calendar
  if (days < 700) return `the past ${Math.round(days / 30.44)} months`;
  return `the past ${Math.round(days / 365.25)} years`;
}

/**
 * Formats a unit or formula for Plotly text and HTML: powers as superscript, indices after _ as subscript and
 * factors written as 1e3 as powers of ten.
 */
export function richText(text) {
  if (!text) return "";
  const escaped = text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  return (
    escaped
      // A whole word 1e{exponent}, the exponent an optional minus and digits: the power of ten alone
      .replace(/\b1e(-?\d+)\b/g, "10<sup>$1</sup>")
      // {mantissa}e{exponent}, the mantissa a digit with optional decimals: mantissa times the power of ten
      .replace(/(\d(?:\.\d+)?)e(-?\d+)\b/g, "$1&#183;10<sup>$2</sup>")
      // ^({exponent}) with no parentheses inside: the exponent raised, without its parentheses
      .replace(/\^\(([^()]+)\)/g, "<sup>$1</sup>")
      // ^{exponent}, the exponent an optional minus and letters, digits, _ or dots: the exponent raised
      .replace(/\^(-?[\w.]+)/g, "<sup>$1</sup>")
      // _ and an index of letters and digits, optionally in curly braces: the index lowered, without the braces
      .replace(/_\{?([A-Za-z0-9]+)\}?/g, "<sub>$1</sub>")
      // The whole words theta and alpha and a word ending in Phi as Greek letters
      .replace(/\btheta\b/g, "&#952;")
      .replace(/\balpha\b/g, "&#945;")
      .replace(/Phi\b/g, "&#934;")
  );
}

/** Returns an element showing a unit or formula formatted by richText; the text holds no markup of its own. */
export function richElement(tag, className, text) {
  const created = element(tag, className);
  created.innerHTML = richText(text);
  return created;
}

/** Turns a text into a fragment in which every web address and DOI is a link. */
export function linkedText(text) {
  const fragment = document.createDocumentFragment();
  let last = 0;
  for (const match of text.matchAll(LINK_PATTERN)) {
    // The text before the match stays plain; a DOI links to its resolver and keeps its written form
    fragment.append(text.slice(last, match.index));
    fragment.append(match[1] ? link(match[1]) : link(`https://doi.org/${match[2]}`, match[0]));
    last = match.index + match[0].length;
  }
  fragment.append(text.slice(last));
  return fragment;
}

/** Creates a caption whose web addresses and DOIs are links. */
export function linkedCaption(text) {
  const caption = element("p", "caption");
  caption.append(linkedText(text));
  return caption;
}

/** Reads a color token of the page theme, so that the charts follow light and dark mode. */
export function themeColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Resolves a configured color: a theme color token such as --theme-wind, any other CSS color, or the fallback for none. */
export function colorValue(value, fallback) {
  if (value === null || value === undefined) return fallback;
  return value.startsWith("--") ? themeColor(value) : value;
}

/** Splits a #rrggbb color into its red, green and blue values from 0 to 255. */
function hexChannels(color) {
  return [1, 3, 5].map((offset) => parseInt(color.slice(offset, offset + 2), 16));
}

/** Mixes two hex colors; share 0 keeps the first, 1 gives the second. */
function mixColors(first, second, share) {
  const to = hexChannels(second);
  // Each channel moves linearly from the first color towards the second and is written back as two hex digits
  const channels = hexChannels(first).map((value, index) => Math.round(value + (to[index] - value) * share).toString(16).padStart(2, "0"));
  return `#${channels.join("")}`;
}

/** Returns a weaker shade of a color for a positive amount, mixed towards the page surface, or a stronger one for a negative amount, mixed towards the text color. */
export function shade(color, amount) {
  return amount >= 0 ? mixColors(color, themeColor("--surface"), amount) : mixColors(color, themeColor("--text"), -amount);
}

/** Returns the configured color of a series or event lane, else the categorical palette color of its position. */
export function seriesColor(series, position) {
  // The palette has eight categorical colors, --series-1 to --series-8, taken in fixed order
  return colorValue(series.color, themeColor(`--series-${(position % 8) + 1}`));
}

/** Returns a hex color with transparency as rgba; other colors stay as they are. */
export function withAlpha(color, alpha) {
  return HEX_COLOR.test(color) ? `rgba(${hexChannels(color).join(",")},${alpha})` : color;
}

/** Returns the color of the highest level of a row a value reaches, the quiet level color below the first level. */
export function levelColor(value, row, fallback) {
  let color = row.quiet ? row.quiet.color : fallback;
  for (const level of row.levels) if (value >= level.value) color = level.color;
  return color;
}

/** Creates a link that opens in a new tab. */
export function link(url, text = url) {
  const anchor = element("a", "", text);
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noopener";
  return anchor;
}

/**
 * Creates a row of buttons for one choice of the viewer, kept in panelChoices under `key` across redraws.
 * @param {string} key The key of the choice in panelChoices.
 * @param {Array<[string, *]>} options The label and value of each button.
 * @param {*} fallback The value chosen while the viewer has not chosen.
 * @param {function} onChoose Called with the value of a clicked button after the choice is stored and marked.
 * @returns {{tabs: HTMLElement, buttons: HTMLButtonElement[]}} The row and its buttons.
 */
export function choiceTabs(key, options, fallback, onChoose) {
  const tabs = element("div", "video-tabs");
  const mark = () => {
    const chosen = state.panelChoices.get(key) ?? fallback;
    buttons.forEach((button, position) => button.setAttribute("aria-pressed", String(options[position][1] === chosen)));
  };
  const buttons = options.map(([label, value]) => {
    const button = element("button", "", label);
    button.type = "button";
    button.addEventListener("click", () => {
      state.panelChoices.set(key, value);
      mark();
      onChoose(value);
    });
    return button;
  });
  tabs.append(...buttons);
  mark();
  return { tabs, buttons };
}
