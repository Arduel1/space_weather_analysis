import { element } from "./core.js";

/**
 * Returns the point at a latitude and longitude in degrees on a sphere of the given radius, z towards north: Carrington
 * longitude and solar radii for the Sun, geographic longitude and Earth radii for the globe.
 */
export function spherePoint(latitude, longitude, radius = 1) {
  // Spherical to Cartesian coordinates: x towards longitude 0 on the equator, y towards longitude 90 degrees
  const phi = (latitude * Math.PI) / 180;
  const lambda = (longitude * Math.PI) / 180;
  return [radius * Math.cos(phi) * Math.cos(lambda), radius * Math.cos(phi) * Math.sin(lambda), radius * Math.sin(phi)];
}

/** Appends the two end points of every piece of a polyline to a flat list of positions, as line segments take them. */
export function appendPieces(positions, points) {
  for (let point = 1; point < points.length; point++) positions.push(...points[point - 1], ...points[point]);
}

// The three.js modules of the 3D view, loaded on first use from the copies in vendor/three
let threeModules = null;

/** Loads three.js and the add-ons of the 3D view once; pages without the view never load them. */
function loadThree() {
  threeModules ??= Promise.all([
    import("three"),
    import("three/addons/controls/OrbitControls.js"),
    import("three/addons/lines/LineSegments2.js"),
    import("three/addons/lines/LineSegmentsGeometry.js"),
    import("three/addons/lines/LineMaterial.js"),
    import("three/addons/renderers/CSS2DRenderer.js"),
  // The exports of all modules merged into one object, so that the views write three.Scene, three.OrbitControls, ...
  ]).then((modules) => Object.assign({}, ...modules));
  return threeModules;
}

// 3D views by panel ID, kept while the page stays selected, so that a redraw keeps the camera
const threeViewers = new Map();

/**
 * Returns the 3D view of a panel with its element, created on first use by `create`, which takes the three.js modules
 * and the element and returns the view or a promise of it; `ready` resolves to the view once it exists.
 */
export function threeViewer(id, className, create) {
  if (!threeViewers.has(id)) {
    const container = element("div", className);
    // request counts the drawings asked for, so that a slow load can tell that a newer drawing replaced it
    const holder = { element: container, request: 0, viewer: null };
    holder.ready = loadThree()
      .then((three) => create(three, container))
      .then((viewer) => (holder.viewer = viewer));
    threeViewers.set(id, holder);
  }
  return threeViewers.get(id);
}

/** Frees the WebGL contexts of all 3D views, as browsers keep only a few of them. */
export function releaseThreeViewers() {
  threeViewers.forEach((holder) => holder.ready.then((viewer) => viewer.dispose()));
  threeViewers.clear();
}
