import { state } from "./state.js";
import { CORONAL_HOLE_COLOR, choiceTabs, element, linkedCaption, panelTitle, shade, themeColor, utcText, withAlpha } from "./core.js";
import { freshnessCaption } from "./freshness.js";
import { chartDiv, cursorShapes, drawPlot, pointsXY, swatchLegend, verticalLine } from "./charts.js";
import { frameIndexAt, onReplay } from "./replay.js";

// Rotation of the Sun seen from the stars in degrees per second: the sidereal Carrington period of 25.38 days
// (NASA Sun Fact Sheet, https://nssdc.gsfc.nasa.gov/planetary/factsheet/sunfact.html)
const SOLAR_ROTATION_DEG_S = 360 / (25.38 * 86400);

// Astronomical unit in kilometres (IAU 2012 Resolution B2, https://www.iau.org/static/resolutions/IAU2012_English.pdf)
const AU_KM = 1.495978707e8;

// Nominal solar radius of 695,700 km in astronomical units (IAU 2015 Resolution B3,
// https://www.iau.org/static/resolutions/IAU2015_English.pdf), where the field lines of the map start
const SOLAR_RADIUS_AU = 6.957e5 / AU_KM;

// Colors of the Sun and its rim in the maps of the solar system; fixed in both themes
export const SUN_COLOR = "#ffd43b";
export const SUN_RIM_COLOR = "#e8590c";

// Speed classes of CMEs by their speed in km/s after the SCORE scale (Evans et al. 2013, doi:10.1002/swe.20058): slow,
// common, occasional, rare, extremely rare; each class is a shade of the CME color, the faster the stronger
const CME_CLASSES = [
  { name: "S", below: 500, shade: 0.6, text: "below 500 km/s" },
  { name: "C", below: 1000, shade: 0.3, text: "500 to 999 km/s" },
  { name: "O", below: 2000, shade: 0, text: "1000 to 1999 km/s" },
  { name: "R", below: 3000, shade: -0.3, text: "2000 to 2999 km/s" },
  { name: "ER", below: Infinity, shade: -0.55, text: "3000 km/s and faster" },
];

// Colors of a shock measured at a body, of an arrival WSA-Enlil expects and of a fast stream from a coronal hole, the
// latter in the color of the coronal holes on the Sun page
export const SHOCK_COLOR = "#ff6b6b";
const EXPECTED_COLOR = "#ffa94d";
const STREAM_COLOR = CORONAL_HOLE_COLOR;

// The reaches of the map in astronomical units: the inner planets and Mars, or out to Jupiter
const HELIO_REACHES = [
  { label: "Out to Mars", au: 1.8 },
  { label: "Out to Jupiter", au: 5.6 },
];

// Height of the map and of the distance chart in pixels
const HELIO_HEIGHT = 520;

/** Returns a speed class entry with its shade of the CME color. */
function classWithColor(entry) {
  return { ...entry, color: shade(themeColor("--theme-cme"), entry.shade) };
}

/** Returns the speed class of a CME speed. */
function cmeClass(speed) {
  return classWithColor(CME_CLASSES.find((entry) => speed < entry.below));
}

/** Returns the value of a daily track at a time, linear between days; angles are unwrapped across +-180 degrees. */
function trackValue(times, values, time, angle = false) {
  if (times.length === 0) return null;
  if (time <= times[0]) return values[0];
  const last = times.length - 1;
  if (time >= times[last]) return values[last];
  const index = frameIndexAt(times, time);
  const share = (time - times[index]) / (times[index + 1] - times[index]);
  let step = values[index + 1] - values[index];
  // An angle step is taken the short way round: shifted into -180 to below 180 degrees, so that a body passing
  // longitude 180 does not swing back across the map
  if (angle) step = ((step + 540) % 360) - 180;
  return values[index] + share * step;
}

/** Returns the place of a body at a time as longitude, latitude and distance; null without positions. */
function bodyAt(body, time) {
  if (body.times.length === 0) return null;
  return {
    longitude: trackValue(body.times, body.longitude, time, true),
    latitude: trackValue(body.times, body.latitude, time),
    distance: trackValue(body.times, body.distance, time),
  };
}

