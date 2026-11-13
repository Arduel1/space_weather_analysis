import { DATA_URL, element, linkedCaption, panelTitle } from "./core.js";
import { freshnessCaption } from "./freshness.js";
import { playTogether, videoControls } from "./video.js";

// Radius of the Sun in radii of Earth: the nominal solar radius of 695,700 km (IAU 2015 Resolution B3,
// https://www.iau.org/static/resolutions/IAU2015_English.pdf) over the mean Earth radius of 6,371 km (IUGG, Moritz 2000,
// doi:10.1007/s001900050278), rounded
const SUN_EARTH_RADIUS_RATIO = 109.2;

// XML namespace of SVG elements, which createElementNS needs for the markers drawn over the sun disk images
const SVG_NS = "http://www.w3.org/2000/svg";

// Colors of the magnetic classes from the simplest to the most complex field, the light steps of the sequential blue
// ramp; fixed in both themes, as they are drawn on the grey solar disk; complex regions produce the strongest flares
export const MAGNETIC_CLASS_COLORS = ["#cde2fb", "#86b6ef", "#3987e5", "#0d366b"];

/** Returns the color of a magnetic class by its complexity: alpha, beta, beta-gamma, then any class with delta. */
export function magneticClassColor(name) {
  if (name.includes("delta")) return MAGNETIC_CLASS_COLORS[3];
  // The class names join their parts with hyphens: alpha has one part, beta-gamma two, beta-gamma-... more
  return MAGNETIC_CLASS_COLORS[Math.min(name.split("-").length - 1, 2)];
}

/** Creates an SVG element with the given attributes. */
function svgElement(tag, attributes = {}) {
  const created = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) created.setAttribute(name, value);
  return created;
}

/** Draws the markers of one frame of a sun disk view: coronal holes, Earth to scale and the numbered sunspot regions. */
function drawDiskMarkers(overlay, frame, regions) {
  overlay.replaceChildren();
  // All positions are shares of the image width and height, the coordinates of the overlay of viewBox 0 0 1 1
  for (const outline of frame.coronal_holes ?? []) {
    overlay.append(svgElement("polygon", { points: outline.map(([x, y]) => `${x},${y}`).join(" "), class: "hole-outline" }));
  }
  // Earth to the same scale as the Sun in the lower left corner; frame.disk holds centre x, centre y and radius
  const earthRadius = frame.disk[2] / SUN_EARTH_RADIUS_RATIO;
  const earthLabel = svgElement("text", { x: 0.06 + earthRadius + 0.01, y: 0.91, class: "region-label" });
  earthLabel.textContent = "Earth to scale";
  overlay.append(svgElement("circle", { cx: 0.06, cy: 0.9, r: earthRadius, class: "earth-scale" }), earthLabel);
  for (const [regionIndex, x, y] of frame.markers) {
    const region = regions[regionIndex];
    // The radius grows with the square root of the area, so that the circle area follows the region area
    const radius = 0.012 + Math.sqrt(region.area ?? 0) * 0.0012;
    const circle = svgElement("circle", { cx: x, cy: y, r: radius, class: "region-marker" });
    circle.style.stroke = magneticClassColor(region.magnetic_class);
    // An SVG title child is the tooltip of its parent
    const tip = svgElement("title");
    tip.textContent = `Region ${region.region}: ${region.magnetic_class}, area ${region.area ?? "?"} millionths of the hemisphere, flares C ${region.flares[0]}, M ${region.flares[1]}, X ${region.flares[2]}`;
    circle.append(tip);
    const label = svgElement("text", { x: x + radius + 0.006, y: y + 0.008, class: "region-label" });
    // NOAA numbers regions with five digits; the last four are the usual short form
    label.textContent = String(region.region).slice(-4);
    overlay.append(circle, label);
  }
}

/**
 * Draws image sequences of the whole Sun side by side as one video with the numbered sunspot regions and coronal holes
 * marked; one bar plays them together, each showing its frame at the common time.
 */
export function renderSunDisk(panel, payload) {
  panel.append(panelTitle(payload.title));
  const views = payload.views.filter((view) => view.frames.length > 0);
  if (views.length === 0) {
    panel.append(element("p", "note", "No images yet."));
    return;
  }
  const grid = element("div", "disk-grid");
  grid.style.gridTemplateColumns = `repeat(${views.length}, minmax(0, 1fr))`;
  const stages = views.map((view) => {
    const stage = element("figure", "disk-stage");
    const image = element("img");
    image.alt = view.label;
    // The overlay stretches its unit square over the image, so that markers are placed in shares of the image size
    const overlay = svgElement("svg", { viewBox: "0 0 1 1", preserveAspectRatio: "none" });
    stage.append(image, overlay, element("figcaption", "", view.label));
    grid.append(stage);
    return { stage, image, overlay };
  });
  const controls = videoControls("Time", false);
  const holes = views.some((view) => view.frames.some((frame) => (frame.coronal_holes ?? []).length > 0));
  const legend = linkedCaption(`Circles: NOAA sunspot regions (size: area; darker: more complex magnetic class).${holes ? " Green: coronal holes found in each AIA image (threshold after Rotter et al. 2012, doi:10.1007/s11207-012-0101-y)." : ""} Corner dot: Earth to scale.`);
  panel.append(grid, controls.bar, legend, freshnessCaption(payload.freshness));
  const drawFrame = (position, frame) => {
    const { image, overlay } = stages[position];
    const url = DATA_URL + frame.url;
    if (image.getAttribute("src") !== url) image.src = url;
    drawDiskMarkers(overlay, frame, payload.regions);
  };
  playTogether(panel, views.map((view) => view.frames), stages.map(({ stage }) => stage), controls, drawFrame);
}
