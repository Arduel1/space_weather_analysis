import { state } from "./state.js";
import { element, utcText } from "./core.js";

// Shortest time around its own time a frame stands for, half an hour; at moments no frame stands for, the sequence has
// no image, which the player shows instead of the next older frame
export const FRAME_REACH_MIN_MS = 30 * 60 * 1000;

// Longest chart view that is replayed, one week in seconds; a replay of longer views would pass too fast to follow
export const REPLAY_MAX_VIEW_S = 7 * 86400;

// Spacings in hours the time marks under the replay slider can take, from one hour to one week
const REPLAY_MARK_STEPS_H = [1, 2, 3, 6, 12, 24, 48, 96, 168];

/** Returns how far around its time a frame of a sequence stands for the moment shown, from the typical frame spacing. */
export function frameReach(frameTimes) {
  // A frame stands for three typical spacings around it; the median spacing is typical, unaffected by single gaps
  const spacings = frameTimes.slice(1).map((time, index) => time - frameTimes[index]).sort((first, second) => first - second);
  const typical = spacings.length > 0 ? spacings[Math.floor(spacings.length / 2)] : 0;
  return Math.max(typical * 3, FRAME_REACH_MIN_MS);
}

/** Returns the index of the frame standing for a moment: the last one at or before it within the reach, else the first one if the moment lies just before it; -1 without such a frame. */
export function frameNear(frameTimes, reach, moment) {
  if (frameTimes.length === 0) return -1;
  const index = frameIndexAt(frameTimes, moment);
  // frameIndexAt gives the first frame for a moment before all frames, which then lies after the moment
  const distance = moment >= frameTimes[index] ? moment - frameTimes[index] : frameTimes[index] - moment;
  return distance <= reach ? index : -1;
}

/** Moves every panel of the shown page to a replay time; null shows the newest data. */
export function setReplayTime(time) {
  state.replayTime = time;
  for (const listener of state.replayListeners) listener.follow(time);
}

/** Registers how the panel being drawn follows the replay time. */
export function onReplay(follow) {
  state.replayListeners.push({ panel: state.drawingPanel.id, follow });
}

/** Creates the time marks under the replay slider: full hours or days, spaced so that they do not crowd. */
function replayMarks(span) {
  const marks = element("div", "replay-marks");
  const hours = (span[1] - span[0]) / 3600000;
  // The shortest spacing giving at most nine marks, else the longest spacing
  const stepHours = REPLAY_MARK_STEPS_H.find((step) => hours / step <= 9) ?? REPLAY_MARK_STEPS_H[REPLAY_MARK_STEPS_H.length - 1];
  const step = stepHours * 3600000;
  // Marks start at the first whole multiple of the step in the span, which is a full hour or day in UTC
  for (let time = Math.ceil(span[0] / step) * step; time <= span[1]; time += step) {
    // utcText gives `{yyyy}-{mm}-{dd} {hh}:{mm}`: characters 5 to 10 are month and day, 11 to 16 the time
    const text = utcText(new Date(time));
    const mark = element("span", "", stepHours >= 24 ? text.slice(5, 10) : text.slice(11, 16) === "00:00" ? text.slice(5, 10) : text.slice(11, 16));
    mark.style.left = `${((time - span[0]) / (span[1] - span[0])) * 100}%`;
    marks.append(mark);
  }
  return marks;
}

/** Creates the replay bar of a page: play, a time slider with time marks over the span of its images, and a return to the newest data. */
export function replayBar(initialSpan) {
  let span = initialSpan;
  const bar = element("div", "replay");
  const play = element("button", "", "Play");
  play.type = "button";
  const track = element("div", "replay-track");
  const slider = element("input");
  slider.type = "range";
  slider.min = span[0];
  slider.max = span[1];
  // The slider moves in whole minutes
  slider.step = 60 * 1000;
  slider.value = state.replayTime ?? span[1];
  slider.setAttribute("aria-label", "Shown time");
  let marks = replayMarks(span);
  track.append(slider, marks);
  const label = element("span", "replay-time");
  const live = element("button", "", "Newest");
  live.type = "button";
  const showLabel = () => {
    label.textContent = state.replayTime === null ? "Showing the newest data" : `Showing ${utcText(new Date(state.replayTime))} UTC`;
  };
  const stop = () => {
    clearInterval(state.replayTimer);
    state.replayTimer = null;
    play.textContent = "Play";
  };
  const moveTo = (time) => {
    slider.value = time;
    setReplayTime(time);
    showLabel();
  };
  slider.addEventListener("input", () => {
    stop();
    moveTo(Number(slider.value));
  });
  play.addEventListener("click", () => {
    if (state.replayTimer !== null) return stop();
    document.querySelectorAll(".panel").forEach((panel) => panel.preload?.());
    play.textContent = "Pause";
    // A replay starts from the beginning unless it was paused inside the span
    let time = state.replayTime === null || state.replayTime >= span[1] ? span[0] : state.replayTime;
    // Every 100 ms the replay moves on by 1/300 of the span, at least one minute: a whole span plays in 30 seconds
    state.replayTimer = setInterval(() => {
      time = Math.min(time + Math.max((span[1] - span[0]) / 300, 60 * 1000), span[1]);
      moveTo(time);
      if (time >= span[1]) stop();
    }, 100);
  });
  live.addEventListener("click", () => {
    stop();
    slider.value = span[1];
    setReplayTime(null);
    showLabel();
  });
  showLabel();
  bar.append(play, track, live, label);
  // New data move the end of the span; a running replay goes on over the longer span
  bar.setSpan = (newSpan) => {
    span = newSpan;
    slider.min = span[0];
    slider.max = span[1];
    if (state.replayTime === null) slider.value = span[1];
    const newMarks = replayMarks(span);
    marks.replaceWith(newMarks);
    marks = newMarks;
  };
  return bar;
}

/** Returns the index of the last frame at or before a time, the first frame for an earlier time; frames are sorted by time. */
export function frameIndexAt(frameTimes, time) {
  // Binary search keeping frameTimes[low] <= time; rounding the middle up lets low move and the loop end
  let low = 0;
  let high = frameTimes.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (frameTimes[middle] <= time) low = middle;
    else high = middle - 1;
  }
  return low;
}