/** Returns the distance of a CME front from the Sun at a time in AU; null before its launch and after its last distance. */
function cmeDistance(cme, time) {
  // The distances are given every step_hours from the launch; the position counts these steps with fraction
  const position = (time - cme.launchMs) / (cme.step_hours * 3600 * 1000);
  if (position < 0 || position > cme.distances.length - 1) return null;
  const index = Math.floor(position);
  const next = Math.min(index + 1, cme.distances.length - 1);
  return cme.distances[index] + (position - index) * (cme.distances[next] - cme.distances[index]);
}

/**
 * Returns the hourly value of a series nearest a time; null without a value within 12 hours. The STEREO A beacon
 * reaches SWPC hours late, and the solar wind changes the winding of a field line only slowly, but a longer gap leaves
 * the line out.
 */
export function hourlyAt(times, values, time) {
  if (times.length === 0) return null;
  const index = frameIndexAt(times, time);
  const nearest = index + 1 < times.length && times[index + 1] - time < time - times[index] ? index + 1 : index;
  return Math.abs(times[nearest] - time) <= 12 * 3600 * 1000 ? values[nearest] : null;
}

/** Converts a heliographic longitude in degrees and a distance into map coordinates with Earth to the right. */
export function mapPoint(longitude, distance) {
  const angle = (longitude * Math.PI) / 180;
  return [distance * Math.cos(angle), distance * Math.sin(angle)];
}

/**
 * Returns the longitude in degrees at a distance in AU of the field line through a body, as a Parker spiral for the
 * wind speed in km/s: the Sun turns on while the wind carries the field out at that speed, which winds the line into
 * a spiral (Parker 1958, doi:10.1086/146579).
 */
export function spiralLongitude(bodyLongitude, bodyDistance, distance, speed) {
  return bodyLongitude + (SOLAR_ROTATION_DEG_S * (bodyDistance - distance) * AU_KM) / speed;
}

/** Tells whether a CME cone around its direction contains a place given by longitude and latitude. */
function coneContains(cme, longitude, latitude) {
  const toRadians = Math.PI / 180;
  // Spherical law of cosines: the cosine of the angle between the CME direction and the place
  const cosine = Math.sin(cme.latitude * toRadians) * Math.sin(latitude * toRadians) + Math.cos(cme.latitude * toRadians) * Math.cos(latitude * toRadians) * Math.cos((cme.longitude - longitude) * toRadians);
  // Rounding can push the cosine just beyond +-1, where acos gives no number
  return Math.acos(Math.min(1, Math.max(-1, cosine))) / toRadians <= cme.half_angle;
}

/** Tells whether a CME reaches a body: WSA-Enlil expects it somewhere, a shock linked to it was measured, or its cone holds Earth. */
function cmeReachesBody(cme) {
  return cme.expected.length > 0 || cme.observed.length > 0 || coneContains(cme, 0, 0);
}

/** Returns the arrivals of a list lying within 12 hours of a time, at which a body counts as being hit. */
function arrivalsNear(arrivals, time) {
  return arrivals.filter((arrival) => Math.abs(arrival.timeMs - time) <= 12 * 3600 * 1000);
}

