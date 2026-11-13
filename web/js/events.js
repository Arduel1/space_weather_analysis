import { ageText, element, linkedText, panelTitle, seriesColor, themeColor, utcText, withAlpha } from "./core.js";
import { freshnessCaption } from "./freshness.js";
import { chartDiv, drawPlot, plainLayout } from "./charts.js";

// Header lines of SWPC messages, which say nothing about the content
const MESSAGE_HEADERS = ["Space Weather Message Code", "Serial Number", "Issue Time"];

// Symbol of each kind of event in the chain chart, by its position in the lanes of the panel
const CHAIN_SYMBOLS = ["star", "triangle-up", "diamond", "cross", "circle-open", "square"];

/** Lists the newest provider messages, each opening to its full text. */
export function renderMessages(panel, payload) {
  panel.append(panelTitle(payload.title));
  const list = element("div", "scroll messages");
  if (payload.messages.length === 0) list.append(element("p", "note", "No messages in the searched span."));
  for (const message of payload.messages) {
    const item = element("details");
    // The summary quotes the first line with content after the header lines
    const firstLine = message.text.split("\n").find((line) => line.trim() && !MESSAGE_HEADERS.some((header) => line.startsWith(header))) ?? "";
    const text = element("pre");
    text.append(linkedText(message.text));
    item.append(element("summary", "", `${utcText(message.time)} UTC ${message.title ? `${message.title}: ` : ""}${firstLine.trim()}`), text);
    list.append(item);
  }
  panel.append(list, freshnessCaption(payload.freshness));
}

/**
 * Draws the chains of linked events like the chain view of DONKI: each chain a block of rows, one event per row at its
 * time, with an elbow line from each event to the events it led to. A click opens the event at the source.
 */
