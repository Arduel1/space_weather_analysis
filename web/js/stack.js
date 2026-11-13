import { state } from "./state.js";
import { element, panelTitle, richText, seriesColor, themeColor, utcText } from "./core.js";
import { freshnessCaption } from "./freshness.js";
import { textInfoButton } from "./info.js";
import { chartDiv, cursorShapes, cycleMarks, drawPlot, horizontalLine, rowAxis, rowDomains, rowTitle, seriesTraces, timeExtent, verticalLine } from "./charts.js";
import { onReplay } from "./replay.js";

/** Draws a chart of one or more stacked areas sharing the time axis, with levels, class axis and the view ending now. */
function drawChart(chart, payload, hideInvalid) {
  const now = Date.now();
  const rows = payload.rows;
  const domains = rowDomains(rows.length);
  const shapes = [];
  const annotations = [];
  const traces = [];
  // A single area is 300 pixels high; stacked areas take 140 pixels each plus room for axis and titles
  const plotLayout = {
    height: rows.length === 1 ? 300 : 140 * rows.length + 90,
    margin: { l: 60, r: 30, t: rows.length === 1 ? 30 : 60, b: 30 },
    hovermode: "x unified",
    legend: { orientation: "h", yanchor: "bottom", y: 1.04, xanchor: "right", x: 1 },
    bargap: 0.1,
  };
  // Position of a series over all areas, which picks its palette color
  let position = 0;
  rows.forEach((row, index) => {
    const { key: axisName, ref: axis } = rowAxis(index);
    const logAxis = row.y_axis === "log";
    const yAxis = { type: row.y_axis, domain: domains[index], title: { text: richText(row.y_title), font: { size: 11 } }, gridcolor: themeColor("--grid"), zerolinecolor: themeColor("--grid"), exponentformat: "power" };
    // Plotly takes the range of a log axis as exponents of ten
    if (row.y_range !== null) yAxis.range = logAxis ? row.y_range.map(Math.log10) : row.y_range;
    // Classes such as the flare classes become the labels of the value axis, each at the lower bound of its class
    if (row.ticks.length > 0) {
      yAxis.tickvals = row.ticks.map((tick) => tick.value);
      yAxis.ticktext = row.ticks.map((tick) => tick.label);
    }
    plotLayout[axisName] = yAxis;
    for (const level of row.levels) {
      shapes.push(horizontalLine(level.value, axis, { dash: "dot", width: 1, color: level.color }));
      // Annotations on a log axis are placed by the exponent of ten, unlike shapes
      annotations.push({ xref: "paper", yref: axis, x: 1, y: logAxis ? Math.log10(level.value) : level.value, text: level.label, showarrow: false, xanchor: "left", yanchor: "middle", xshift: 4, font: { size: 10, color: level.color } });
    }
    if (row.title && rows.length > 1) {
      annotations.push(rowTitle(row.title, domains[index][1], 2));
    }
    for (const series of row.series) {
      traces.push(...seriesTraces(series, row, axis, seriesColor(series, position), hideInvalid));
      position += 1;
    }
  });
  // Level names stand right of the chart, so that they never cover data or the area titles
  if (rows.some((row) => row.levels.length > 0)) plotLayout.margin.r = 80;
  // Stacked areas are named by their titles, which also name the series they share, so only a single area gets a legend
  plotLayout.showlegend = rows.length === 1 && position > 1;
  if (rows.length > 1) plotLayout.margin.t = 25;
  // The view shows the last payload.view seconds up to now
  const viewStart = now - payload.view * 1000;
  // Model values of the coming days extend the view beyond now, which a dashed line marks
  const viewEnd = now + (payload.ahead ?? 0) * 1000;
  const extent = timeExtent(rows) ?? [viewStart, viewEnd];
  plotLayout.xaxis = {
    type: "date",
    // The shared time axis is drawn under the lowest area
    anchor: rowAxis(rows.length - 1).ref,
    range: [utcText(new Date(viewStart), true), utcText(new Date(viewEnd), true)],
    // Panning ends where the loaded data end, so that the chart never shows an empty stretch beyond them
    minallowed: utcText(new Date(Math.min(extent[0], viewStart)), true),
    maxallowed: utcText(new Date(Math.max(extent[1], viewEnd)), true),
    gridcolor: themeColor("--grid"),
  };
  if (viewEnd > now) {
    const x = utcText(new Date(now), true);
    shapes.push(verticalLine(x, { dash: "dash", width: 1, color: themeColor("--muted") }));
    annotations.push({ xref: "x", yref: "paper", x, y: 1, text: "now", showarrow: false, xanchor: "left", yanchor: "top", xshift: 3, font: { size: 10, color: themeColor("--muted") } });
  }
  const cycle = cycleMarks(payload.marks ?? []);
  shapes.push(...cycle.shapes);
  annotations.push(...cycle.annotations);
  // The replay moves only the cursor; the other shapes are kept to draw it on top of them
  chart.baseShapes = shapes;
  plotLayout.shapes = [...shapes, ...cursorShapes(state.replayTime)];
  plotLayout.annotations = annotations;
  // The container takes the height of the stacked areas, so that none of them is squeezed
  chart.style.height = `${plotLayout.height}px`;
  drawPlot(chart, traces, plotLayout);
}

/** Draws a chart panel with a switch to hide invalid values where a fill value or bound is known. */
export function renderTimeseries(panel, payload) {
  panel.append(panelTitle(payload.title));
  const chart = chartDiv("chart");
  const allSeries = payload.rows.flatMap((row) => row.series);
  // The switch shows only where a fill value or a bound tells invalid values apart
  const knownValidity = allSeries.some((series) => Object.values(series.valid).some((bound) => bound !== null));
  const choiceKey = `${state.drawingPanel.id}:hide-invalid`;
  const hideInvalid = () => knownValidity && (state.panelChoices.get(choiceKey) ?? payload.hide_invalid);
  if (knownValidity) {
    const toggle = element("div", "toggle");
    const label = element("label");
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.checked = hideInvalid();
    checkbox.addEventListener("change", () => {
      state.panelChoices.set(choiceKey, checkbox.checked);
      drawChart(chart, payload, checkbox.checked);
    });
    label.append(checkbox, " Hide invalid values");
    const lines = ["Hides fill values, which mark missing measurements, and values outside the valid range of each field:", ...allSeries.map((series) => `${series.label}: ${series.validity}`)];
    toggle.append(label, textInfoButton("Hide invalid values", lines));
    panel.append(toggle);
  }
  if (!allSeries.some((series) => series.values.length > 0)) {
    panel.append(element("p", "note", "No data stored."));
    return;
  }
  panel.append(chart, freshnessCaption(payload.freshness));
  drawChart(chart, payload, hideInvalid());
  onReplay((time) => Plotly.relayout(chart, { shapes: [...chart.baseShapes, ...cursorShapes(time)] }));
}
