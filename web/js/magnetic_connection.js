import { state } from "./state.js";
import { element, linkedCaption, panelTitle, richText, themeColor, utcText, withAlpha } from "./core.js";
import { freshnessCaption } from "./freshness.js";
import { chartDiv, cursorShapes, drawPlot, horizontalLine, pointsXY, rowAxis, rowDomains, rowTitle, swatchLegend, verticalLine } from "./charts.js";
import { onReplay } from "./replay.js";
import { SHOCK_COLOR, SUN_COLOR, SUN_RIM_COLOR, hourlyAt, mapPoint, spiralLongitude } from "./heliosphere.js";

// Radius of the enlarged Sun in the view of the field lines, in astronomical units of the drawing; distances from the
// Sun are stretched onto the space between its rim and the observers, so that each line ends at its true footpoint
const CONNECTION_SUN = 0.3;

// Colors of the observers, the same as their field lines in the map of the solar system
const OBSERVER_COLORS = ["--series-1", "--series-5"];

/** Returns the color of a flare by its angle to a footpoint: bright for well connected flares, grey for distant ones. */
function connectionColor(angle, width) {
  if (angle === null || angle === undefined) return themeColor("--muted");
  // Gaussian closeness, 1 at the footpoint and about 0.6 at the band width; the color runs from a translucent grey
  // rgba(120,130,140,0.35) at 0 to an opaque red-orange rgb(255,70,40) at 1
  const closeness = Math.exp(-0.5 * (angle / width) ** 2);
  return `rgba(${Math.round(120 + 135 * closeness)},${Math.round(130 - 60 * closeness)},${Math.round(140 - 100 * closeness)},${0.35 + 0.65 * closeness})`;
}

/** Returns the smallest angle of a flare to the footpoint of any observer; null if none is known. */
function closestAngle(flare) {
  const angles = Object.values(flare.connection_angles).filter((angle) => angle !== null);
  return angles.length === 0 ? null : angles.reduce((best, angle) => (Math.abs(angle) < Math.abs(best) ? angle : best));
}

/** Returns the marker size of a flare by its X-ray class letter. */
function flareSize(label) {
  return { X: 18, M: 13, C: 9 }[label.charAt(0)] ?? 7;
}

/** Writes a heliographic longitude in degrees as W or E of the central meridian, as flare positions are given. */
function longitudeText(longitude) {
  return `${longitude >= 0 ? "W" : "E"}${Math.abs(longitude).toFixed(0)}`;
}

/** Returns the flare markers of a chart: diamonds sized by class, colored by the angle `angleOf` gives for a flare. */
function flareMarker(flares, angleOf, width) {
  // A thick ring in the shock color marks the flares DONKI links to a particle event
  return { size: flares.map((flare) => flareSize(flare.label)), color: flares.map((flare) => connectionColor(angleOf(flare), width)), symbol: "diamond", line: { width: flares.map((flare) => (flare.particles ? 3 : 0)), color: SHOCK_COLOR } };
}

/** Converts a longitude and a distance from the Sun in AU into the stretched drawing of the field lines, Earth to the right. */
function connectionPoint(longitude, distance) {
  return mapPoint(longitude, CONNECTION_SUN + distance * (1 - CONNECTION_SUN));
}

