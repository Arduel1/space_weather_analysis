import { state } from "./state.js";
import { DATA_URL, element, utcText } from "./core.js";
import { frameNear, frameReach, onReplay } from "./replay.js";

// Time between two frames of a playing video
export const VIDEO_FRAME_MS = 150;

// Steps a video takes through its span when it plays
export const VIDEO_STEPS = 150;

/** Marks a video stage or cell as having no image at the shown moment, or clears the mark. */
export function markMissing(stage, missing) {
  // style.css shows the text of data-missing over the faded frame
  if (missing) stage.dataset.missing = "No image at this time";
  else delete stage.dataset.missing;
}

/** Stops a timer of a playing panel and drops it from the video timers; returns null for the variable that held it. */
export function stopVideoTimer(timer) {
  clearInterval(timer);
  state.videoTimers = state.videoTimers.filter((running) => running !== timer);
  return null;
}

/** Loads the images of frames in the background, so that a played sequence does not wait for them. */
export function preloadFrames(frames) {
  // An image object that is never shown still puts the file into the browser cache
  frames.forEach((frame) => (new Image().src = DATA_URL + frame.url));
}

/** Limits a time in milliseconds to a span given as first and last time. */
export function clampToSpan(time, span) {
  return Math.min(Math.max(time, span[0]), span[1]);
}

/** Switches an element to full screen, or leaves full screen when it is active. */
export function toggleFullScreen(target) {
  if (document.fullscreenElement) document.exitFullscreen();
  else target.requestFullscreen?.();
}

/**
 * Creates the control bar of a video: play, a slider with the given accessible name, the time shown and, on request,
 * full screen. The caller sets the range of the slider and attaches the handlers.
 */
export function videoControls(sliderLabel, withFullScreen = true) {
  const bar = element("div", "video-bar");
  const play = element("button", "video-button", "Play");
  play.type = "button";
  const slider = element("input");
  slider.type = "range";
  slider.setAttribute("aria-label", sliderLabel);
  const time = element("span", "caption");
  bar.append(play, slider, time);
  let full = null;
  if (withFullScreen) {
    full = element("button", "video-button", "Full screen");
    full.type = "button";
    bar.append(full);
  }
  return { bar, play, slider, time, full };
}

/**
 * Plays several image sequences together on one time slider: each cell shows the frame of its sequence standing for the
 * common time, or is marked as missing one. Sets the preload, isPlaying and replaySpan hooks of the panel.
 * @param {HTMLElement} panel The panel of the sequences.
 * @param {object[][]} frameLists The frames of each sequence, sorted by time.
 * @param {HTMLElement[]} cells The element of each sequence that markMissing marks.
 * @param {object} controls The play button, slider and time label of videoControls.
 * @param {function} drawFrame Shows a frame, called with the position of its sequence and the frame.
 * @param {boolean} followsReplay Whether the sequences follow the replay of the page instead of their own time only.
 */
export function playTogether(panel, frameLists, cells, controls, drawFrame, followsReplay = true) {
  const frameTimes = frameLists.map((frames) => frames.map((frame) => Date.parse(frame.time)));
  const reaches = frameTimes.map(frameReach);
  // The common span runs from the earliest first frame to the latest last frame of all sequences
  const span = [Math.min(...frameTimes.map((times) => times[0])), Math.max(...frameTimes.map((times) => times[times.length - 1]))];
  const { play, slider, time } = controls;
  slider.min = span[0];
  slider.max = span[1];
  // The slider moves in whole minutes
  slider.step = 60 * 1000;
  let current = span[1];
  let timer = null;
  const show = (moment) => {
    current = moment;
    slider.value = moment;
    time.textContent = `${utcText(new Date(moment))} UTC`;
    frameLists.forEach((frames, position) => {
      const index = frameNear(frameTimes[position], reaches[position], moment);
      markMissing(cells[position], index < 0);
      if (index >= 0) drawFrame(position, frames[index]);
    });
  };
  const stop = () => {
    timer = stopVideoTimer(timer);
    play.textContent = "Play";
  };
  slider.addEventListener("input", () => {
    stop();
    show(Number(slider.value));
  });
  play.addEventListener("click", () => {
    if (timer !== null) return stop();
    panel.preload();
    play.textContent = "Pause";
    const step = (span[1] - span[0]) / VIDEO_STEPS;
    // The video loops from the start of the span after its end
    timer = setInterval(() => show(current + step > span[1] ? span[0] : current + step), VIDEO_FRAME_MS);
    state.videoTimers.push(timer);
  });
  panel.preload = () => frameLists.forEach((frames) => preloadFrames(frames));
  panel.isPlaying = () => timer !== null;
  // A zoom onto one event plays on its own time, apart from the replay of the page
  if (!followsReplay) {
    show(span[1]);
    return;
  }
  onReplay((moment) => {
    stop();
    show(moment === null ? span[1] : clampToSpan(moment, span));
  });
  show(state.replayTime === null ? span[1] : clampToSpan(state.replayTime, span));
  panel.replaySpan = span;
}

/**
 * Makes a button play the frames of a day in a loop, after loading all of them, so that the animation runs evenly.
 * @param {HTMLButtonElement} play The play button.
 * @param {string} label The text of the button while not playing.
 * @param {number} count The number of frames.
 * @param {function} loadAll Returns a promise resolving once every frame is loaded.
 * @param {function} show Shows the frame of an index.
 * @param {function} startIndex Returns the index the play starts at.
 * @param {number} stepMs The time between two frames.
 * @returns {{stop: function, playing: function}} Stops the play; tells whether it plays.
 */
export function frameLoop(play, label, count, loadAll, show, startIndex, stepMs) {
  let timer = null;
  const stop = () => {
    timer = stopVideoTimer(timer);
    play.textContent = label;
  };
  play.addEventListener("click", async () => {
    if (timer !== null) return stop();
    play.textContent = "Loading...";
    await loadAll();
    play.textContent = "Pause";
    let index = startIndex();
    timer = setInterval(() => {
      show(index);
      index = (index + 1) % count;
    }, stepMs);
    state.videoTimers.push(timer);
  });
  return { stop, playing: () => timer !== null };
}
