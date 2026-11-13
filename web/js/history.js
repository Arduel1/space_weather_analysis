import { colorValue, element, linkedCaption, panelTitle, richText, seriesColor, shade, themeColor, utcText, withAlpha } from "./core.js";
import { freshnessCaption } from "./freshness.js";
import { ROW_GAP, chartDiv, cycleMarks, drawPlot, plainLayout, rowAxis, rowDomains, rowTitle, seriesTraces, swatchLegend, verticalLine } from "./charts.js";

/**
 * Draws the solar cycle with what it drives on one time axis in three rows: the sunspot number, every catalog event as
 * a bubble sized by its width, and every intense storm with the strongest named by date.
 */
export function renderSolarCycle(panel, payload) {
  panel.append(panelTitle(payload.title));
  const chart = chartDiv("chart chart-tall");
  chart.style.height = "760px";
  // Shares of the chart height of the three rows from the top: sunspots, CMEs, storms
  const domains = [[0.72, 1], [0.3, 0.66], [0, 0.24]];
  const sunColor = themeColor("--theme-sun");
  // The three groups of CMEs are categories and take the first three palette slots in fixed order, the only ones that
  // stay apart in a scatter: reached Earth blue (the color of Earth on every page), halo orange, the others aqua
  const earthColor = themeColor("--series-1");
  const flagColor = themeColor("--series-2");
  const otherColor = themeColor("--series-3");
  const stormColor = themeColor("--theme-storm");
  // Bubble diameter grows with the square root of the width, so that the area follows the width; halo CMEs of 360
  // degrees stay below the size that would cover their neighbours
  const sizeOf = (width) => 3 + Math.sqrt(Math.max(width, 0)) * 0.6;
  const groupStyles = [
    { color: otherColor, opacity: 0.3, ring: 0, name: payload.other_label, short: "CME" },
    { color: flagColor, opacity: 0.6, ring: 1, name: payload.flag_label, short: "Halo CME" },
    { color: earthColor, opacity: 0.95, ring: 1.5, name: payload.earth_label, short: "CME that reached Earth" },
  ];
  // One trace per group, holding the indices of the events of that group
  const eventTrace = (group) => {
    const chosen = payload.event_groups.map((value, index) => (value === group ? index : -1)).filter((index) => index >= 0);
    const style = groupStyles[group];
    return {
      type: "scattergl",
      mode: "markers",
      x: chosen.map((index) => utcText(payload.event_times[index], true)),
      y: chosen.map((index) => payload.event_values[index]),
      yaxis: "y2",
      marker: { size: chosen.map((index) => sizeOf(payload.event_sizes[index])), color: withAlpha(style.color, style.opacity), line: { width: style.ring, color: themeColor("--surface") } },
      customdata: chosen.map((index) => payload.event_sizes[index]),
      name: style.name,
      hovertemplate: `%{x|%Y-%m-%d %H:%M} UTC<br>${richText(payload.event_label)}: %{y:.2e}<br>${richText(payload.size_label)}: %{customdata}<extra>${style.short}</extra>`,
    };
  };
  const stormX = payload.storm_times.map((time) => utcText(time, true));
  const traces = [
    { type: "scatter", mode: "lines", x: payload.sunspot_times.map((time) => utcText(time, true)), y: payload.sunspots, line: { color: withAlpha(sunColor, 0.6), width: 1 }, name: "Monthly sunspot number", hovertemplate: "%{x|%Y-%m}: %{y:.0f}<extra>Monthly sunspot number</extra>" },
    { type: "scatter", mode: "lines", x: payload.smoothed_times.map((time) => utcText(time, true)), y: payload.smoothed, fill: "tozeroy", fillcolor: withAlpha(sunColor, 0.18), line: { color: sunColor, width: 2.5 }, name: "Smoothed sunspot number", hovertemplate: "%{x|%Y-%m}: %{y:.0f}<extra>Smoothed sunspot number</extra>" },
    eventTrace(0),
    eventTrace(1),
    eventTrace(2),
    { type: "scatter", mode: "markers", x: stormX, y: payload.storm_values, yaxis: "y3", marker: { size: 8, color: stormColor, line: { width: 1, color: themeColor("--surface") } }, name: "Intense storm", hovertemplate: `%{x|%Y-%m-%d %H:%M} UTC<br>${richText(payload.storm_label)}: %{y:.0f}<extra>Intense storm</extra>` },
  ];
  const annotations = [
    rowTitle(payload.sunspot_label, domains[0][1]),
    rowTitle(`Each CME as a bubble: height ${payload.event_label}, size ${payload.size_label}`, domains[1][1]),
    rowTitle(`Intense storms (${payload.storm_label} below ${payload.storm_threshold}), the strongest named`, domains[2][1]),
    // Dates of storms close in time are staggered in height and side, so that they do not cover each other: the arrow
    // points left and right in turn (ax), and every other pair is raised by 14 pixels (ay)
    ...payload.labelled.map((index, position) => ({ x: stormX[index], y: payload.storm_values[index], yref: "y3", text: utcText(payload.storm_times[index]).slice(0, 10), showarrow: true, arrowhead: 0, ax: position % 2 === 0 ? -48 : 48, ay: position % 4 < 2 ? 0 : -14, font: { size: 10, color: themeColor("--text") } })),
  ];
  const { shapes, annotations: cycleLabels } = cycleMarks(payload.marks, "cycle ");
  annotations.push(...cycleLabels);
  drawPlot(chart, traces, {
    height: 760,
    margin: { l: 70, r: 20, t: 40, b: 30 },
    hovermode: "closest",
    showlegend: false,
    xaxis: { type: "date", anchor: "y3", gridcolor: themeColor("--grid") },
    yaxis: { domain: domains[0], title: { text: "Sunspots", font: { size: 11 } }, gridcolor: themeColor("--grid"), rangemode: "tozero" },
    yaxis2: { domain: domains[1], type: "log", title: { text: "erg", font: { size: 11 } }, gridcolor: themeColor("--grid"), exponentformat: "power" },
    // The storm row reaches at least down to -150 nT with a quarter more room below the strongest storm, and ends
    // a little above the threshold, which every shown storm lies below
    yaxis3: { domain: domains[2], title: { text: "nT", font: { size: 11 } }, gridcolor: themeColor("--grid"), range: [Math.min(...payload.storm_values, -150) * 1.25, payload.storm_threshold * 0.85] },
    shapes,
    annotations,
  });
  const legend = swatchLegend([
    ["swatch-line", sunColor, "Smoothed sunspot number (thin: monthly)"],
    ...(payload.earth_label ? [["swatch-ring", earthColor, payload.earth_label]] : []),
    ["swatch-ring", flagColor, payload.flag_label],
    ["swatch-ring", otherColor, payload.other_label],
    ["swatch-ring", stormColor, "Intense storm"],
  ]);
  panel.append(legend, chart, linkedCaption(`Cycle maxima (dashed) and minima (dotted) found in the smoothed sunspot number. Intense storms: ${payload.storm_label} below ${payload.storm_threshold} (Gonzalez et al. 1994, doi:10.1029/93JA02867).${payload.earth_until ? ` CMEs that reached Earth: those the near-Earth ICME list of Richardson and Cane names (doi:10.7910/DVN/C2MHTH), up to ${utcText(payload.earth_until).slice(0, 10)}.` : ""} ${payload.event_times.length.toLocaleString("en")} CMEs with known energy, ${payload.storm_times.length} storms.`), freshnessCaption(payload.freshness));
}