/** Builds the traces of the view from above: the enlarged Sun with its flares, the observers and their field lines. */
function connectionMap(payload, moment) {
  const traces = [];
  // An arc of 60 steps at a radius between two longitudes in degrees
  const ring = (radius, from, to) => Array.from({ length: 61 }, (_, step) => mapPoint(from + ((to - from) * step) / 60, radius));
  // A half disk is its arc closed through the centre, so that fill "toself" fills it
  const halfDisk = (from, to) => pointsXY([[0, 0], ...ring(CONNECTION_SUN, from, to), [0, 0]]);
  traces.push({ type: "scatter", mode: "lines", ...halfDisk(90, 270), fill: "toself", fillcolor: withAlpha(SUN_RIM_COLOR, 0.12), line: { width: 0 }, hovertemplate: "Far side of the Sun, not seen from Earth<extra></extra>" });
  traces.push({ type: "scatter", mode: "lines", ...halfDisk(-90, 90), fill: "toself", fillcolor: withAlpha(SUN_COLOR, 0.35), line: { width: 0 }, hovertemplate: "Side of the Sun facing Earth<extra></extra>" });
  traces.push({ type: "scatter", mode: "lines", ...pointsXY(ring(CONNECTION_SUN, 0, 360)), line: { color: SUN_RIM_COLOR, width: 1.5 }, hoverinfo: "skip" });
  payload.observers.forEach((observer, position) => {
    const color = themeColor(OBSERVER_COLORS[position % OBSERVER_COLORS.length]);
    // The index of the hourly value nearest the moment, the same for all series of the observer
    const times = observer.times.map(Date.parse);
    const index = hourlyAt(times, times.map((_, item) => item), moment);
    if (index === null) return;
    const foot = observer.footpoints[index];
    const longitude = observer.longitudes[index];
    const distance = observer.distances[index];
    const speed = observer.wind_speeds[index];
    // The band of good connection as a ring segment on the rim, 30 % of the solar radius deep: the outer arc one way,
    // the inner arc back
    const outer = ring(CONNECTION_SUN * 1.3, foot - payload.width, foot + payload.width);
    const inner = ring(CONNECTION_SUN, foot + payload.width, foot - payload.width);
    traces.push({ type: "scatter", mode: "lines", ...pointsXY([...outer, ...inner, outer[0]]), fill: "toself", fillcolor: withAlpha(color, 0.25), line: { width: 0 }, hovertemplate: `Well connected to ${observer.name}: within ${payload.width} degrees of the foot<extra></extra>` });
    // The field line from the Sun to the observer in 80 steps
    const line = [];
    for (let step = 0; step <= 80; step++) {
      const along = (distance * step) / 80;
      line.push(connectionPoint(spiralLongitude(longitude, distance, along, speed), along));
    }
    traces.push({ type: "scatter", mode: "lines", ...pointsXY(line), line: { color, width: 2.5 }, hovertemplate: `Field line through ${observer.name}, solar wind ${Math.round(speed)} km/s, foot ${longitudeText(foot)}<extra></extra>` });
    const [x, y] = connectionPoint(longitude, distance);
    traces.push({ type: "scatter", mode: "markers+text", x: [x], y: [y], text: [observer.name], textposition: y >= 0 ? "top center" : "bottom center", textfont: { size: 11, color }, marker: { size: 11, color, symbol: position === 0 ? "circle" : "diamond" }, hoverinfo: "skip", cliponaxis: false });
  });
  // Flares up to the moment, just inside the rim at their longitude
  const shown = payload.flares.filter((flare) => flare.timeMs <= moment);
  const points = shown.map((flare) => mapPoint(flare.longitude, CONNECTION_SUN * 0.85));
  traces.push({
    type: "scatter",
    mode: "markers",
    ...pointsXY(points),
    marker: flareMarker(shown, closestAngle, payload.width),
    text: shown.map((flare) => `${flare.label} flare of ${utcText(flare.time)} UTC at ${longitudeText(flare.longitude)}; ${Object.entries(flare.connection_angles).filter(([, angle]) => angle !== null).map(([name, angle]) => `${Math.abs(angle).toFixed(0)} degrees from the foot of ${name}`).join(", ")}`),
    hovertemplate: "%{text}<extra></extra>",
  });
  return traces;
}

/**
 * Draws where the magnetic field lines through Earth and STEREO A meet the Sun, computed from the wind measured at
 * each, with the flares colored by how close they lie to a footpoint, and the protons measured at each observer.
 */