/** Builds the Plotly traces of the map of the inner solar system at one time; the map is widthPx pixels wide. */
function heliosphereMap(payload, cmes, time, reach, widthPx) {
  const traces = [];
  const muted = themeColor("--muted");
  // Bodies a little beyond the reach still show, cut at the edge of the map
  const places = payload.bodies.map((body) => ({ body, place: bodyAt(body, time) })).filter(({ place }) => place !== null && place.distance <= reach * 1.05);
  // Orbits of the planets as circles at their present distance, in steps of 3 degrees; null breaks the line between
  // two orbits, so that one trace holds all of them
  const orbitX = [];
  const orbitY = [];
  for (const { body, place } of places) {
    if (body.kind !== "planet") continue;
    for (let step = 0; step <= 120; step++) {
      const [x, y] = mapPoint(step * 3, place.distance);
      orbitX.push(x);
      orbitY.push(y);
    }
    orbitX.push(null);
    orbitY.push(null);
  }
  traces.push({ type: "scatter", mode: "lines", x: orbitX, y: orbitY, line: { color: themeColor("--grid"), width: 1 }, hoverinfo: "skip", showlegend: false });
  // Field lines through the bodies with measured wind, wound by the speed of the wind of the hour, from the solar
  // surface to half again beyond the reach in 150 steps per reach
  payload.winds.forEach((wind, position) => {
    const body = payload.bodies.find((candidate) => candidate.name === wind.body);
    const place = body ? bodyAt(body, time) : null;
    const speed = hourlyAt(wind.times, wind.speeds, time);
    if (place === null || speed === null) return;
    const x = [];
    const y = [];
    for (let distance = SOLAR_RADIUS_AU; distance <= reach * 1.5; distance += reach / 150) {
      const [pointX, pointY] = mapPoint(spiralLongitude(place.longitude, place.distance, distance, speed), distance);
      x.push(pointX);
      y.push(pointY);
    }
    const color = position === 0 ? themeColor("--series-1") : themeColor("--series-5");
    traces.push({ type: "scatter", mode: "lines", x, y, line: { color: withAlpha(color, 0.7), width: 1.5, dash: "dot" }, hoverinfo: "skip", name: `Field line through ${wind.body} (${Math.round(speed)} km/s)` });
  });
  // CME fronts as arcs over the width they cut out of the plane of the planets, their flanks back to the Sun; a front
  // fades on its way out, so that the CMEs just launched stand out
  for (const cme of cmes) {
    const distance = cmeDistance(cme, time);
    if (distance === null || distance > reach) continue;
    const color = cmeClass(cme.speed).color;
    const inPlane = cme.ecliptic_half_width !== null;
    // Full strength at the Sun, 30 % at the edge of the map
    const strength = 1 - 0.7 * (distance / reach);
    const half = inPlane ? cme.ecliptic_half_width : cme.half_angle;
    const x = [];
    const y = [];
    // The outline runs from the Sun to one flank, along the front in 24 steps across twice the half width, and back
    const [flankX, flankY] = mapPoint(cme.longitude - half, distance);
    x.push(0, flankX);
    y.push(0, flankY);
    for (let step = 0; step <= 24; step++) {
      const [pointX, pointY] = mapPoint(cme.longitude - half + (step / 12) * half, distance);
      x.push(pointX);
      y.push(pointY);
    }
    x.push(0);
    y.push(0);
    const text = `CME of ${utcText(cme.start)} UTC, ${Math.round(cme.speed)} km/s, now at ${distance.toFixed(2)} AU${inPlane ? "" : ", passing above or below the planets"}`;
    // hoveron "fills+points" shows the text over the whole area of the CME, not only at its outline
    traces.push({ type: "scatter", mode: "lines", x, y, fill: "toself", fillcolor: withAlpha(color, (inPlane ? 0.12 : 0.04) * strength), line: { color: withAlpha(color, (inPlane ? 1 : 0.5) * strength), width: inPlane ? 2.5 : 1, dash: inPlane ? "solid" : "dot" }, text, hoveron: "fills+points", hovertemplate: "%{text}<extra></extra>", showlegend: false });
  }
  traces.push({ type: "scatter", mode: "markers", x: [0], y: [0], marker: { size: 16, color: SUN_COLOR, line: { width: 1, color: SUN_RIM_COLOR } }, hovertemplate: "Sun<extra></extra>", showlegend: false });
  // Bodies hit now: a ring for an arrival WSA-Enlil expects, a thick red ring for a shock measured there
  const expectedNow = new Set(cmes.flatMap((cme) => arrivalsNear(cme.expected, time).map((arrival) => arrival.location)));
  const shockedNow = new Set(arrivalsNear(payload.shocks, time).map((arrival) => arrival.location));
  const streamNow = new Set(arrivalsNear(payload.streams, time).map((arrival) => arrival.location));
  const ringTrace = (names, color, width, label) => {
    const hit = places.filter(({ body }) => names.has(body.name));
    const points = hit.map(({ place }) => mapPoint(place.longitude, place.distance));
    return { type: "scatter", mode: "markers", ...pointsXY(points), marker: { symbol: "circle-open", size: 24, color, line: { width } }, text: hit.map(({ body }) => body.name), hovertemplate: `%{text}: ${label}<extra></extra>`, showlegend: false };
  };
  traces.push(ringTrace(streamNow, STREAM_COLOR, 2, "fast stream from a coronal hole"), ringTrace(expectedNow, EXPECTED_COLOR, 2, "CME arrival expected by WSA-Enlil"), ringTrace(shockedNow, SHOCK_COLOR, 4, "shock measured"));
  // Labels go to Earth, the bodies hit and the planets first; a label that would cover another one or a body is left
  // to the hover. Labels stand right of bodies on the right half and left of them on the left half.
  const priority = ({ body }) => (body.name === "Earth" ? 0 : expectedNow.has(body.name) || shockedNow.has(body.name) ? 1 : body.kind === "planet" ? 2 : 3);
  // Label sizes are known in pixels; the map spans twice the reach over its width
  const auPerPx = (2 * reach) / widthPx;
  for (const entry of places) [entry.x, entry.y] = mapPoint(entry.place.longitude, entry.place.distance);
  // Every body takes a box of 10 pixels, which no label may cover
  const boxes = places.map((entry) => ({ left: entry.x - 5 * auPerPx, right: entry.x + 5 * auPerPx, bottom: entry.y - 5 * auPerPx, top: entry.y + 5 * auPerPx, owner: entry }));
  const overlaps = (first, second) => first.left < second.right && second.left < first.right && first.bottom < second.top && second.bottom < first.top;
  for (const entry of [...places].sort((first, second) => priority(first) - priority(second))) {
    // A label box is about 7 pixels per character plus 8 pixels of space wide and 14 pixels high, centred on the body
    const width = (entry.body.name.length * 7 + 8) * auPerPx;
    const half = 7 * auPerPx;
    const box = entry.x >= 0 ? { left: entry.x, right: entry.x + width } : { left: entry.x - width, right: entry.x };
    Object.assign(box, { bottom: entry.y - half, top: entry.y + half, owner: entry });
    // A label may cover the box of its own body only
    entry.label = boxes.every((other) => other.owner === entry || !overlaps(box, other));
    if (entry.label) boxes.push(box);
  }
  for (const kind of ["planet", "spacecraft"]) {
    const shown = places.filter(({ body }) => body.kind === kind);
    traces.push({
      type: "scatter",
      mode: "markers+text",
      x: shown.map((entry) => entry.x),
      y: shown.map((entry) => entry.y),
      text: shown.map((entry) => (entry.label ? entry.body.name : "")),
      customdata: shown.map((entry) => `${entry.body.name}, ${entry.place.distance.toFixed(2)} AU from the Sun`),
      textposition: shown.map((entry) => (entry.x >= 0 ? "middle right" : "middle left")),
      textfont: { size: 11, color: themeColor("--text") },
      marker: kind === "planet" ? { size: shown.map((entry) => (entry.body.name === "Earth" ? 11 : 9)), color: shown.map((entry) => (entry.body.name === "Earth" ? themeColor("--series-1") : muted)) } : { symbol: "diamond", size: 8, color: themeColor("--muted") },
      hovertemplate: "%{customdata}<extra></extra>",
      cliponaxis: false,
      showlegend: false,
    });
  }
  return traces;
}