/** Draws the typical value of a quantity per bin of another, as bars or as one line per band of a third quantity. */
export function renderGrouped(panel, payload) {
  panel.append(panelTitle(payload.title));
  if (payload.values === 0) {
    panel.append(element("p", "note", "No history stored."));
    return;
  }
  const chart = chartDiv("chart");
  chart.style.height = "380px";
  // Bands of one quantity take shades of one color, the last band the full color; without color the palette order
  const base = payload.color ? colorValue(payload.color) : null;
  const traces = payload.series.map((series, index) => {
    // The shade weakens towards the first band by up to 70 % towards the page surface
    const shaded = base ? shade(base, ((payload.series.length - 1 - index) / Math.max(payload.series.length, 1)) * 0.7) : themeColor(`--series-${index + 1}`);
    const color = payload.band_colors.length > index ? colorValue(payload.band_colors[index]) : shaded;
    const customdata = series.counts;
    const hovertemplate = `%{x}: %{y:.2f}<br>%{customdata} hourly values<extra>${series.label}</extra>`;
    const x = payload.x_ticks;
    return payload.style === "bars"
      ? { type: "bar", x, y: series.values, customdata, marker: { color }, name: series.label, hovertemplate }
      : { type: "scatter", mode: "lines+markers", x, y: series.values, customdata, line: { color, width: 2.5 }, marker: { size: 8, color, line: { width: 2, color: themeColor("--surface") } }, name: series.label, hovertemplate };
  });
  // Each line is named at its end as well as in the legend, so that its band does not rest on color alone
  const endLabels = payload.style === "lines" && payload.series.length > 1
    ? payload.series.map((series, index) => {
        // The last bin with a value
        const last = series.values.map((value, position) => (value === null ? -1 : position)).filter((position) => position >= 0).pop();
        return { x: payload.x_ticks[last], y: series.values[last], text: series.label.split(" (")[0], showarrow: false, xanchor: "left", xshift: 8, font: { size: 11, color: themeColor("--text") } };
      })
    : [];
  drawPlot(chart, traces, {
    height: 380,
    margin: { l: 60, r: payload.style === "lines" ? 95 : 15, t: 30, b: 70 },
    annotations: endLabels,
    bargap: 0.15,
    showlegend: payload.series.length > 1,
    legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0 },
    xaxis: { title: { text: richText(payload.x_label) }, type: "category", tickangle: payload.x_ticks.some((tick) => tick.length > 5) ? -35 : 0, showgrid: false, fixedrange: true },
    yaxis: { title: { text: richText(payload.y_label) }, gridcolor: themeColor("--grid"), rangemode: "tozero", fixedrange: true },
  });
  const since = payload.since ? utcText(payload.since).slice(0, 4) : "?";
  panel.append(chart, linkedCaption(`${payload.caption} From ${payload.values.toLocaleString("en")} hourly values since ${since}.`));
}

