import { state } from "./state.js";
import { choiceTabs, element, linkedCaption, loadJson, panelTitle, themeColor, utcText } from "./core.js";
import { freshnessCaption } from "./freshness.js";
import { chartDiv, cursorShapes, drawPlot, plainLayout } from "./charts.js";
import { frameIndexAt, frameNear, frameReach, onReplay } from "./replay.js";
import { frameLoop } from "./video.js";
import { spherePoint, threeViewer } from "./viewer3d.js";

// Distance of the camera of the globe from the centre of the Earth in Earth radii
const GLOBE_CAMERA_DISTANCE = 4.2;

// Geographic latitude the camera looks down from onto the night side of each hemisphere
const GLOBE_VIEW_LATITUDE = 58;

// Probability of aurora in percent at which the color of a cell is green, yellow and red; cells below the lowest one
// are not drawn by the source
const AURORA_LOWEST = 5;
const AURORA_YELLOW = 30;
const AURORA_RED = 60;

// Vertex shader of both spheres of the globe: hands the direction of each point from the centre to the fragment shader
const SPHERE_VERTEX_SHADER = "varying vec3 vNormal; void main() { vNormal = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }";

// Coastlines as GeoJSON, loaded once on first use
let coastlines = null;

// Loaded aurora maps by URL, so that a played day is fetched once
const auroraMaps = new Map();

/** Returns the coastlines as GeoJSON, loaded on first use. */
function loadCoastlines() {
  // Coastlines of Natural Earth at 1:110 million (public domain), self-hosted
  coastlines ??= fetch("vendor/naturalearth/ne_110m_coastline.geojson").then((response) => response.json());
  return coastlines;
}

/** Returns a stored aurora map, loaded on first use. */
function auroraMap(url) {
  if (!auroraMaps.has(url)) auroraMaps.set(url, loadJson(url));
  return auroraMaps.get(url);
}

/**
 * Returns the points of a circle of constant geomagnetic latitude around the pole of the geomagnetic dipole, as
 * positions on a sphere of the given radius.
 */
function geomagneticCircle(magneticLatitude, poleLatitude, poleLongitude, radius) {
  const pole = spherePoint(poleLatitude, poleLongitude);
  // Two unit directions across the dipole axis span the plane of the circle: the first is the pole direction turned by
  // 90 degrees in the equator plane, the second the cross product pole x first
  const across = [-pole[1], pole[0], 0];
  const length = Math.hypot(...across);
  const first = across.map((value) => value / length);
  const second = [pole[1] * first[2] - pole[2] * first[1], pole[2] * first[0] - pole[0] * first[2], pole[0] * first[1] - pole[1] * first[0]];
  // A point of the circle lies sin(latitude) along the axis and cos(latitude) away from it
  const along = Math.sin((magneticLatitude * Math.PI) / 180);
  const away = Math.cos((magneticLatitude * Math.PI) / 180);
  const positions = [];
  // 180 steps of 2 degrees around the axis, the last point closing the circle on the first
  for (let step = 0; step <= 180; step++) {
    const angle = (step * 2 * Math.PI) / 180;
    for (let axis = 0; axis < 3; axis++) positions.push(radius * (along * pole[axis] + away * (Math.cos(angle) * first[axis] + Math.sin(angle) * second[axis])));
  }
  return positions;
}

/** Returns the color of a cell by its probability of aurora, green over yellow to red, as RGB from 0 to 1. */
function auroraColor(probability) {
  // The same colors as the scale in the legend: rgb(61,219,151), rgb(255,212,59), rgb(255,77,77)
  const green = [0.24, 0.86, 0.59];
  const yellow = [1, 0.83, 0.23];
  const red = [1, 0.3, 0.3];
  // Linear mix per channel, the share held between 0 and 1 so that values beyond the ends take the end colors
  const mix = (from, to, share) => from.map((value, index) => value + (to[index] - value) * Math.min(Math.max(share, 0), 1));
  return probability < AURORA_YELLOW ? mix(green, yellow, (probability - AURORA_LOWEST) / (AURORA_YELLOW - AURORA_LOWEST)) : mix(yellow, red, (probability - AURORA_YELLOW) / (AURORA_RED - AURORA_YELLOW));
}

