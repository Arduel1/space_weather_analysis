import { state } from "./state.js";
import { CORONAL_HOLE_COLOR, DATA_URL, choiceTabs, element, linkedCaption, loadJson, panelTitle, themeColor, utcText, withAlpha } from "./core.js";
import { freshnessCaption } from "./freshness.js";
import { chartDiv, drawPlot, swatchLegend, verticalLine } from "./charts.js";
import { frameIndexAt, onReplay } from "./replay.js";
import { frameLoop, stopVideoTimer } from "./video.js";
import { MAGNETIC_CLASS_COLORS, magneticClassColor } from "./sundisk.js";
import { appendPieces, spherePoint, threeViewer } from "./viewer3d.js";

// Largest radial field in gauss the color scales of the field views show; stronger fields take the end colors
const FIELD_COLOR_LIMIT = 20;

// Start distance of the camera of the 3D view from the centre of the Sun in solar radii
const FIELD_CAMERA_DISTANCE = 7.5;

// Time between two steps of the turning 3D view
const TURN_STEP_MS = 50;

// Models of the magnetic field by file, loaded once for the 3D view and the map of the same model
const fieldModels = new Map();

/** Loads a model of the magnetic field once and keeps it for every view of it. */
function fieldModel(url) {
  if (!fieldModels.has(url)) fieldModels.set(url, loadJson(url));
  return fieldModels.get(url);
}

/** Returns the diverging color scale of the radial field: inward blue, outward red, a neutral grey around zero. */
function fieldColorScale() {
  return [
    [0, themeColor("--series-1")],
    [0.5, themeColor("--grid")],
    [1, themeColor("--series-2")],
  ];
}

/**
 * Closes a surface map for drawing on a sphere: the first longitude repeated after the last, so that no seam stays open,
 * and a row at each pole holding the mean of the row next to it, so that the poles are covered.
 */
function closedSphere(map) {
  const mean = (row) => row.reduce((sum, value) => sum + value, 0) / row.length;
  const wrapped = map.values.map((row) => [...row, row[0]]);
  const south = wrapped[0].map(() => mean(map.values[0]));
  const north = wrapped[0].map(() => mean(map.values[map.values.length - 1]));
  return {
    longitudes: [...map.longitudes, map.longitudes[0] + 360],
    latitudes: [-90, ...map.latitudes, 90],
    values: [south, ...wrapped, north],
  };
}

/** Lists the end points of every piece of a set of field lines, as the line segments of the 3D view take them. */
function segmentPositions(lines) {
  const positions = [];
  for (const line of lines) appendPieces(positions, line.points);
  return positions;
}

/** Lists the end points of every piece of the closed outlines of the coronal holes, just above the surface. */
function outlinePositions(holes) {
  const positions = [];
  for (const hole of holes) {
    // The first point repeated at the end closes the outline; outline points are [latitude, longitude]
    appendPieces(positions, [...hole.outline, hole.outline[0]].map(([latitude, longitude]) => spherePoint(latitude, longitude, 1.01)));
  }
  return positions;
}

/** Returns the flares and CMEs that happened within three hours of a time. */
function eventsNear(events, moment) {
  return events.filter((event) => Math.abs(Date.parse(event.time) - moment) <= 3 * 3600 * 1000);
}

/** Returns the 3D view of the field of a panel, created on first use. */
function fieldViewer(id) {
  return threeViewer(id, "field-view", createFieldViewer);
}

/**
 * Creates the WebGL view of the field: the Sun colored by its surface field, the field lines and the coronal holes as
 * line segments, and the sunspot regions and events as labels. Showing another model only replaces colors and buffers,
 * and the view draws only when something changed.
 */