/** Draws the typical course of several quantities around all events: median, middle half and the newest event. */
export function renderEpoch(panel, payload) {
  panel.append(panelTitle(payload.title));
  if (payload.events === 0) {
    panel.append(element("p", "note", "No event in the stored history."));
    return;
  }
  // Offsets count steps of payload.step seconds from the extreme of each event
  const hours = payload.offsets.map((offset) => (offset * payload.step) / 3600);
  const domains = rowDomains(payload.rows.length);
  const newest = themeColor("--muted");
  const plotLayout = plainLayout(130 * payload.rows.length + 120);
  // The legend stands above the first row title
  plotLayout.margin.t = 80;
  plotLayout.legend.y = 1.12;
  plotLayout.hovermode = "x unified";
  plotLayout.annotations = [];
  plotLayout.shapes = [verticalLine(0, { dash: "dot", width: 1, color: themeColor("--muted") })];
  const traces = [];
  payload.rows.forEach((row, index) => {
    const { key: axisKey, ref: axis } = rowAxis(index);
    plotLayout[axisKey] = { domain: domains[index], title: { text: richText(row.unit), font: { size: 11 } }, gridcolor: themeColor("--grid"), zerolinecolor: themeColor("--grid") };
    plotLayout.annotations.push(rowTitle(row.label, domains[index][1], 2));
    const first = index === 0;
    const median = colorValue(row.color, themeColor("--series-1"));
    // The band of the middle half fills from the lower quartile up to the invisible upper quartile trace before it
    traces.push(
      { type: "scatter", mode: "lines", x: hours, y: row.upper, yaxis: axis, line: { width: 0 }, hoverinfo: "skip", showlegend: false },
      { type: "scatter", mode: "lines", x: hours, y: row.lower, yaxis: axis, line: { width: 0 }, fill: "tonexty", fillcolor: withAlpha(median, 0.2), name: "Middle half of all storms", legendgroup: "band", showlegend: first, hoverinfo: "skip" },
      { type: "scatter", mode: "lines", x: hours, y: row.median, yaxis: axis, line: { color: median, width: 2 }, name: "Median of all storms", legendgroup: "median", showlegend: first, customdata: row.lower.map((low, position) => [low, row.upper[position]]), hovertemplate: `median %{y:.1f}, middle half %{customdata[0]:.1f} to %{customdata[1]:.1f}<extra>${row.label}</extra>` },
      { type: "scatter", mode: "lines", x: hours, y: row.newest, yaxis: axis, line: { color: newest, width: 1.5, dash: "dot" }, name: `Newest storm (${utcText(payload.newest).slice(0, 10)})`, legendgroup: "newest", showlegend: first, hovertemplate: `newest: %{y:.1f}<extra>${row.label}</extra>` },
    );
  });
  // The dotted line at hour zero, where every event is aligned, gets its own legend entry from a trace without points
  traces.push({ type: "scatter", mode: "lines", x: [null], y: [null], line: { color: themeColor("--muted"), dash: "dot", width: 1 }, name: `Hour 0: the lowest ${payload.event_label} of each storm`, hoverinfo: "skip" });
  plotLayout.xaxis = { anchor: `y${payload.rows.length}`, title: { text: `Hours from the lowest ${payload.event_label}` }, gridcolor: themeColor("--grid"), zeroline: false, minallowed: hours[0], maxallowed: hours[hours.length - 1] };
  const chart = chartDiv("chart");
  chart.style.height = `${plotLayout.height}px`;
  drawPlot(chart, traces, plotLayout);
  const since = utcText(payload.since).slice(0, 4);
  const count = `${payload.events} events with ${payload.event_label} ${payload.below ? "below" : "above"} ${payload.threshold} since ${since}, aligned at their extreme.`;
  panel.append(chart, linkedCaption(payload.caption ? `${payload.caption} ${count}` : count));
}