/** Builds the Plotly traces of the distances of CME fronts and bodies over time, with measured and expected arrivals. */
function heliosphereDistances(payload, cmes, reach) {
  const traces = [];
  const start = Date.parse(payload.start);
  const stop = Date.parse(payload.stop);
  const annotations = [];
  for (const body of payload.bodies) {
    // The distance of each body every six hours, named at the right end of its line
    const times = [];
    const distances = [];
    for (let time = start; time <= stop; time += 6 * 3600 * 1000) {
      times.push(utcText(new Date(time), true));
      distances.push(bodyAt(body, time).distance);
    }
    if (Math.min(...distances) > reach) continue;
    const earth = body.name === "Earth";
    traces.push({ type: "scatter", mode: "lines", x: times, y: distances, line: { color: earth ? themeColor("--series-1") : withAlpha(themeColor("--muted"), body.kind === "planet" ? 0.8 : 0.45), width: earth ? 2 : 1, dash: body.kind === "planet" ? "solid" : "dot" }, hovertemplate: `${body.name}: %{y:.2f} AU<extra></extra>`, showlegend: false });
    annotations.push({ x: times[times.length - 1], y: distances[distances.length - 1], text: body.name, showarrow: false, xanchor: "left", xshift: 4, font: { size: 10, color: earth ? themeColor("--series-1") : themeColor("--muted") } });
  }
  // Names at the right edge are pushed apart from the bottom up, at least 4 % of the reach, so that bodies at similar
  // distances stay readable
  const gap = reach * 0.04;
  annotations.sort((first, second) => first.y - second.y);
  annotations.forEach((annotation, index) => {
    if (index > 0) annotation.y = Math.max(annotation.y, annotations[index - 1].y + gap);
  });
  for (const cme of cmes) {
    const color = cmeClass(cme.speed).color;
    const towardsEarth = coneContains(cme, 0, 0);
    const times = cme.distances.map((_, index) => utcText(new Date(cme.launchMs + index * cme.step_hours * 3600 * 1000), true));
    traces.push({ type: "scatter", mode: "lines", x: times, y: cme.distances, line: { color: withAlpha(color, towardsEarth ? 1 : 0.45), width: towardsEarth ? 2.5 : 1.2 }, hovertemplate: `CME of ${utcText(cme.start)} UTC, ${Math.round(cme.speed)} km/s${towardsEarth ? ", towards Earth" : ""}: %{y:.2f} AU<extra></extra>`, showlegend: false });
  }
  // Arrivals sit on the line of the body they meet; arrivals at places without a track are left out
  const arrivalMarks = (arrivals, symbol, color, label) => {
    const known = arrivals.map((arrival) => ({ arrival, body: payload.bodies.find((body) => body.name === arrival.location) })).filter(({ body }) => body !== undefined);
    return {
      type: "scatter",
      mode: "markers",
      x: known.map(({ arrival }) => utcText(new Date(arrival.timeMs), true)),
      y: known.map(({ arrival, body }) => bodyAt(body, arrival.timeMs).distance),
      marker: { symbol, size: 9, color, line: { width: 1.5, color } },
      text: known.map(({ arrival }) => `${arrival.location}: ${label}${arrival.glancing ? ", grazing" : ""}, ${utcText(new Date(arrival.timeMs))} UTC`),
      hovertemplate: "%{text}<extra></extra>",
      showlegend: false,
    };
  };
  traces.push(arrivalMarks(payload.streams, "triangle-up", STREAM_COLOR, "fast stream from a coronal hole"));
  traces.push(arrivalMarks(cmes.flatMap((cme) => cme.expected), "circle-open", EXPECTED_COLOR, "arrival expected by WSA-Enlil"));
  traces.push(arrivalMarks(payload.shocks, "circle", SHOCK_COLOR, "shock measured"));
  return { traces, annotations };
}

