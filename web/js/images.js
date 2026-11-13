import { state } from "./state.js";
import { DATA_URL, element, link, panelTitle, utcText } from "./core.js";
import { FRAME_REACH_MIN_MS, frameIndexAt, frameNear, frameReach, onReplay } from "./replay.js";
import { VIDEO_FRAME_MS, VIDEO_STEPS, markMissing, playTogether, preloadFrames, stopVideoTimer, toggleFullScreen, videoControls } from "./video.js";

/**
 * Draws an image panel like a video player: tabs for its variants, the frame, and a bar with play, a frame slider, the
 * time shown and full screen. Play and the page replay step through time, so that a gap in the sequence shows as such
 * instead of a jump to the next frame.
 */
export function renderImages(panel, payload) {
  panel.append(panelTitle(payload.title));
  if (!payload.variants.some((variant) => variant.frames.length > 0)) {
    panel.append(element("p", "note", "No images yet."));
    return;
  }
  if (payload.columns > 0) {
    renderImageGrid(panel, payload);
    return;
  }
  // The variant chosen before, else the configured one; an empty variant gives way to the first one with frames
  const choiceKey = `${state.drawingPanel.id}:variant`;
  let variant = payload.variants[state.panelChoices.get(choiceKey) ?? payload.selected];
  if (variant.frames.length === 0) variant = payload.variants.find((candidate) => candidate.frames.length > 0);
  let frameTimes = [];
  let reach = FRAME_REACH_MIN_MS;
  let current = 0;
  let timer = null;

  const stage = element("div", "video-stage");
  const image = element("img", "frame");
  image.alt = payload.title;
  stage.append(image);
  // The slider runs over the frame indices; play and replay move through time
  const { bar, play, slider, time, full } = videoControls("Image");
  slider.min = 0;

  const showAt = (moment) => {
    current = moment;
    const index = frameNear(frameTimes, reach, moment);
    markMissing(stage, index < 0);
    // The time without the year: utcText starts with `{yyyy}-`
    time.textContent = `${utcText(new Date(moment)).slice(5)} UTC`;
    if (index < 0) return;
    slider.value = index;
    const url = DATA_URL + variant.frames[index].url;
    // Setting the same source again would reload the image
    if (image.getAttribute("src") !== url) image.src = url;
  };
  const stop = () => {
    timer = stopVideoTimer(timer);
    play.textContent = "Play";
  };
  // Without replay the frame of now is shown, the newest for measured images and the present one of a model run
  const startMoment = () => frameTimes[frameIndexAt(frameTimes, Date.now())];
  const select = (chosen) => {
    variant = chosen;
    frameTimes = variant.frames.map((frame) => Date.parse(frame.time));
    reach = frameReach(frameTimes);
    slider.max = frameTimes.length - 1;
    showAt(state.replayTime === null || !payload.follows_replay ? startMoment() : state.replayTime);
    tabs.querySelectorAll("button").forEach((tab, position) => tab.setAttribute("aria-pressed", String(payload.variants[position] === variant)));
  };

  const tabs = element("div", "video-tabs");
  if (payload.variants.length > 1) {
    payload.variants.forEach((candidate, position) => {
      const tab = element("button", "", candidate.label);
      tab.type = "button";
      tab.disabled = candidate.frames.length === 0;
      tab.addEventListener("click", () => {
        state.panelChoices.set(choiceKey, position);
        stop();
        select(candidate);
      });
      tabs.append(tab);
    });
    panel.append(tabs);
  }
  panel.append(stage, bar);
  if (variant.event) panel.append(eventCaption(variant.event));
  select(variant);

  slider.addEventListener("input", () => {
    stop();
    showAt(frameTimes[Number(slider.value)]);
  });
  play.addEventListener("click", () => {
    if (timer !== null) return stop();
    preloadFrames(variant.frames);
    play.textContent = "Pause";
    const first = frameTimes[0];
    const last = frameTimes[frameTimes.length - 1];
    // Play steps evenly through time, at least one minute per frame
    const step = Math.max((last - first) / VIDEO_STEPS, 60 * 1000);
    // The video loops from its first frame after the last one
    timer = setInterval(() => showAt(current + step > last ? first : current + step), VIDEO_FRAME_MS);
    state.videoTimers.push(timer);
  });
  full.addEventListener("click", () => toggleFullScreen(stage));
  panel.preload = () => preloadFrames(variant.frames);
  panel.isPlaying = () => timer !== null;
  // A zoom onto one event plays on its own time, apart from the replay of the page
  if (!payload.follows_replay) return;
  onReplay((moment) => {
    stop();
    showAt(moment === null ? startMoment() : moment);
  });
  // The replay runs over the frames of every variant, so that switching the variant keeps the span
  const allTimes = payload.variants.flatMap((candidate) => candidate.frames.map((frame) => Date.parse(frame.time)));
  panel.replaySpan = [Math.min(...allTimes), Math.max(...allTimes)];
}

/** Describes the event a zoom shows: headline, place, peak time, the span shown and its page at DONKI. */
function eventCaption(event) {
  const line = element("p", "caption", `${event.label ? `Flare ${event.label}` : "Event"}${event.location ? ` at ${event.location}` : ""}, peak ${utcText(event.peak)} UTC, shown from 10 minutes before its begin to 10 minutes after its end. `);
  if (event.link) line.append(link(event.link, "Event at DONKI"));
  return line;
}

/**
 * Draws every variant of an image panel at once in a grid, each labelled, with one bar playing them together: each
 * shows its frame at the common time, so that the views of one moment stand side by side.
 */
function renderImageGrid(panel, payload) {
  const variants = payload.variants.filter((variant) => variant.frames.length > 0);
  const grid = element("div", "video-grid");
  grid.style.gridTemplateColumns = `repeat(${payload.columns}, minmax(0, 1fr))`;
  const cells = [];
  const images = variants.map((variant) => {
    const cell = element("figure", "video-cell");
    const image = element("img", "frame");
    image.alt = variant.label;
    cell.append(image, element("figcaption", "", variant.label));
    grid.append(cell);
    cells.push(cell);
    return image;
  });
  const controls = videoControls("Time");
  panel.append(grid, controls.bar);
  const event = variants.find((variant) => variant.event)?.event;
  if (event) panel.append(eventCaption(event));
  controls.full.addEventListener("click", () => toggleFullScreen(grid));
  const drawFrame = (position, frame) => {
    const url = DATA_URL + frame.url;
    if (images[position].getAttribute("src") !== url) images[position].src = url;
  };
  playTogether(panel, variants.map((variant) => variant.frames), cells, controls, drawFrame, payload.follows_replay);
}