function createFieldViewer(three, container) {
  const renderer = new three.WebGLRenderer({ antialias: true, alpha: true });
  // Sharp on high-density screens, but at most twice the pixels per CSS pixel to keep the drawing fast
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  // The label renderer places HTML elements over the canvas at the screen position of their 3D point
  const labelRenderer = new three.CSS2DRenderer();
  labelRenderer.domElement.className = "field-labels";
  container.append(renderer.domElement, labelRenderer.domElement);
  const scene = new three.Scene();
  // Opening angle of 35 degrees, aspect ratio set on resize, near and far clipping planes in solar radii
  const camera = new three.PerspectiveCamera(35, 1, 0.1, 100);
  // The z axis points to solar north, which stays up on screen while the viewer turns the Sun
  camera.up.set(0, 0, 1);
  const controls = new three.OrbitControls(camera, renderer.domElement);
  controls.enablePan = false;
  // Zoom between 3 and 16 solar radii from the centre
  [controls.minDistance, controls.maxDistance] = [3, 16];
  // Turn speed of the automatic turn, where 2 turns once in 30 seconds
  controls.autoRotateSpeed = 2;
  // The surface shows the field colors and, where an image is laid on, the image on the side it saw: a weight per
  // point from 0 to 1 mixes the two, so that the far side keeps the field of the model. The shader chunks of the
  // material are patched: the vertex shader hands the weight "seen" on, the fragment shader skips the usual texture
  // step and mixes the texture into the vertex color by that weight instead
  const surfaceMaterial = new three.MeshBasicMaterial({ vertexColors: true, side: three.DoubleSide });
  surfaceMaterial.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute float seen;\nvarying float vSeen;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvSeen = seen;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying float vSeen;")
      .replace("#include <map_fragment>", "")
      .replace("#include <color_fragment>", "#include <color_fragment>\n#ifdef USE_MAP\ndiffuseColor.rgb = mix(diffuseColor.rgb, texture2D(map, vMapUv).rgb, vSeen);\n#endif");
  };
  const sphere = new three.Mesh(new three.BufferGeometry(), surfaceMaterial);
  scene.add(sphere);
  // Images of the corona by URL, each loaded once
  const loader = new three.TextureLoader();
  const textures = new Map();
  const texture = (url) => {
    if (!textures.has(url)) {
      textures.set(url, loader.loadAsync(url).then((loaded) => {
        // The image files hold sRGB colors, which three.js converts to linear light for drawing
        loaded.colorSpace = three.SRGBColorSpace;
        return loaded;
      }));
    }
    return textures.get(url);
  };
  let shownImage = null;
  // Wide lines need LineSegments2 with a LineMaterial, as WebGL draws plain lines one pixel wide; width in pixels
  const segments = (width) => {
    const created = new three.LineSegments2(new three.LineSegmentsGeometry(), new three.LineMaterial({ linewidth: width }));
    scene.add(created);
    return created;
  };
  const lines = { closed: segments(1.5), outward: segments(2.5), inward: segments(2.5), holes: segments(3) };
  let labels = [];
  let placed = false;
  let turnTimer = null;

  const render = () => {
    // A label is hidden while the Sun stands between it and the camera: its point faces away from the camera when the
    // dot product of point and camera position falls below the square of the solar radius, 1
    for (const label of labels) label.visible = label.position.dot(camera.position) > 1;
    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
  };
  const resize = () => {
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (width === 0 || height === 0) return;
    renderer.setSize(width, height);
    labelRenderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    // LineMaterial sets its widths in pixels and needs the size of the canvas for that
    Object.values(lines).forEach((line) => line.material.resolution.set(width, height));
    render();
  };
  controls.addEventListener("change", render);
  const observer = new ResizeObserver(resize);
  observer.observe(container);

  const setSegments = (line, positions, color) => {
    // A new geometry per model, as LineSegmentsGeometry does not shrink its buffers
    line.geometry.dispose();
    line.geometry = new three.LineSegmentsGeometry();
    line.visible = positions.length > 0;
    if (line.visible) line.geometry.setPositions(positions);
    line.material.color.set(color);
  };
  const label = (className, text, position, size = null) => {
    const marker = element("div", className);
    if (size !== null) marker.style.width = marker.style.height = `${size}px`;
    marker.append(element("span", "", text));
    const object = new three.CSS2DObject(marker);
    object.position.set(...position);
    return object;
  };
  // A grid of points over latitude and longitude, each cell split into two triangles of point indices
  const surfaceGeometry = (latitudes, longitudes) => {
    const positions = [];
    for (const latitude of latitudes) for (const longitude of longitudes) positions.push(...spherePoint(latitude, longitude, 1));
    const indices = [];
    const columns = longitudes.length;
    for (let row = 0; row < latitudes.length - 1; row++) {
      for (let column = 0; column < columns - 1; column++) {
        const corner = row * columns + column;
        indices.push(corner, corner + 1, corner + columns, corner + 1, corner + columns + 1, corner + columns);
      }
    }
    // Colors, texture coordinates and the image weight per point start empty and are filled per model
    const geometry = new three.BufferGeometry();
    geometry.setAttribute("position", new three.Float32BufferAttribute(positions, 3));
    geometry.setAttribute("color", new three.Float32BufferAttribute(new Float32Array(positions.length), 3));
    geometry.setAttribute("uv", new three.Float32BufferAttribute(new Float32Array((positions.length / 3) * 2), 2));
    geometry.setAttribute("seen", new three.Float32BufferAttribute(new Float32Array(positions.length / 3), 1));
    geometry.setIndex(indices);
    return geometry;
  };

  return {
    /**
     * Shows a model with the regions, coronal holes and events drawn on it; the first model sets the camera facing
     * Earth. With an image, the image covers the side of the Sun seen at its time instead of the field colors.
     */
    draw(model, regions, holes, events, image = null) {
      const surface = closedSphere(model.surface_field);
      // The grid is built again only when the model has another resolution
      if (sphere.geometry.getAttribute("position")?.count !== surface.latitudes.length * surface.longitudes.length) {
        sphere.geometry.dispose();
        sphere.geometry = surfaceGeometry(surface.latitudes, surface.longitudes);
      }
      // Colors as in the map: inward blue, outward red, both through the grey of zero
      const [inward, zero, outward] = ["--series-1", "--grid", "--series-2"].map((name) => new three.Color(themeColor(name)));
      const mixed = new three.Color();
      const colors = sphere.geometry.getAttribute("color");
      surface.values.flat().forEach((value, index) => {
        const share = Math.max(-1, Math.min(1, value / FIELD_COLOR_LIMIT));
        mixed.lerpColors(zero, share < 0 ? inward : outward, Math.abs(share));
        colors.setXYZ(index, mixed.r, mixed.g, mixed.b);
      });
      colors.needsUpdate = true;
      shownImage = image;
      const seen = sphere.geometry.getAttribute("seen");
      if (image === null) {
        seen.array.fill(0);
        seen.needsUpdate = true;
        // A material losing its texture needs a new shader program
        if (surfaceMaterial.map !== null) {
          surfaceMaterial.map = null;
          surfaceMaterial.needsUpdate = true;
        }
      } else {
        // Each point seen from Earth at the image time takes the image pixel it appears at; a point of the far side
        // takes the pixel on the limb in its direction, so that the image does not smear across the limb.
        // image.disk holds centre x, centre y and radius of the disk in shares of the image size; b0 is the tilt of
        // the solar north pole towards Earth in degrees
        const uvs = sphere.geometry.getAttribute("uv");
        const [diskX, diskY, diskRadius] = image.disk;
        const tilt = (image.b0 * Math.PI) / 180;
        let index = 0;
        for (const latitude of surface.latitudes) {
          const phi = (latitude * Math.PI) / 180;
          for (const longitude of surface.longitudes) {
            const lambda = ((longitude - image.centre_longitude) * Math.PI) / 180;
            // Orthographic projection onto the image plane: west and north on the disk in solar radii, toward the
            // component pointing to Earth, positive on the visible side
            const west = Math.cos(phi) * Math.sin(lambda);
            const north = Math.sin(phi) * Math.cos(tilt) - Math.cos(phi) * Math.cos(lambda) * Math.sin(tilt);
            const toward = Math.sin(phi) * Math.sin(tilt) + Math.cos(phi) * Math.cos(lambda) * Math.cos(tilt);
            // A far side point is pushed out onto the limb, the circle of radius 1
            const reach = toward > 0 ? 1 : Math.hypot(west, north) || 1;
            // Texture rows count from the bottom, image rows from the top
            uvs.setXY(index, diskX + (west / reach) * diskRadius, 1 - (diskY - (north / reach) * diskRadius));
            seen.setX(index, toward > 0 ? 1 : 0);
            index++;
          }
        }
        uvs.needsUpdate = true;
        seen.needsUpdate = true;
        // Until its image is loaded, the surface shows the field
        texture(DATA_URL + image.url).then((loaded) => {
          if (shownImage !== image) return;
          if (surfaceMaterial.map !== loaded) {
            // Only the first texture needs a new shader program; swapping one texture for another does not
            surfaceMaterial.needsUpdate = surfaceMaterial.map === null;
            surfaceMaterial.map = loaded;
          }
          render();
        });
      }
      setSegments(lines.closed, segmentPositions(model.lines.filter((line) => !line.open)), themeColor("--muted"));
      setSegments(lines.outward, segmentPositions(model.lines.filter((line) => line.open && line.polarity > 0)), themeColor("--series-2"));
      setSegments(lines.inward, segmentPositions(model.lines.filter((line) => line.open && line.polarity < 0)), themeColor("--series-1"));
      setSegments(lines.holes, outlinePositions(holes), CORONAL_HOLE_COLOR);
      labels.forEach((object) => scene.remove(object));
      labels = [
        // A region label grows with the square root of the region area and is framed in the color of its magnetic
        // class; NOAA numbers regions with five digits, the last four are the usual short form
        ...regions.map((region) => {
          const marker = label("field-region", String(region.region).slice(-4), spherePoint(region.latitude, region.carrington_longitude, 1.02), 6 + Math.sqrt(region.area ?? 0) * 0.5);
          marker.element.style.borderColor = magneticClassColor(region.magnetic_class ?? "");
          return marker;
        }),
        // FLR is the DONKI kind of a flare; every other event of the panel is a CME
        ...events.map((event) =>
          label(event.kind === "FLR" ? "field-event field-flare" : "field-event field-cme", `${event.kind === "FLR" ? "Flare" : "CME"} ${event.label}`.trim(), spherePoint(event.latitude, event.carrington_longitude, 1.04)),
        ),
      ];
      labels.forEach((object) => scene.add(object));
      if (!placed) {
        // The camera starts above the Carrington longitude facing Earth, raised by 18 % of its distance over the
        // solar equator
        const angle = (model.earth_longitude * Math.PI) / 180;
        camera.position.set(FIELD_CAMERA_DISTANCE * Math.cos(angle), FIELD_CAMERA_DISTANCE * Math.sin(angle), FIELD_CAMERA_DISTANCE * 0.18);
        controls.update();
        placed = true;
      }
      resize();
    },
    /** Starts or stops turning the Sun; the timer is kept among the video timers, which every page drawing stops. */
    turn(on) {
      turnTimer = stopVideoTimer(turnTimer);
      controls.autoRotate = on;
      if (!on) return;
      // OrbitControls turns by the time passed since the last update, given in seconds
      turnTimer = setInterval(() => controls.update(TURN_STEP_MS / 1000), TURN_STEP_MS);
      state.videoTimers.push(turnTimer);
    },
    /** Whether the Sun is turning. */
    turning: () => turnTimer !== null && state.videoTimers.includes(turnTimer),
    /** Frees the WebGL context, the buffers and the listeners. */
    dispose() {
      this.turn(false);
      observer.disconnect();
      controls.dispose();
      scene.traverse((object) => {
        object.geometry?.dispose();
        object.material?.dispose();
      });
      surfaceMaterial.dispose();
      textures.forEach((loading) => loading.then((loaded) => loaded.dispose()));
      renderer.dispose();
      renderer.forceContextLoss();
    },
  };
}