/**
 * Draws the inner solar system from above beside the distance of every CME front over time: planets and spacecraft
 * at their place, the field lines bent by the measured wind, the CMEs flying out, and the bodies they meet.
 */
export function renderHeliosphere(panel, payload) {
  panel.append(panelTitle(payload.title));
  // Times are parsed once into milliseconds, as every replay step compares them
  for (const cme of payload.cmes) {
    cme.launchMs = Date.parse(cme.launch);
    for (const arrival of [...cme.expected, ...cme.observed]) arrival.timeMs = Date.parse(arrival.time);
  }
  for (const arrival of [...payload.shocks, ...payload.streams]) arrival.timeMs = Date.parse(arrival.time);
  for (const body of payload.bodies) body.times = body.times.map(Date.parse);
  for (const wind of payload.winds) wind.times = wind.times.map(Date.parse);
  if (payload.bodies.length === 0) {
    panel.append(element("p", "note", "No positions stored yet."));
    return;
  }
  const reachKey = `${state.drawingPanel.id}:reach`;
  const cmeKey = `${state.drawingPanel.id}:cmes`;
  const reachTabs = choiceTabs(reachKey, HELIO_REACHES.map((choice, position) => [choice.label, position]), 0, () => draw()).tabs;
  // The map draws the CMEs that reach a planet or spacecraft, or all of them
  const cmeTabs = choiceTabs(cmeKey, [["CMEs that reach a planet or spacecraft", 0], ["All CMEs", 1]], 0, () => draw()).tabs;
  const choices = element("div", "helio-choices");
  choices.append(reachTabs, cmeTabs);
  const legend = swatchLegend([
    ...CME_CLASSES.map(classWithColor).map((entry) => ["swatch-line", entry.color, `CME ${entry.text}`]),
    ["swatch-ring", EXPECTED_COLOR, "Arrival expected by WSA-Enlil"],
    ["swatch-ring", SHOCK_COLOR, "Shock measured"],
    ["swatch-ring", STREAM_COLOR, "Fast stream from a coronal hole at Earth"],
  ]);
  const views = element("div", "helio-views");
  const map = chartDiv("chart helio-map");
  const distances = chartDiv("chart helio-distances");
  map.style.height = `${HELIO_HEIGHT}px`;
  distances.style.height = `${HELIO_HEIGHT}px`;
  views.append(map, distances);
  const time = element("p", "caption helio-time");
  const caption = linkedCaption("Seen from above the Sun's north pole, Earth to the right. Positions: JPL Horizons. Spirals: Parker spiral for the measured wind speed (Parker 1958, doi:10.1086/146579). CMEs: direction and width from DONKI, front moved from 21.5 solar radii with the drag-based model (Vrsnak et al. 2013, doi:10.1007/s11207-012-0035-4); dotted where the CME passes above or below the planets.");
  panel.append(choices, legend, views, time, caption, freshnessCaption(payload.freshness));
  panel.replaySpan = [Date.parse(payload.start), Date.parse(payload.stop)];
  const now = Date.now();
  // The distance chart changes only with the reach and the CME choice, not with the replay time
  let drawnDistances = null;
  const draw = () => {
    const reachIndex = state.panelChoices.get(reachKey) ?? 0;
    const reach = HELIO_REACHES[reachIndex].au;
    const cmeChoice = state.panelChoices.get(cmeKey) ?? 0;
    const cmes = cmeChoice === 0 ? payload.cmes.filter(cmeReachesBody) : payload.cmes;
    const moment = state.replayTime ?? now;
    // More than a minute past the time of drawing, the map shows planned positions and forecasts only
    time.textContent = `${utcText(new Date(moment))} UTC${moment > now + 60000 ? ", ahead of now: positions and expected arrivals only" : ""}`;
    // Fixed axes of equal scale (scaleanchor), so that circles stay round and the map does not zoom
    const axis = { range: [-reach, reach], zeroline: false, showgrid: false, showticklabels: false, fixedrange: true, constrain: "domain" };
    // The map is square, at most as wide as it is high; before layout the element has no width yet
    drawPlot(map, heliosphereMap(payload, cmes, moment, reach, Math.min(map.clientWidth || HELIO_HEIGHT, HELIO_HEIGHT)), {
      height: HELIO_HEIGHT,
      margin: { l: 5, r: 5, t: 5, b: 5 },
      showlegend: true,
      legend: { orientation: "h", x: 0, y: 0, yanchor: "bottom", font: { size: 10 }, bgcolor: "rgba(0,0,0,0)" },
      xaxis: axis,
      yaxis: { ...axis, scaleanchor: "x" },
      uirevision: `reach-${reach}`,
    });
    if (drawnDistances !== `${reach}-${cmeChoice}`) {
      const { traces, annotations } = heliosphereDistances(payload, cmes, reach);
      drawPlot(distances, traces, {
        height: HELIO_HEIGHT,
        // The wide right margin holds the names of the bodies at the ends of their lines
        margin: { l: 55, r: 120, t: 10, b: 35 },
        showlegend: false,
        annotations,
        xaxis: { type: "date", range: [payload.start, payload.stop], gridcolor: themeColor("--grid") },
        yaxis: { title: { text: "Distance from the Sun (AU)" }, range: [0, reach], gridcolor: themeColor("--grid"), zeroline: false },
        uirevision: `reach-${reach}`,
      });
      drawnDistances = `${reach}-${cmeChoice}`;
    }
    const nowLine = verticalLine(utcText(new Date(now), true), { dash: "dash", width: 1, color: themeColor("--muted") });
    Plotly.relayout(distances, { shapes: [nowLine, ...cursorShapes(state.replayTime)] });
  };
  draw();
  onReplay(draw);
}
