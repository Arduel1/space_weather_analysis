// Mutable state shared by the modules of the dashboard
export const state = {
  // Freshness labels on the page with the datasets they rate, updated by a timer
  freshnessLabels: [],
  // The layout document of the dashboard with its pages; null until it is loaded
  layout: null,
  // The loaded panel documents of every page, by panel ID
  payloadsById: {},
  // Panels of the shown page that follow the replay time: panel ID and a function taking the time in milliseconds,
  // null for now
  replayListeners: [],
  // The replay time of the shown page in milliseconds; null shows the newest data
  replayTime: null,
  // Chart elements of the shown page by panel ID and position, kept while the page stays selected
  chartsByKey: new Map(),
  // The panel being drawn, whose charts chartDiv numbers
  drawingPanel: { id: "", charts: 0 },
  // Choices of the viewer per panel, such as the hide-invalid switch, kept across redraws
  panelChoices: new Map(),
  // Timers of the playing videos of the shown page, stopped when the page is drawn again
  videoTimers: [],
  // The running replay, stopped when the page is drawn again
  replayTimer: null,
};