export function renderChains(panel, payload) {
  panel.append(panelTitle(payload.title));
  if (payload.chains.length === 0) {
    panel.append(element("p", "note", "No linked events in this span."));
    return;
  }
  // Oldest chain at the top, so that time runs down the chart as it runs to the right
  const chains = [...payload.chains].reverse();
  const rowOf = new Map();
  const shapes = [];
  // Rows count from 0 at the top; each chain is a block of rows on a band of alternating shade, the bands 0.4 rows apart
  let row = 0;
  chains.forEach((chain, chainIndex) => {
    const first = row;
    for (const position of chain) rowOf.set(position, row++);
    shapes.push({ type: "rect", xref: "paper", yref: "y", x0: 0, x1: 1, y0: first - 0.45, y1: row - 0.55, line: { width: 0 }, fillcolor: withAlpha(themeColor("--muted"), chainIndex % 2 === 0 ? 0.08 : 0.02), layer: "below" });
    row += 0.4;
  });
  // Events without link follow below the chains, one row per kind, so that the chart stays short however many there are
  const linked = new Set(payload.chains.flat());
  const crowded = new Set();
  const annotations = [];
  if (payload.unlinked > 0) {
    row += 0.4;
    const first = row;
    payload.lanes.forEach((lane, laneIndex) => {
      const positions = payload.events.map((_, position) => position).filter((position) => !linked.has(position) && payload.events[position].lane === laneIndex);
      if (positions.length === 0) return;
      for (const position of positions) rowOf.set(position, row);
      // A crowded row of more than 12 events shows its headlines on hover only, as written ones would overlap
      if (positions.length > 12) positions.forEach((position) => crowded.add(position));
      row += 1;
    });
    shapes.push({ type: "rect", xref: "paper", yref: "y", x0: 0, x1: 1, y0: first - 0.45, y1: row - 0.55, line: { width: 0 }, fillcolor: withAlpha(themeColor("--muted"), 0.05), layer: "below" });
    annotations.push({ xref: "paper", yref: "y", x: 0, y: first - 0.45, xanchor: "left", yanchor: "bottom", showarrow: false, text: "Events without links", font: { size: 10, color: themeColor("--muted") } });
  }
  // Each link is an elbow from the cause down its time to the row of the effect and along to the effect; null breaks the
  // line between two links, so that one trace holds all of them
  const linkX = [];
  const linkY = [];
  for (const [first, second] of payload.links) {
    const [cause, effect] = Date.parse(payload.events[first].time) <= Date.parse(payload.events[second].time) ? [first, second] : [second, first];
    const causeTime = payload.events[cause].time;
    linkX.push(causeTime, causeTime, payload.events[effect].time, null);
    linkY.push(rowOf.get(cause), rowOf.get(effect), rowOf.get(effect), null);
  }
  const traces = [{ type: "scatter", mode: "lines", x: linkX, y: linkY, line: { color: themeColor("--muted"), width: 1, dash: "dot" }, hoverinfo: "skip", showlegend: false }];
  payload.lanes.forEach((lane, laneIndex) => {
    const positions = [...rowOf.keys()].filter((position) => payload.events[position].lane === laneIndex);
    traces.push({
      type: "scatter",
      mode: "markers+text",
      name: lane.label,
      x: positions.map((position) => payload.events[position].time),
      y: positions.map((position) => rowOf.get(position)),
      text: positions.map((position) => (crowded.has(position) ? "" : payload.events[position].label ?? "")),
      textposition: "middle right",
      textfont: { size: 11, color: themeColor("--text") },
      customdata: positions.map((position) => [payload.events[position].link, payload.events[position].label ?? ""]),
      marker: { symbol: CHAIN_SYMBOLS[laneIndex % CHAIN_SYMBOLS.length], size: 11, color: seriesColor(lane, laneIndex), line: { width: 1.5, color: seriesColor(lane, laneIndex) } },
      hovertemplate: `${lane.label}<br>%{x|%b %d, %H:%M} UTC<br>%{customdata[1]}<extra>click opens it at DONKI</extra>`,
      cliponaxis: false,
    });
  });
  const now = Date.now();
  const start = now - payload.view * 1000;
  // 22 pixels per row plus room for legend and time axis, at least 220 pixels
  const plotLayout = plainLayout(Math.max(220, row * 22 + 90));
  plotLayout.margin = { l: 15, r: 60, t: 40, b: 30 };
  plotLayout.hovermode = "closest";
  plotLayout.shapes = shapes;
  plotLayout.annotations = annotations;
  plotLayout.legend = { orientation: "h", yanchor: "bottom", y: 1.01, xanchor: "left", x: 0 };
  plotLayout.xaxis = { type: "date", range: [utcText(new Date(start), true), utcText(new Date(now), true)], minallowed: utcText(new Date(start), true), maxallowed: utcText(new Date(now), true), gridcolor: themeColor("--grid") };
  plotLayout.yaxis = { autorange: "reversed", showticklabels: false, showgrid: false, zeroline: false, fixedrange: true };
  const chart = chartDiv("chart");
  chart.style.height = `${plotLayout.height}px`;
  drawPlot(chart, traces, plotLayout);
  // The chart element is kept across redraws; the click handler is attached once
  if (!chart.chainClicks) {
    chart.chainClicks = true;
    chart.on("plotly_click", (event) => {
      const target = event.points[0]?.customdata?.[0];
      if (target) window.open(target, "_blank", "noopener");
    });
  }
  const caption = element("p", "caption", `${payload.chains.length} chains and ${payload.unlinked} single events of the last ${ageText(payload.view)}, linked by the DONKI forecasters; dotted lines lead from cause to effect.`);
  const terms = element("details", "terms");
  terms.append(element("summary", "", "What the kinds of event mean (SPASE dictionary)"));
  const definitions = element("dl");
  for (const lane of payload.lanes) {
    if (!lane.definition) continue;
    definitions.append(element("dt", "", `${lane.label} (${lane.term})`), element("dd", "", lane.definition));
  }
  terms.append(definitions);
  panel.append(chart, caption, terms);
}