/**
 * Draws the butterfly diagram as cells of solar rotation and latitude strip, classed by the share of the strip covered by
 * sunspots, with the chart areas of the panel below it on the same time axis.
 */
export function renderButterfly(panel, payload) {
  panel.append(panelTitle(payload.title));
  if (payload.entries === 0) {
    panel.append(element("p", "note", "No sunspot regions stored."));
    return;
  }
  const rows = payload.rows;
  // The diagram takes the upper 45 % of the chart, the areas below share the rest with a gap between each two
  const diagramShare = rows.length > 0 ? 0.45 : 1;
  const below = (1 - diagramShare - ROW_GAP - ROW_GAP * Math.max(rows.length - 1, 0)) / Math.max(rows.length, 1);
  const domains = [[1 - diagramShare, 1], ...rows.map((_, index) => {
    const top = 1 - diagramShare - ROW_GAP - index * (below + ROW_GAP);
    return [Math.max(top - below, 0), top];
  })];
  // Colors of the classes for a growing share of the strip covered by sunspots, grey, red and yellow as in the diagram of
  // Hathaway; fixed in both themes
  const colors = ["#8a96a8", "#e8590c", "#ffd43b"];
  // The class is drawn with discrete colors: each class covers its own third of the color scale, and zmin and zmax
  // put the classes 1, 2 and 3 into the middle of their thirds
  const colorscale = colors.flatMap((color, index) => [[index / colors.length, color], [(index + 1) / colors.length, color]]);
  const centres = payload.rotation_starts.map((start) => utcText(start));
  // Cells are drawn at the centre between their latitude edges
  const latitudeCentres = payload.latitude_edges.slice(0, -1).map((edge, index) => (edge + payload.latitude_edges[index + 1]) / 2);
  const traces = [
    {
      type: "heatmap",
      x: centres,
      y: latitudeCentres,
      z: payload.classes,
      zmin: 0.5,
      zmax: colors.length + 0.5,
      colorscale,
      showscale: false,
      xgap: 0,
      ygap: 0,
      hovertemplate: `rotation from %{x|%Y-%m-%d}, latitude %{y} deg<extra></extra>`,
      name: "Sunspot cover",
    },
  ];
  // Legend entries for the classes, drawn as invisible markers in the class colors
  payload.class_bounds.forEach((bound, index) => {
    traces.push({ type: "scatter", mode: "markers", x: [null], y: [null], marker: { symbol: "square", size: 12, color: colors[index] }, name: `more than ${bound} % of the strip covered`, showlegend: true, hoverinfo: "skip" });
  });
  const plotLayout = {
    height: 330 + 140 * rows.length,
    margin: { l: 60, r: 20, t: 40, b: 30 },
    hovermode: "x unified",
    legend: { orientation: "h", yanchor: "bottom", y: 1.01, xanchor: "left", x: 0 },
    annotations: [],
    yaxis: { domain: domains[0], title: { text: "Latitude (deg)", font: { size: 11 } }, range: [-50, 50], gridcolor: themeColor("--grid"), zerolinecolor: themeColor("--muted") },
  };
  // Position of a series over all areas, which picks its palette color; the areas take the axes after the diagram
  let position = 0;
  rows.forEach((row, index) => {
    const { key: axisName, ref: axis } = rowAxis(index + 1);
    plotLayout[axisName] = { domain: domains[index + 1], title: { text: richText(row.y_title), font: { size: 11 } }, gridcolor: themeColor("--grid"), zerolinecolor: themeColor("--grid") };
    plotLayout.annotations.push(rowTitle(row.title, domains[index + 1][1], 2));
    for (const series of row.series) {
      traces.push(...seriesTraces(series, row, axis, seriesColor(series, position), true).map((trace) => ({ ...trace, showlegend: false })));
      position += 1;
    }
  });
  const start = payload.start;
  const cycle = cycleMarks(payload.marks);
  plotLayout.shapes = cycle.shapes;
  plotLayout.annotations.push(...cycle.annotations);
  plotLayout.xaxis = { type: "date", anchor: `y${rows.length + 1}`, range: [utcText(start), utcText(new Date())], minallowed: utcText(start), maxallowed: utcText(new Date()), gridcolor: themeColor("--grid") };
  const chart = chartDiv("chart");
  chart.style.height = `${plotLayout.height}px`;
  drawPlot(chart, traces, plotLayout);
  panel.append(chart, element("p", "caption", `Cell: one solar rotation (27.3 days) and 2 degrees of latitude, from ${payload.entries.toLocaleString("en")} daily NOAA sunspot regions. Cycle maxima dashed, minima dotted.`), freshnessCaption(payload.freshness));
}