export function renderMagneticConnection(panel, payload) {
  panel.append(panelTitle(payload.title));
  for (const flare of payload.flares) flare.timeMs = Date.parse(flare.time);
  const views = element("div", "helio-views");
  const view = chartDiv("chart connection-disk");
  const flux = chartDiv("chart connection-flux");
  // Both charts take the height of the proton chart, one area per observer
  const height = 160 + 170 * payload.observers.length;
  view.style.height = `${height}px`;
  flux.style.height = `${height}px`;
  views.append(view, flux);
  const legend = swatchLegend([
    ...payload.observers.map((observer, position) => ["swatch-line", themeColor(OBSERVER_COLORS[position % OBSERVER_COLORS.length]), `Field line through ${observer.name}`]),
    ["swatch-line", connectionColor(0, payload.width), "Flare near a footpoint"],
    ["swatch-line", connectionColor(null, payload.width), "Flare far from it"],
    ["swatch-ring", SHOCK_COLOR, "Flare DONKI links to a particle event"],
  ]);
  const caption = linkedCaption(`Sun enlarged, seen from above its north pole, Earth to the right. Field lines: Parker spiral for the wind measured at each spacecraft (Parker 1958, doi:10.1086/146579). Bands: ${payload.width} degrees around each footpoint, the spread of particle events found by Richardson et al. (2014), doi:10.1007/s11207-014-0524-8. The STEREO A beacon gives its protons without a unit.`);
  panel.append(legend, views, caption, freshnessCaption(payload.freshness));
  const traces = [];
  const shapes = [];
  const annotations = [];
  // The shared time axis is drawn under the lowest area
  const plotLayout = { height, margin: { l: 60, r: 20, t: 30, b: 35 }, showlegend: false, xaxis: { type: "date", range: [payload.start, utcText(new Date(), true)], gridcolor: themeColor("--grid"), anchor: rowAxis(payload.observers.length - 1).ref } };
  const domains = rowDomains(payload.observers.length);
  payload.observers.forEach((observer, position) => {
    const axis = rowAxis(position);
    // Protons keep the particle color on every page; the observer is named by the row title
    const color = themeColor("--theme-particles");
    // The log axis reaches at least one power of ten below the peak, with room above it for the flare markers
    const peak = Math.max(1, ...observer.fluxes.flatMap((series) => series.values));
    const low = Math.min(peak / 10, ...observer.fluxes.flatMap((series) => series.values));
    observer.fluxes.forEach((series, index) => {
      traces.push({ type: "scattergl", mode: "lines", x: series.times.map((time) => utcText(time, true)), y: series.values, yaxis: axis.ref, line: { color, width: 1.5, dash: index === 0 ? "solid" : "dot" }, name: series.label, hovertemplate: `%{y:.3g} ${richText(series.unit)}<extra>${observer.name}: ${series.label}</extra>` });
    });
    // Flares stand in a line above the protons, six times the peak
    const flareY = peak * 6;
    const angleText = (flare) => {
      const angle = flare.connection_angles[observer.name];
      return angle === null || angle === undefined ? "" : `, ${Math.abs(angle).toFixed(0)} degrees from the foot of ${observer.name}`;
    };
    traces.push({ type: "scatter", mode: "markers", x: payload.flares.map((flare) => utcText(flare.time, true)), y: payload.flares.map(() => flareY), yaxis: axis.ref, marker: flareMarker(payload.flares, (flare) => flare.connection_angles[observer.name], payload.width), text: payload.flares.map((flare) => `${flare.label} flare${angleText(flare)}`), hovertemplate: "%{text}<extra></extra>" });
    const unit = observer.fluxes[0]?.unit || "rate, no unit given";
    // Plotly takes the range of a log axis as exponents of ten; the margins of 0.3 and 0.5 keep the lines off the edges
    plotLayout[axis.key] = { type: "log", domain: domains[position], range: [Math.log10(low) - 0.3, Math.log10(flareY) + 0.5], title: { text: richText(unit), font: { size: 11 } }, gridcolor: themeColor("--grid"), exponentformat: "power" };
    const labels = observer.fluxes.map((series, index) => `${index === 0 ? "solid" : "dotted"} ${series.label}`).join(", ");
    annotations.push(rowTitle(`Protons at ${observer.name}: ${labels}`, domains[position][1]));
    if (position === 0) {
      // The S1 radiation storm starts at 10 particle flux units (NOAA Space Weather Scales,
      // https://www.swpc.noaa.gov/noaa-scales-explanation); the first observer measures in that unit
      shapes.push(horizontalLine(10, axis.ref, { dash: "dot", width: 1, color: themeColor("--stale") }));
      annotations.push({ xref: "paper", yref: axis.ref, x: 1, y: 1, text: `S1 radiation storm (${observer.fluxes[0]?.label ?? ""})`, showarrow: false, xanchor: "right", yanchor: "bottom", font: { size: 10, color: themeColor("--stale") } });
    }
  });
  for (const event of payload.particle_events) shapes.push(verticalLine(utcText(event.time, true), { dash: "dot", width: 1.5, color: SHOCK_COLOR }));
  drawPlot(flux, traces, { ...plotLayout, shapes, annotations });
  const draw = () => {
    const moment = state.replayTime ?? Date.now();
    // Fixed axes of equal scale (scaleanchor), reaching 1.2 drawing units from the Sun, just beyond the observers at 1
    const axis = { range: [-1.2, 1.2], zeroline: false, showgrid: false, showticklabels: false, fixedrange: true, constrain: "domain" };
    drawPlot(view, connectionMap(payload, moment), { height, margin: { l: 5, r: 5, t: 10, b: 5 }, showlegend: false, xaxis: axis, yaxis: { ...axis, scaleanchor: "x" } });
    // Only the replay cursor of the proton chart moves; its data stay
    Plotly.relayout(flux, { shapes: [...shapes, ...cursorShapes(state.replayTime)] });
  };
  draw();
  onReplay(draw);
}
