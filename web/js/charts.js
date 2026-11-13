import { state } from "./state.js";
import { element, levelColor, richText, themeColor, utcText, withAlpha } from "./core.js";

// Vertical gap between two stacked chart areas, as share of the chart height
export const ROW_GAP = 0.05;

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
  // A bar fills 90 % of the median spacing, so that neighbouring bars stay apart
  return spacing[Math.floor(spacing.length / 2)] * 0.9;
}

/**
 * Draws a Plotly chart with the defaults every chart of the page shares: transparent background, the text and hover
 * colors of the theme, and zoom and pan of the viewer kept across later drawings.
 */
export function drawPlot(chart, traces, plotLayout) {
  const themed = {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: themeColor("--text") },
    hoverlabel: { bgcolor: themeColor("--surface"), bordercolor: themeColor("--border"), font: { color: themeColor("--text") } },
    // Plotly keeps zoom and pan while uirevision stays the same; a chart that sets its own resets them on a change
    uirevision: "keep",
    ...plotLayout,
  };
  // Charts follow the size of their element; the toolbar has no Plotly logo and no upload to Chart Studio
  Plotly.react(chart, traces, themed, { responsive: true, displaylogo: false, showSendToCloud: false });
}

/**
 * Returns the chart element of the panel being drawn, the same element as in the previous drawing of the page, so that
 * a redraw with new data keeps zoom, pan and scroll position.
 */
export function chartDiv(className = "chart") {
  // Charts are numbered in the order the panel creates them
  const key = `${state.drawingPanel.id}:${state.drawingPanel.charts++}`;
  let chart = state.chartsByKey.get(key);
  if (chart === undefined) {
    chart = element("div");
    state.chartsByKey.set(key, chart);
  }
  chart.className = className;
  return chart;
}

/** Returns the vertical domain of each stacked chart area, the first at the top. */
export function rowDomains(count) {
  // Plotly places areas in shares of the chart height from 0 at the bottom to 1 at the top
  const height = (1 - ROW_GAP * (count - 1)) / count;
  return Array.from({ length: count }, (_, index) => {
    const top = 1 - index * (height + ROW_GAP);
    return [Math.max(top - height, 0), top];
  });
}

/** Returns the layout key and the trace reference of the value axis of a stacked chart area: yaxis and y, then yaxis2 and y2, ... */
export function rowAxis(index) {
  return index === 0 ? { key: "yaxis", ref: "y" } : { key: `yaxis${index + 1}`, ref: `y${index + 1}` };
}

/** Returns the title of a stacked chart area, written above its top left corner in the muted color. */
export function rowTitle(text, top, yshift = 0) {
  return { xref: "paper", yref: "paper", x: 0, y: top, text, showarrow: false, xanchor: "left", yanchor: "bottom", yshift, font: { size: 11, color: themeColor("--muted") } };
}

/** Returns a line across the whole chart height at a time or position of the x axis. */
export function verticalLine(x, line) {
  return { type: "line", xref: "x", yref: "paper", x0: x, x1: x, y0: 0, y1: 1, line };
}

/** Returns a line across the whole chart width at a value of the value axis `yref`. */
export function horizontalLine(y, yref, line) {
  return { type: "line", xref: "paper", yref, x0: 0, x1: 1, y0: y, y1: y, line };
}

/** Splits points given as [x, y] pairs into the x and y lists of a Plotly trace. */
export function pointsXY(points) {
  return { x: points.map((point) => point[0]), y: points.map((point) => point[1]) };
}

/**
 * Returns the lines and labels marking the maxima (dashed) and minima (dotted) of the solar cycle; the label is the
 * prefix and max or min.
 */
export function cycleMarks(marks, prefix = "") {
  const shapes = [];
  const annotations = [];
  for (const mark of marks) {
    const x = utcText(mark.time);
    const maximum = mark.label.includes("maximum");
    shapes.push(verticalLine(x, { dash: maximum ? "dash" : "dot", width: 1, color: themeColor("--muted") }));
    annotations.push({ xref: "x", yref: "paper", x, y: 1, text: `${prefix}${maximum ? "max" : "min"}`, showarrow: false, yanchor: "bottom", font: { size: 10, color: themeColor("--muted") } });
  }
  return { shapes, annotations };
}