/**
 * Creates the WebGL view of the Earth: day and night side from the subsolar point, coastlines and a grid of latitudes
 * and longitudes, the cells of an aurora map as glowing points and the rule-of-thumb edge of the oval as dashed circles.
 */
function createGlobeViewer(three, container, coast) {
  const renderer = new three.WebGLRenderer({ antialias: true, alpha: true });
  // Sharp on high-density screens, but at most twice the pixels per CSS pixel to keep the drawing fast
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  container.append(renderer.domElement);
  const scene = new three.Scene();
  // Opening angle of 30 degrees, aspect ratio set on resize, near and far clipping planes in Earth radii
  const camera = new three.PerspectiveCamera(30, 1, 0.1, 100);
  // The z axis points to geographic north, which stays up on screen while the viewer turns the globe
  camera.up.set(0, 0, 1);
  const controls = new three.OrbitControls(camera, renderer.domElement);
  controls.enablePan = false;
  // Zoom between 2 and 9 Earth radii from the centre
  [controls.minDistance, controls.maxDistance] = [2, 9];
  // The surface is lit where the Sun stands above the horizon, with a soft twilight band: smoothstep blends night into
  // day while the cosine of the angle to the subsolar point goes from -0.1 to 0.1
  const surface = new three.ShaderMaterial({
    uniforms: { sun: { value: new three.Vector3(1, 0, 0) }, day: { value: new three.Color(0x2b4c6f) }, night: { value: new three.Color(0x0b1220) } },
    vertexShader: SPHERE_VERTEX_SHADER,
    fragmentShader: "uniform vec3 sun; uniform vec3 day; uniform vec3 night; varying vec3 vNormal; void main() { float lit = smoothstep(-0.1, 0.1, dot(vNormal, sun)); gl_FragColor = vec4(mix(night, day, lit), 1.0); }",
  });
  const sphereGeometry = new three.SphereGeometry(1, 96, 64);
  // SphereGeometry has its poles on the y axis; the turn puts them on the z axis of spherePoint
  sphereGeometry.rotateX(Math.PI / 2);
  scene.add(new three.Mesh(sphereGeometry, surface));
  const lineSet = (positions, color, opacity) => {
    const geometry = new three.BufferGeometry();
    geometry.setAttribute("position", new three.Float32BufferAttribute(positions, 3));
    const segments = new three.LineSegments(geometry, new three.LineBasicMaterial({ color, transparent: true, opacity }));
    scene.add(segments);
    return segments;
  };
  // Coastlines and grid lie just above the surface, so that the sphere does not hide them; GeoJSON points are
  // [longitude, latitude]
  const coastPositions = [];
  for (const feature of coast.features) {
    const points = feature.geometry.coordinates;
    for (let index = 1; index < points.length; index++) coastPositions.push(...spherePoint(points[index - 1][1], points[index - 1][0], 1.002), ...spherePoint(points[index][1], points[index][0], 1.002));
  }
  const coastLines = lineSet(coastPositions, 0xc9d1d9, 0.75);
  // Latitude circles every 30 degrees from 60 south to 60 north and meridians every 30 degrees, in pieces of 5 degrees
  const gridPositions = [];
  for (let latitude = -60; latitude <= 60; latitude += 30) {
    for (let longitude = 0; longitude < 360; longitude += 5) gridPositions.push(...spherePoint(latitude, longitude, 1.001), ...spherePoint(latitude, longitude + 5, 1.001));
  }
  for (let longitude = 0; longitude < 360; longitude += 30) {
    for (let latitude = -90; latitude < 90; latitude += 5) gridPositions.push(...spherePoint(latitude, longitude, 1.001), ...spherePoint(latitude + 5, longitude, 1.001));
  }
  const gridLines = lineSet(gridPositions, 0x8b949e, 0.18);
  // The cells of a map are painted into a texture of one pixel per degree, 360 longitudes by 181 latitudes, which a
  // slightly larger sphere shows by the latitude and longitude of each of its points, so that the oval appears as a
  // smooth glow
  const canvas = document.createElement("canvas");
  canvas.width = 360;
  canvas.height = 181;
  const painter = canvas.getContext("2d");
  const cells = painter.createImageData(canvas.width, canvas.height);
  const auroraTexture = new three.CanvasTexture(canvas);
  // Canvas rows count from the south pole up, as the shader reads them, so the texture is not flipped
  auroraTexture.flipY = false;
  auroraTexture.magFilter = three.LinearFilter;
  auroraTexture.minFilter = three.LinearFilter;
  // The fragment shader turns the direction of a point into latitude and longitude and scales both to texture
  // coordinates from 0 to 1; additive blending adds the glow onto the surface below, black cells add nothing
  const auroraMaterial = new three.ShaderMaterial({
    uniforms: { cells: { value: auroraTexture } },
    transparent: true,
    depthWrite: false,
    blending: three.AdditiveBlending,
    vertexShader: SPHERE_VERTEX_SHADER,
    fragmentShader: `uniform sampler2D cells; varying vec3 vNormal; void main() { float latitude = asin(clamp(vNormal.z, -1.0, 1.0)); float longitude = atan(vNormal.y, vNormal.x); vec2 uv = vec2((longitude + ${Math.PI}) / ${2 * Math.PI}, (latitude + ${Math.PI / 2}) / ${Math.PI}); gl_FragColor = texture2D(cells, uv); }`,
  });
  const auroraGeometry = new three.SphereGeometry(1.008, 180, 90);
  const aurora = new three.Mesh(auroraGeometry, auroraMaterial);
  scene.add(aurora);
  // One dashed circle per hemisphere; dash and gap lengths in Earth radii
  const ruleMaterial = new three.LineDashedMaterial({ color: new three.Color(themeColor("--theme-storm")), dashSize: 0.03, gapSize: 0.02 });
  const rules = [new three.Line(new three.BufferGeometry(), ruleMaterial), new three.Line(new three.BufferGeometry(), ruleMaterial)];
  rules.forEach((rule) => scene.add(rule));

  // The view draws only on a change: a turn by the viewer, a new size or a new map
  const render = () => renderer.render(scene, camera);
  const resize = () => {
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (width === 0 || height === 0) return;
    renderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    render();
  };
  controls.addEventListener("change", render);
  const observer = new ResizeObserver(resize);
  observer.observe(container);

  return {
    /** Shows one aurora map with its subsolar point, and the rule-of-thumb edge at a geomagnetic latitude; null hides it. */
    draw(map, frame, ruleLatitude, pole) {
      surface.uniforms.sun.value.set(...spherePoint(frame.subsolar_latitude, frame.subsolar_longitude));
      cells.data.fill(0);
      map.probability.forEach((probability, index) => {
        // Rows run from -90 degrees latitude, columns from -180 degrees longitude, as the shader reads them; each pixel
        // takes four bytes, red, green, blue and alpha
        const offset = ((map.latitude[index] + 90) * canvas.width + (map.longitude[index] + 180)) * 4;
        const [red, green, blue] = auroraColor(probability);
        // Faint cells glow less, so that the oval shows its bright core
        const strength = Math.min(1, 0.35 + probability / AURORA_RED);
        cells.data.set([red * strength * 255, green * strength * 255, blue * strength * 255, 255], offset);
      });
      painter.putImageData(cells, 0, 0);
      auroraTexture.needsUpdate = true;
      rules.forEach((rule, index) => {
        rule.visible = ruleLatitude !== null;
        if (!rule.visible) return;
        // The first circle lies in the north, the second at the same geomagnetic latitude in the south
        rule.geometry.dispose();
        rule.geometry = new three.BufferGeometry();
        rule.geometry.setAttribute("position", new three.Float32BufferAttribute(geomagneticCircle(index === 0 ? ruleLatitude : -ruleLatitude, pole[0], pole[1], 1.012), 3));
        // Dashed lines need the length along the line at each point
        rule.computeLineDistances();
      });
      render();
    },
    /** Looks down on a hemisphere from above its night side at the time of a frame. */
    view(north, frame) {
      // The night side is centred on the longitude opposite the subsolar point
      const [x, y, z] = spherePoint(north ? GLOBE_VIEW_LATITUDE : -GLOBE_VIEW_LATITUDE, frame.subsolar_longitude + 180, GLOBE_CAMERA_DISTANCE);
      camera.position.set(x, y, z);
      controls.update();
      render();
    },
    /** Frees the WebGL context and the buffers of the view. */
    dispose() {
      observer.disconnect();
      controls.dispose();
      for (const object of [coastLines, gridLines, ...rules]) object.geometry.dispose();
      auroraGeometry.dispose();
      auroraMaterial.dispose();
      auroraTexture.dispose();
      sphereGeometry.dispose();
      surface.dispose();
      ruleMaterial.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
    },
  };
}

