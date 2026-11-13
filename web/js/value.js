import { colorValue, element, panelTitle, spanWords, themeColor, utcText, withAlpha } from "./core.js";
import { freshnessCaption } from "./freshness.js";
import { chartDiv, drawPlot, horizontalLine } from "./charts.js";

/**
 * Draws the recent values of a tile with their axes and, where the history is known, the usual range of the history as
 * band with its median, so that a viewer sees at once whether the newest value is usual.
 */
function drawTileChart(chart, payload) {
  const sparkline = payload.sparkline;
  const context = payload.context;
  const present = sparkline.values.filter((value) => value !== null);
  // Values spanning several powers of ten, such as X-ray and particle fluxes, need a log axis to show their course
  const positive = present.length > 0 && Math.min(...present) > 0;
  // A log axis from a ratio of 50 between the largest and the smallest value on
  const logAxis = positive && Math.max(...present) / Math.min(...present) > 50;
  const color = colorValue(payload.color, themeColor("--series-1"));
  const traces = [{ type: "scatter", mode: "lines", x: sparkline.times.map((time) => utcText(time, true)), y: sparkline.values, line: { color, width: 1.5 }, hovertemplate: "%{y:.3g}<extra>%{x|%H:%M} UTC</extra>", showlegend: false }];
  const shapes = [];
  const annotations = [];
  if (context) {
    // The usual range as a band behind the line, its median dashed; layer "below" keeps both under the data
    shapes.push(
      { type: "rect", xref: "paper", yref: "y", x0: 0, x1: 1, y0: context.low, y1: context.high, fillcolor: withAlpha(themeColor("--muted"), 0.15), line: { width: 0 }, layer: "below" },
      { ...horizontalLine(context.median, "y", { color: themeColor("--muted"), width: 1, dash: "dash" }), layer: "below" },
    );
    annotations.push({
      xref: "paper",
      yref: "paper",
      x: 0,
      y: 1,
      xanchor: "left",
      yanchor: "bottom",
      align: "left",
      showarrow: false,
      font: { size: 10, color: themeColor("--muted") },
      text: `<b>higher than ${Math.round(context.percentile)} %</b> of ${spanWords(context.span)}<br>band: middle 90 %, dashed: median`,
    });
  }
  // Zoom and pan stay within the recent values
  const latest = sparkline.times.length > 0 ? Date.parse(sparkline.times[sparkline.times.length - 1]) : Date.now();
  const earliest = sparkline.times.length > 0 ? Date.parse(sparkline.times[0]) : latest;
  drawPlot(chart, traces, {
    height: 150,
    margin: { l: 42, r: 8, t: context ? 32 : 8, b: 24 },
    hovermode: "x unified",
    showlegend: false,
    shapes,
    annotations,
    xaxis: { type: "date", gridcolor: themeColor("--grid"), tickfont: { size: 10 }, nticks: 4, tickangle: 0, tickformat: "%H:%M", minallowed: utcText(new Date(earliest), true), maxallowed: utcText(new Date(latest), true) },
    yaxis: { type: logAxis ? "log" : "linear", gridcolor: themeColor("--grid"), zerolinecolor: themeColor("--grid"), tickfont: { size: 10 }, nticks: 4, exponentformat: "power" },
  });
}

/** Draws a tile with the newest value, its level, recent values against the usual range, re-expressions and time. */
export function renderValue(panel, payload) {
  const view = payload.view;
  panel.append(panelTitle(view.title), element("div", "tile-value", view.text));
  if (view.level !== null) {
    const level = element("span", "level", view.level.label);
    level.style.background = view.level.color;
    panel.append(level);
  }
  if (payload.sparkline && payload.sparkline.values.some((value) => value !== null)) {
    const chart = chartDiv("chart tile-chart");
    panel.append(chart);
    drawTileChart(chart, payload);
  }
  for (const expression of payload.re_expressions) panel.append(element("p", "context", `${expression.label}: ${expression.text}`));
  panel.append(view.time === null ? element("p", "caption", "No data yet") : freshnessCaption(payload.freshness));
}