/** Builds the legend of the 3D view and the scale of its surface colors. */
function fieldLegend() {
  const legend = swatchLegend([
    ["swatch-line", themeColor("--muted"), "Closed loops"],
    ["swatch-line", themeColor("--series-2"), "Open field, pointing out"],
    ["swatch-line", themeColor("--series-1"), "Open field, pointing in"],
    ["swatch-line", CORONAL_HOLE_COLOR, "Coronal holes (AIA)"],
    ["swatch-ring", MAGNETIC_CLASS_COLORS[2], "Sunspot regions"],
  ]);
  const scale = element("span", "field-legend-item");
  const bar = element("span", "swatch-scale");
  bar.style.background = `linear-gradient(to right, ${themeColor("--series-1")}, ${themeColor("--grid")}, ${themeColor("--series-2")})`;
  scale.append(`-${FIELD_COLOR_LIMIT} G`, bar, `+${FIELD_COLOR_LIMIT} G surface field`);
  legend.append(scale);
  return legend;
}

/** Draws the models of the coronal magnetic field of the last day as a turnable Sun with closed loops and open field lines. */
export function renderFieldLines(panel, payload) {
  panel.append(panelTitle(payload.title));
  if (payload.url === null || payload.models.length === 0) {
    panel.append(element("p", "note", "No model computed yet."));
    return;
  }
  const holder = fieldViewer(state.drawingPanel.id);
  const bar = element("div", "video-bar");
  const play = element("button", "video-button", "Play the day");
  play.type = "button";
  const turn = element("button", "video-button", "Turn");
  turn.type = "button";
  const time = element("span", "caption");
  bar.append(play, turn, time, element("span", "caption", "Drag to turn it by hand, scroll to zoom."));
  const caption = linkedCaption("PFSS model (Schatten et al. 1969, doi:10.1007/BF00146478; Altschuler and Newkirk 1969, doi:10.1007/BF00145734) of the hourly GONG magnetograms, or of the daily SDO/HMI synoptic map while GONG is not available, open above 2.5 solar radii. Surface: radial field, or the AIA 193 A image on the side SDO saw. Diamonds: flares, crosses: CMEs within 3 hours of the model.");
  // The surface shows the field or the image of the corona nearest the model, kept as choice of the viewer
  const surfaceKey = `${state.drawingPanel.id}:surface`;
  const imageTimes = payload.surface_images.map((image) => Date.parse(image.time));
  const { tabs: surfaceTabs, buttons: surfaceButtons } = choiceTabs(surfaceKey, [["Magnetic field", false], ["Corona (AIA 193 A)", true]], false, () => show(shown));
  surfaceButtons[1].disabled = imageTimes.length === 0;
  panel.append(fieldLegend(), surfaceTabs, holder.element, bar, caption, freshnessCaption(payload.freshness));
  const modelTimes = payload.models.map((model) => Date.parse(model.time));
  const newest = payload.models.length - 1;
  let shown = newest;
  const imageAt = (moment) => (state.panelChoices.get(surfaceKey) === true && imageTimes.length > 0 ? payload.surface_images[frameIndexAt(imageTimes, moment)] : null);
  const show = (index) => {
    shown = index;
    // A slower load of an earlier model must not replace a later one
    const request = ++holder.request;
    Promise.all([holder.ready, fieldModel(payload.models[index].url)]).then(([viewer, model]) => {
      if (request !== holder.request) return;
      const image = imageAt(modelTimes[index]);
      viewer.draw(model, payload.regions, payload.coronal_holes, eventsNear(payload.events, modelTimes[index]), image);
      // The image time without its date: utcText gives `{date} {hours}:{minutes}`, the date 10 characters and a space
      // The magnetogram is named from the model file, so a model of the fallback map is told apart from the GONG ones
      time.textContent = `Model of ${utcText(model.map_time)} UTC${model.magnetogram ? ` from ${model.magnetogram}` : ""}${image === null ? "" : `, AIA ${utcText(image.time).slice(11)}`}`;
    });
  };
  // A redraw of the page stops the turn, so that the button starts as Turn
  holder.ready.then((viewer) => viewer.turn(false));
  show(state.replayTime === null ? newest : frameIndexAt(modelTimes, state.replayTime));
  let turning = false;
  turn.addEventListener("click", async () => {
    turning = !turning;
    turn.textContent = turning ? "Stop" : "Turn";
    (await holder.ready).turn(turning);
  });
  // The day plays one model every 700 ms, from the first model unless one other than the newest is shown
  const loop = frameLoop(play, "Play the day", payload.models.length, () => Promise.all(payload.models.map((model) => fieldModel(model.url))), show, () => (shown === newest ? 0 : shown), 700);
  onReplay((moment) => {
    loop.stop();
    show(moment === null ? newest : frameIndexAt(modelTimes, moment));
  });
  panel.isPlaying = () => loop.playing() || holder.viewer?.turning() === true;
}