/** Returns the edge the rule of thumb gives at a time from the newest Kp value at or before it; null without Kp. */
function ruleEdgeAt(payload, kpTimes, time) {
  if (kpTimes.length === 0 || time < kpTimes[0]) return null;
  return payload.kp_edges[frameIndexAt(kpTimes, time)];
}

/**
 * Lists the times and values of a series over the aurora maps, with a missing value between two maps further apart
 * than the reach, so that its line breaks where no map was collected, as the globe shows no aurora there.
 */
function gappedSeries(frameTimes, values, reach) {
  const x = [];
  const y = [];
  frameTimes.forEach((time, index) => {
    if (index > 0 && time - frameTimes[index - 1] > reach) {
      // Plotly breaks a line at a null value, placed here midway in the gap
      x.push(utcText(new Date((time + frameTimes[index - 1]) / 2), true));
      y.push(null);
    }
    x.push(utcText(new Date(time), true));
    y.push(values[index]);
  });
  return { x, y };
}

/**
 * Draws the aurora of the last day on a turnable Earth from the stored OVATION maps, with a chart of how close to the
 * equator its night-side edge reaches in each hemisphere against the rule of thumb from Kp.
 */
export function renderAurora(panel, payload) {
  panel.append(panelTitle(payload.title));
  if (payload.frames.length === 0) {
    panel.append(element("p", "note", "No aurora map stored yet."));
    return;
  }
  const holder = threeViewer(state.drawingPanel.id, "globe-view", (three, container) => loadCoastlines().then((coast) => createGlobeViewer(three, container, coast)));
  const sideKey = `${state.drawingPanel.id}:side`;
  const north = () => state.panelChoices.get(sideKey) ?? true;
  const { tabs: sideTabs } = choiceTabs(sideKey, [["Northern hemisphere", true], ["Southern hemisphere", false]], true, (chosen) => {
    holder.ready.then((viewer) => viewer.view(chosen, payload.frames[shown]));
  });
  const legend = element("div", "field-legend");
  const scale = element("span", "field-legend-item");
  const bar = element("span", "swatch-scale");
  bar.style.background = "linear-gradient(to right, rgb(61,219,151), rgb(255,212,59), rgb(255,77,77))";
  scale.append(`${AURORA_LOWEST} %`, bar, `${AURORA_RED} % and more: probability of aurora`);
  legend.append(scale);
  const rule = element("span", "field-legend-item");
  const ruleSwatch = element("span", "swatch-line");
  ruleSwatch.style.setProperty("--swatch", themeColor("--theme-storm"));
  rule.append(ruleSwatch, "Edge of the oval after the rule of thumb from Kp");
  legend.append(rule);
  const views = element("div", "helio-views");
  const chart = chartDiv("chart aurora-edges");
  chart.style.height = "520px";
  views.append(holder.element, chart);
  const playBar = element("div", "video-bar");
  const play = element("button", "video-button", "Play the day");
  play.type = "button";
  const time = element("span", "caption");
  playBar.append(play, time, element("span", "caption", "Drag to turn the Earth, scroll to zoom."));
  const [nightStart, nightEnd] = payload.night_hours;
  const caption = linkedCaption(`OVATION Prime model of NOAA SWPC (Newell et al. 2009, doi:10.1029/2009JA014326), cells with at least ${AURORA_LOWEST} %. Dashed circles: ${payload.kp_rule}, drawn around the geomagnetic dipole pole (WMM2025, NOAA NCEI). Chart: median equatorward edge of the cells with at least 10 % on the night side (${nightStart}:00 to ${nightEnd}:00 local time).`);
  panel.append(sideTabs, legend, views, playBar, caption, freshnessCaption(payload.freshness));
  const frameTimes = payload.frames.map((frame) => Date.parse(frame.time));
  const kpTimes = payload.kp_times.map(Date.parse);
  const newest = frameIndexAt(frameTimes, Date.now());
  let shown = newest;
  const pole = [payload.pole_latitude, payload.pole_longitude];
  const show = (index) => {
    shown = index;
    // A slower load of an earlier map must not replace a later one
    const request = ++holder.request;
    const frame = payload.frames[index];
    Promise.all([holder.ready, auroraMap(frame.url)]).then(([viewer, map]) => {
      if (request !== holder.request) return;
      // The first map turns the camera onto the night side; later maps keep the camera of the viewer
      if (!holder.placed) {
        viewer.view(north(), frame);
        holder.placed = true;
      }
      viewer.draw(map, frame, ruleEdgeAt(payload, kpTimes, frameTimes[index]), pole);
      time.textContent = `Aurora at ${utcText(frame.time)} UTC`;
    });
    Plotly.relayout(chart, { shapes: cursorShapes(frameTimes[index]) });
  };
  const reach = frameReach(frameTimes);
  const northEdge = gappedSeries(frameTimes, payload.frames.map((frame) => frame.edge_north), reach);
  const southEdge = gappedSeries(frameTimes, payload.frames.map((frame) => frame.edge_south), reach);
  drawPlot(chart, [
    { type: "scatter", mode: "lines+markers", x: northEdge.x, y: northEdge.y, marker: { size: 4 }, line: { color: themeColor("--theme-aurora"), width: 2 }, name: "OVATION, north", hovertemplate: "%{y:.1f} deg<extra>OVATION, north</extra>" },
    { type: "scatter", mode: "lines+markers", x: southEdge.x, y: southEdge.y, marker: { size: 4, symbol: "circle-open" }, line: { color: themeColor("--theme-aurora"), width: 1.5, dash: "dot" }, name: "OVATION, south", hovertemplate: "%{y:.1f} deg<extra>OVATION, south</extra>" },
    // shape "hv" holds each Kp value until the next one, as Kp is given per three hours
    { type: "scatter", mode: "lines", x: payload.kp_times.map((value) => utcText(value, true)), y: payload.kp_edges, line: { color: themeColor("--theme-storm"), width: 2, shape: "hv", dash: "dash" }, name: "Rule of thumb from Kp", hovertemplate: "%{y:.1f} deg<extra>Rule of thumb from Kp</extra>" },
  ], {
    ...plainLayout(520),
    margin: { l: 60, r: 15, t: 30, b: 40 },
    xaxis: { type: "date", range: [payload.start, utcText(new Date(Math.max(Date.now(), frameTimes[frameTimes.length - 1])), true)], gridcolor: themeColor("--grid") },
    // Reversed, so that an edge reaching further towards the equator stands lower
    yaxis: { title: { text: "Edge of the aurora, geomagnetic latitude (deg)" }, gridcolor: themeColor("--grid"), autorange: "reversed" },
  });
  show(state.replayTime === null ? newest : frameIndexAt(frameTimes, state.replayTime));
  // The day plays one map every 250 ms, from the first map unless one other than the newest is shown
  const loop = frameLoop(play, "Play the day", payload.frames.length, () => Promise.all(payload.frames.map((frame) => auroraMap(frame.url))), show, () => (shown === newest ? 0 : shown), 250);
  onReplay((moment) => {
    loop.stop();
    if (moment === null) return show(newest);
    const index = frameNear(frameTimes, reach, moment);
    if (index >= 0) return show(index);
    // Between the collected maps there is no aurora to show, which the globe and its time say instead of an older map
    holder.request++;
    holder.ready.then((viewer) => viewer.draw({ latitude: [], longitude: [], probability: [] }, payload.frames[frameIndexAt(frameTimes, moment)], null, pole));
    time.textContent = `No aurora map at ${utcText(new Date(moment))} UTC`;
    Plotly.relayout(chart, { shapes: cursorShapes(moment) });
  });
  panel.preload = () => payload.frames.forEach((frame) => auroraMap(frame.url));
  panel.replaySpan = [frameTimes[0], frameTimes[frameTimes.length - 1]];
  panel.isPlaying = loop.playing;
}