/** Builds the Plotly traces of one series in its chart area, without its invalid values on request. */
export function seriesTraces(series, row, axis, color, hideInvalid) {
  const x = series.times.map((time) => utcText(time, true));
  const y = hideInvalid ? series.values.map((value) => (value !== null && isInvalid(value, series.valid) ? null : value)) : series.values;
  const hovertemplate = `%{y:.3g} ${richText(series.unit)}`.trim();
  // legendgroup lets one click on the legend hide every trace of the series
  const common = { x, yaxis: axis, name: series.label, legendgroup: series.label };
  if (series.envelope) {
    // An envelope mirrors the values below zero; fill "tonexty" fills down to the trace before it, the upper line
    const lower = y.map((value) => (value === null ? null : -value));
    return [
      { ...common, type: "scatter", mode: "lines", y, line: { width: 0.5, color }, hovertemplate, showlegend: true },
      { ...common, type: "scatter", mode: "lines", y: lower, fill: "tonexty", fillcolor: withAlpha(color, 0.18), line: { width: 0.5, color }, hoverinfo: "skip", showlegend: false },
    ];
  }
  const markerColors = series.color_by_scale ? y.map((value) => (value === null ? color : levelColor(value, row, color))) : color;
  const traces = [];
  if (series.style === "bars") {
    const marker = { color: markerColors };
    // Hatching marks values of lesser standing, such as estimates, in the colors of the measured ones
    if (series.hatched) marker.pattern = { shape: "/", fillmode: "overlay", fgcolor: themeColor("--background"), size: 6, solidity: 0.35 };
    traces.push({ ...common, type: "bar", y, width: barWidth(series.times), marker, hovertemplate });
  } else if (series.style === "markers") {
    const marker = { color: markerColors, size: 8, line: { width: 1, color: themeColor("--surface") } };
    const text = series.labels ?? undefined;
    // WebGL draws many markers fast; labelled markers need the SVG trace, which writes text
    traces.push({ ...common, type: text ? "scatter" : "scattergl", mode: text ? "markers+text" : "markers", y, marker, text, textposition: "top center", textfont: { size: 10, color: themeColor("--muted") }, cliponaxis: false, hovertemplate: text ? `%{text}: ${hovertemplate}` : hovertemplate });
  } else if (series.style === "area") {
    traces.push({ ...common, type: "scatter", mode: "lines", y, fill: "tozeroy", fillcolor: withAlpha(color, 0.25), line: { color, width: 1.5 }, hovertemplate });
  } else {
    traces.push({ ...common, type: "scattergl", mode: "lines", y, line: { color, width: 1.5, dash: series.dash }, hovertemplate, connectgaps: false });
  }
  if (series.shade_negative) {
    // A second trace holding only the negative part fills it down from zero in the warning color
    const negative = y.map((value) => (value === null ? null : Math.min(value, 0)));
    traces.push({ ...common, type: "scatter", mode: "lines", y: negative, fill: "tozeroy", fillcolor: withAlpha(themeColor("--stale"), 0.35), line: { width: 0 }, hoverinfo: "skip", showlegend: false });
  }
  return traces;
}

/** Returns the earliest and latest time of the series of a chart in milliseconds; null without values. */
export function timeExtent(rows) {
  // The times of a series are sorted, so its first and last time bound it
  const times = rows.flatMap((row) => row.series.flatMap((series) => (series.times.length > 0 ? [Date.parse(series.times[0]), Date.parse(series.times[series.times.length - 1])] : [])));
  return times.length > 0 ? [Math.min(...times), Math.max(...times)] : null;
}

/** Returns the vertical line marking the replay time on a chart; none while the newest data are shown. */
export function cursorShapes(time) {
  if (time === null) return [];
  return [verticalLine(utcText(new Date(time), true), { color: themeColor("--text"), width: 1.5 })];
}

/** Returns the shared Plotly layout of a chart drawn without time axis, at a given height. */
export function plainLayout(height) {
  return {
    height,
    margin: { l: 60, r: 15, t: 30, b: 45 },
    // The legend stands in one line above the top right corner of the plot area
    legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "right", x: 1 },
  };
}

/** Creates a legend of colored swatches, each an entry of swatch class, color and text. */
export function swatchLegend(entries) {
  const legend = element("div", "field-legend");
  for (const [className, color, text] of entries) {
    const item = element("span", "field-legend-item");
    const swatch = element("span", className);
    // The swatch classes of style.css draw their line, ring or square in this custom property
    swatch.style.setProperty("--swatch", color);
    item.append(swatch, text);
    legend.append(item);
  }
  return legend;
}
