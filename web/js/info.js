import { cadenceWords, element, link, linkedText, richElement, utcText } from "./core.js";

/** Opens the info dialog showing the given sections. */
function openInfoDialog(sections) {
  const dialog = document.getElementById("about");
  dialog.querySelector(".about-content").replaceChildren(...sections);
  dialog.showModal();
}

/** Opens the info dialog with a heading and lines of text. */
function showText(title, lines) {
  const section = element("section", "about-dataset");
  section.append(element("h3", "", title));
  for (const line of lines) {
    const paragraph = element("p");
    paragraph.append(linkedText(line));
    section.append(paragraph);
  }
  openInfoDialog([section]);
}

/** Creates the round "i" button with a tooltip and accessible name, calling `open` on a click. */
function infoButtonWith(label, open) {
  const button = element("button", "info-button", "i");
  button.type = "button";
  // A page translation would turn the letter into a word
  button.translate = false;
  button.title = label;
  button.setAttribute("aria-label", label);
  button.addEventListener("click", open);
  return button;
}

/** Creates a small info button opening the info dialog with the given heading and lines. */
export function textInfoButton(title, lines) {
  return infoButtonWith(title, () => showText(title, lines));
}

// Words for the position of a time stamp in its measurement interval, by the value of the metadata
const TIMESTAMP_LOCATIONS = { begin: "at the start of", center: "in the middle of", end: "at the end of" };

/** Adds a labelled line to a definition list when the value is given. */
function addFact(list, label, value) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0)) return;
  list.append(element("dt", "", label));
  const detail = element("dd");
  detail.append(typeof value === "string" ? linkedText(value) : value);
  list.append(detail);
}

/** Writes the time span the provider holds data for; empty if the metadata give neither end. */
function coverageText(about) {
  if (about.coverage_start === null && about.coverage_stop === null) return "";
  const start = about.coverage_start === null ? "?" : utcText(about.coverage_start).slice(0, 10);
  const stop = about.coverage_stop === null ? "now" : utcText(about.coverage_stop).slice(0, 10);
  return `${start} to ${stop}`;
}

/** Builds the readable metadata of one dataset: description, time axis, fields, terms and the sources of the record. */
function aboutSection(about) {
  const section = element("section", "about-dataset");
  section.append(element("h3", "", about.title ?? about.dataset));
  if (about.description) {
    const description = element("p");
    description.append(linkedText(about.description));
    section.append(description);
  }
  const facts = element("dl");
  addFact(facts, "Provider", about.provider);
  if (about.cadence !== null) {
    const location = TIMESTAMP_LOCATIONS[about.timestamp_location];
    addFact(facts, "Time between two measurements", `${cadenceWords(about.cadence)}${location ? `; each time stamp lies ${location} its interval` : ""}`);
  }
  addFact(facts, "Held by the provider", coverageText(about));
  for (const region of about.observed_regions ?? []) addFact(facts, "Observed region", region.definition ? `${region.term}: ${region.definition}` : region.term);
  for (const kind of about.measurement_types ?? []) addFact(facts, "Measurement type", kind.definition ? `${kind.term}: ${kind.definition}` : kind.term);
  addFact(facts, "SPASE record", about.spase_url ? link(about.spase_url, about.spase_resource_id ?? about.spase_url) : about.spase_resource_id);
  addFact(facts, "Product page", about.resource_url ? link(about.resource_url) : null);
  for (const url of about.documentation) addFact(facts, "Documentation", link(url));
  section.append(facts);

  if (about.fields.length > 0) {
    section.append(element("h4", "", "Shown fields"));
    const fields = element("dl");
    for (const field of about.fields) {
      const parts = [field.description, field.units ? `unit ${field.units}` : null, field.validity, field.coordinate_system ? `coordinates ${field.coordinate_system}` : null];
      if (field.flag_meanings) parts.push(Object.entries(field.flag_meanings).map(([flag, meaning]) => `${flag}: ${meaning}`).join("; "));
      addFact(fields, field.label ?? field.name, parts.filter((part) => part).join(". ") || "No description in the metadata.");
    }
    section.append(fields);
  }

  const terms = element("dl");
  addFact(terms, "License", about.license);
  addFact(terms, "Acknowledgement", about.acknowledgement);
  addFact(terms, "Citation", about.citation);
  addFact(terms, "DOI", about.doi ? link(`https://doi.org/${about.doi}`, about.doi) : null);
  addFact(terms, "Rules of use", about.rules_of_use);
  if (terms.children.length > 0) section.append(element("h4", "", "Terms of use"), terms);

  return section;
}

/** Finds every quantity of a payload computed with a published formula, each once, with its name. */
export function computedQuantities(payload) {
  // Walks the whole payload, as formulas sit at any depth; the formula text keys each quantity once
  const found = new Map();
  const visit = (value) => {
    if (Array.isArray(value)) value.forEach(visit);
    else if (value !== null && typeof value === "object") {
      if (typeof value.formula === "string" && value.formula && !found.has(value.formula)) {
        found.set(value.formula, { label: value.label ?? "", formula: value.formula, reference: value.reference ?? "" });
      }
      Object.values(value).forEach(visit);
    }
  };
  visit(payload);
  return [...found.values()];
}

/** Builds the section on the quantities a panel computes from the measured ones: name, formula and its publication. */
function computedSection(quantities) {
  const section = element("section", "about-dataset");
  section.append(element("h3", "", "Computed from the measured values"));
  const list = element("dl");
  for (const quantity of quantities) {
    list.append(element("dt", "", quantity.label || "Formula"));
    const detail = element("dd");
    detail.append(richElement("div", "formula", quantity.formula));
    const reference = element("div", "caption");
    reference.append(linkedText(quantity.reference));
    detail.append(reference);
    list.append(detail);
  }
  section.append(list);
  return section;
}

/** Opens the info dialog with the help of a tile, the computed quantities and the readable metadata of every dataset of a panel. */
function showAbout(payload) {
  const sections = [];
  const help = payload.view?.help_lines ?? [];
  if (help.length > 0) {
    const section = element("section", "about-dataset");
    for (const line of help) section.append(element("p", "", line));
    sections.push(section);
  }
  const quantities = computedQuantities(payload);
  if (quantities.length > 0) sections.push(computedSection(quantities));
  sections.push(...payload.about.map(aboutSection));
  openInfoDialog(sections);
}

/** Creates the button opening the info dialog of a panel. */
export function infoButton(payload) {
  return infoButtonWith("About this data", () => showAbout(payload));
}