/** Draws the newest model of the magnetic field as a map: the surface field, where the field is open, and the current sheet. */
export function renderFieldMap(panel, payload) {
  panel.append(panelTitle(payload.title));
  if (payload.url === null) {
    panel.append(element("p", "note", "No model computed yet."));
    return;
  }
  const chart = chartDiv("chart chart-tall");
  chart.style.height = "380px";
  const caption = element("p", "caption", "The same model as a map in Carrington longitude. Shaded: open field. Thick line: polarity change at 2.5 solar radii (heliospheric current sheet). Dashed: longitude facing Earth. Circles: NOAA regions; green: coronal holes in AIA; diamonds and crosses: flares and CMEs of the last day.");
  panel.append(chart, caption, freshnessCaption(payload.freshness));
  fieldModel(payload.url).then((model) => {
    const surface = model.surface_field;
    const open = model.open_field;
    // Open cells are shaded in the text color, closed cells stay clear, so that the field colors below remain readable
    const openMask = open.values.map((row) => row.map((value) => (value === 0 ? null : 1)));
    const shade = withAlpha(themeColor("--text"), 0.35);
    const sheet = model.source_surface_field;
    drawPlot(chart, [
      { type: "heatmap", x: surface.longitudes, y: surface.latitudes, z: surface.values, zmin: -FIELD_COLOR_LIMIT, zmax: FIELD_COLOR_LIMIT, colorscale: fieldColorScale(), colorbar: { title: { text: "Field (G)", side: "right" }, thickness: 10 }, hovertemplate: "longitude %{x}, latitude %{y}: %{z:.1f} G<extra></extra>", name: "Surface field" },
      // A color scale of one color paints every open cell alike; null cells stay transparent
      { type: "heatmap", x: open.longitudes, y: open.latitudes, z: openMask, colorscale: [[0, shade], [1, shade]], showscale: false, hoverinfo: "skip", name: "Open field" },
      // The single contour at zero of the field at the source surface is the current sheet
      { type: "contour", x: sheet.longitudes, y: sheet.latitudes, z: sheet.values, contours: { start: 0, end: 0, size: 1, coloring: "none" }, line: { color: themeColor("--text"), width: 2.5 }, showscale: false, hoverinfo: "skip", name: "Current sheet" },
      // Outline points are [latitude, longitude]; the first point repeated closes each outline
      ...payload.coronal_holes.map((hole, index) => ({ type: "scatter", mode: "lines", x: [...hole.outline.map((point) => point[1]), hole.outline[0][1]], y: [...hole.outline.map((point) => point[0]), hole.outline[0][0]], line: { color: CORONAL_HOLE_COLOR, width: 2.5 }, name: "Coronal hole", showlegend: index === 0, hoverinfo: "skip" })),
      // Region markers grow with the square root of the area; the label is the short four-digit NOAA number
      { type: "scatter", mode: "markers+text", x: payload.regions.map((region) => region.carrington_longitude), y: payload.regions.map((region) => region.latitude), text: payload.regions.map((region) => String(region.region).slice(-4)), textposition: "top right", textfont: { size: 10, color: themeColor("--text") }, marker: { symbol: "circle-open", size: payload.regions.map((region) => 8 + Math.sqrt(region.area ?? 0) * 0.6), color: payload.regions.map((region) => magneticClassColor(region.magnetic_class ?? "")), line: { width: 2.5 } }, hovertemplate: "Sunspot region %{text}<extra></extra>", name: "Sunspot regions", cliponaxis: false },
      { type: "scatter", mode: "markers", x: payload.events.map((event) => event.carrington_longitude), y: payload.events.map((event) => event.latitude), text: payload.events.map((event) => `${event.kind === "FLR" ? "Flare" : "CME"} ${event.label} ${utcText(event.time)} UTC`), marker: { symbol: payload.events.map((event) => (event.kind === "FLR" ? "diamond" : "x")), size: 10, color: payload.events.map((event) => (event.kind === "FLR" ? "#ffd43b" : "#ff6b6b")), line: { color: "#000000", width: 1 } }, hovertemplate: "%{text}<extra></extra>", name: "Flares and CMEs of the day" },
    ], {
      height: 380,
      margin: { l: 55, r: 10, t: 10, b: 45 },
      showlegend: false,
      shapes: [verticalLine(model.earth_longitude, { dash: "dash", width: 1.5, color: themeColor("--text") })],
      // minallowed and maxallowed keep zoom and pan inside the map
      xaxis: { title: { text: "Carrington longitude (deg)" }, range: [0, 360], minallowed: 0, maxallowed: 360, gridcolor: themeColor("--grid") },
      yaxis: { title: { text: "Latitude (deg)" }, range: [-90, 90], minallowed: -90, maxallowed: 90, gridcolor: themeColor("--grid") },
    });
  });
}
