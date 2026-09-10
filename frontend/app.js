// Front end of the new-listing wizard: renders the form, auto-fills, AI-matches and publishes.
let TAXONOMY = {};

let SPEC_FIELDS = [];
let FEATURE_GROUPS = {};

// Loads spec fields and feature groups from schema.json and builds the brand, model, trim and year TAXONOMY.
async function loadFormSchema() {
  const res = await fetch("/schema.json");
  const schema = await res.json();
  SPEC_FIELDS = schema.specFields;
  FEATURE_GROUPS = schema.featureGroups;

  try {
    const taxRes = await fetch("/taxonomy.json");
    const tax = await taxRes.json();
    TAXONOMY = {};
    (tax.brands || []).forEach((b) => {
      TAXONOMY[b.name] = {};
      (b.models || []).forEach((m) => {
        TAXONOMY[b.name][m.name] = { trims: m.trims || [], years: m.years || [] };
      });
    });
  } catch (e) {
    console.error("Couldn't load /taxonomy.json — brand dropdown will be empty:", e);
  }
}

// Renders the specifications grid as select or text inputs from SPEC_FIELDS.
function renderSpecFields() {
  const grid = document.getElementById("specGrid");
  grid.innerHTML = SPEC_FIELDS.map((f) => {
    const req = f.required ? ' <span class="required">*</span>' : "";
    if (f.type === "select") {
      const opts = f.options.map((o) => `<option value="${o}">${o}</option>`).join("");
      return `<div class="field"><label for="${f.id}">${f.label}${req}</label>
        <select id="${f.id}"><option value="">Select ${f.label}</option>${opts}</select></div>`;
    }
    return `<div class="field"><label for="${f.id}">${f.label}${req}</label>
      <input type="text" id="${f.id}" placeholder="${f.placeholder || ""}" /></div>`;
  }).join("");
}

// Renders a checkbox grid per feature group and attaches its search bar, warning on missing containers.
function renderFeatureGrids() {
  Object.entries(FEATURE_GROUPS).forEach(([key, items]) => {
    const el = document.getElementById(`grid-${key}`);
    if (!el) {
      console.warn(`renderFeatureGrids: no #grid-${key} container for ${items.length} feature(s)`);
      return;
    }
    el.innerHTML = items.map((label) => `
      <label class="checkbox">
        <input type="checkbox" name="feature-${key}" value="${label}" /> ${label}
      </label>`).join("");
    attachFeatureSearch(key, el, items.length);
  });
}

// Adds a search box and selected-only toggle above a feature grid that hides non-matching checkboxes.
function attachFeatureSearch(key, grid, total) {
  const existing = document.getElementById(`search-${key}`);
  if (existing) existing.remove();

  const bar = document.createElement("div");
  bar.className = "feature-search";
  bar.id = `search-${key}`;
  bar.innerHTML = `
    <input type="search" class="feature-search-input" placeholder="Search ${total} features…"
           aria-label="Search features" autocomplete="off" />
    <label class="feature-search-toggle">
      <input type="checkbox" class="feature-only-selected" /> Selected only
    </label>
    <span class="feature-search-count" aria-live="polite"></span>`;
  grid.parentNode.insertBefore(bar, grid);

  const input = bar.querySelector(".feature-search-input");
  const onlySelected = bar.querySelector(".feature-only-selected");
  const count = bar.querySelector(".feature-search-count");

  // Shows or hides feature checkboxes by search term and selected-only filter and updates the count text.
  function apply() {
    const term = input.value.trim().toLowerCase();
    const selectedOnly = onlySelected.checked;
    let shown = 0;
    let checked = 0;

    grid.querySelectorAll("label.checkbox").forEach((label) => {
      const box = label.querySelector("input[type=checkbox]");
      if (box.checked) checked++;
      const matchesTerm = !term || label.textContent.toLowerCase().includes(term);
      const matchesFilter = !selectedOnly || box.checked;
      const visible = (matchesTerm && matchesFilter) || (box.checked && !term && !selectedOnly);
      label.classList.toggle("is-hidden", !visible);
      if (visible) shown++;
    });

    count.textContent = (term || selectedOnly)
      ? `${shown} of ${total} shown · ${checked} selected`
      : (checked ? `${checked} of ${total} selected` : "");
    grid.classList.toggle("is-empty", shown === 0);
  }

  input.addEventListener("input", apply);
  onlySelected.addEventListener("change", apply);
  grid.addEventListener("change", apply);
  apply();
}

// Re-runs every feature search filter so shown and selected counts match current checkbox state.
function refreshFeatureCounts() {
  document.querySelectorAll(".feature-search-input").forEach((input) => {
    input.dispatchEvent(new Event("input"));
  });
}

// Fills the brand dropdown with every brand in TAXONOMY.
function populateBrandDropdown() {
  const brandSelect = document.getElementById("brand");
  brandSelect.innerHTML = '<option value="">Search and select a brand</option>' +
    Object.keys(TAXONOMY).map((b) => `<option value="${b}">${b}</option>`).join("");
}

// Repopulates model, trim and year dropdowns from TAXONOMY when brand or model changes.
function wireCascadingDropdowns() {
  const brandSelect = document.getElementById("brand");
  const modelSelect = document.getElementById("model");
  const trimSelect = document.getElementById("trim");
  const yearSelect = document.getElementById("year");

  brandSelect.addEventListener("change", () => {
    const models = TAXONOMY[brandSelect.value] || {};
    modelSelect.innerHTML = '<option value="">Search and select a model</option>' +
      Object.keys(models).map((m) => `<option value="${m}">${m}</option>`).join("");
    trimSelect.innerHTML = '<option value="">Search and select a trim</option>';
    yearSelect.innerHTML = '<option value="">Search and select a year</option>';
  });

  modelSelect.addEventListener("change", () => {
    const entry = (TAXONOMY[brandSelect.value] || {})[modelSelect.value];
    trimSelect.innerHTML = '<option value="">Search and select a trim</option>' +
      (entry ? entry.trims.map((t) => `<option value="${t}">${t}</option>`).join("") : "");
    yearSelect.innerHTML = '<option value="">Search and select a year</option>' +
      (entry ? entry.years.map((y) => `<option value="${y}">${y}</option>`).join("") : "");
  });
}

const STEP_ORDER = ["details", "photos", "publish"];

// Shows the named wizard panel, marks active and done steps, and renders the review on the publish step.
function goToStep(stepName) {
  STEP_ORDER.forEach((s) => {
    document.getElementById(`panel-${s}`).hidden = s !== stepName;
  });
  document.querySelectorAll(".step").forEach((btn) => {
    const s = btn.dataset.step;
    btn.classList.toggle("step--active", s === stepName);
    btn.classList.toggle("step--done", STEP_ORDER.indexOf(s) < STEP_ORDER.indexOf(stepName));
  });
  if (stepName === "publish") renderReviewSummary();
}

// Attaches click handlers that move the wizard to the step named by data-step or data-next.
function wireStepNav() {
  document.querySelectorAll(".step, [data-next]").forEach((el) => {
    el.addEventListener("click", () => {
      const target = el.dataset.step || el.dataset.next;
      if (target) goToStep(target);
    });
  });
}

// Wires description toolbar buttons to run their execCommand formatting on the editor.
function wireEditorToolbar() {
  document.querySelectorAll(".editor-toolbar button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.execCommand(btn.dataset.cmd, false, null);
      document.getElementById("descriptionEditor").focus();
    });
  });
}

// Appends a removable photo thumbnail to the exterior or interior thumbnail grid.
function addThumb(kind, url, alt) {
  const grid = document.getElementById(`thumbs-${kind}`);
  const div = document.createElement("div");
  div.className = "thumb";
  div.innerHTML = `<img src="${url}" alt="${alt}" /><div class="remove">✕</div>`;
  div.querySelector(".remove").addEventListener("click", () => div.remove());
  grid.appendChild(div);
}

// Wires click, drag-and-drop and file input handling for an exterior or interior photo dropzone.
function wireDropzone(kind) {
  const zone = document.getElementById(`dropzone-${kind}`);
  const input = document.getElementById(`fileInput-${kind}`);

  zone.addEventListener("click", (e) => {
    if (e.target.closest(".remove")) return;
    input.click();
  });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("dragover"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    addThumbs(e.dataTransfer.files);
  });
  input.addEventListener("change", () => addThumbs(input.files));

  // Reads each image file as a data URI and adds it as a thumbnail.
  function addThumbs(fileList) {
    Array.from(fileList).forEach((file) => {
      if (!file.type.startsWith("image/")) return;
      const reader = new FileReader();
      reader.onload = () => addThumb(kind, reader.result, file.name);
      reader.readAsDataURL(file);
    });
  }
}

// Fills a text field or selects a matching existing option, flagging unlisted values; returns a status.
function setSelectValue(id, value) {
  const el = document.getElementById(id);
  if (!el || value === undefined || value === null || String(value) === "") {
    return { status: "skipped", field: id, value };
  }
  const strValue = String(value);
  if (el.tagName !== "SELECT") {
    el.value = strValue;
    return { status: "set", field: id, value: strValue };
  }
  // Lowercases a value and strips non-alphanumeric characters for option comparison.
  const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");
  const target = norm(strValue);
  const match = Array.from(el.options).find(
    (o) => o.value === strValue || (o.value && norm(o.value) === target)
  );

  if (!match) {
    el.dataset.unresolved = strValue;
    el.classList.add("field-unresolved");
    console.warn(
      `setSelectValue: "${strValue}" is not an option on #${id} — leaving it blank. ` +
      `The admin panel only allows selecting an existing entry, never adding one.`
    );
    return { status: "not_in_list", field: id, value: strValue };
  }

  delete el.dataset.unresolved;
  el.classList.remove("field-unresolved");
  el.value = match.value;
  return { status: "set", field: id, value: match.value };
}

// Applies extracted identity fields, description and feature ticks, skipping locked fields; returns errors.
function applyExtractedData(data, skipFields) {
  skipFields = skipFields || new Set();
  const errors = [];

  try {
    if (!skipFields.has("brand")) {
      setSelectValue("brand", data.brand);
      document.getElementById("brand").dispatchEvent(new Event("change"));
    }
    if (!skipFields.has("model")) {
      setSelectValue("model", data.model);
      document.getElementById("model").dispatchEvent(new Event("change"));
    }
    if (!skipFields.has("trim")) setSelectValue("trim", data.trim);
    if (!skipFields.has("year")) setSelectValue("year", data.year);
  } catch (e) { errors.push(`brand/model/trim/year: ${e.message}`); }


  try {
    if (data.description) {
      document.getElementById("descriptionEditor").innerText = data.description;
    }
  } catch (e) { errors.push(`description: ${e.message}`); }

  try {
    const features = data.features;
    if (Array.isArray(features)) {
      features.forEach((label) => {
        if (typeof label !== "string") return;
        const box = document.querySelector(`input[name^="feature-"][value="${CSS.escape(label)}"]`);
        if (box) box.checked = true;
      });
    } else if (features && typeof features === "object") {
      Object.entries(features).forEach(([group, labels]) => {
        if (!Array.isArray(labels)) return;
        labels.forEach((label) => {
          if (typeof label !== "string") return;
          const box = document.querySelector(`input[name="feature-${group}"][value="${CSS.escape(label)}"]`);
          if (box) box.checked = true;
        });
      });
    }
  } catch (e) { errors.push(`features: ${e.message}`); }

  if (errors.length) console.warn("applyExtractedData: some sections failed:", errors);
  return errors;
}

// Converts a FastAPI error body's detail (string, list or object) into a readable message.
function errorDetailToMessage(err, fallback) {
  const detail = err && err.detail;
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && typeof d === "object" ? (d.msg || JSON.stringify(d)) : String(d))).join("; ");
  }
  if (typeof detail === "object" && detail.message) return detail.message;
  return JSON.stringify(detail);
}

// Returns the deduplicated values of every feature checkbox rendered on the page.
function scrapeAvailableFeatures() {
  return Array.from(new Set(
    Array.from(document.querySelectorAll('input[type="checkbox"][name^="feature-"]')).map((el) => el.value)
  ));
}

// Posts raw features to /match-features, ticks returned checkboxes and renders unmatched suggestions.
async function matchFeaturesWithAI(data) {
  const availableFeatures = scrapeAvailableFeatures();
  if (!availableFeatures.length) return { matched: 0, suggested: 0, error: null };

  const res = await fetch("/match-features", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      raw_features: data.raw_features || [],
      description: data.description || "",
      available_features: availableFeatures,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(errorDetailToMessage(err, `Server returned ${res.status}`));
  }
  const result = await res.json();
  let matched = 0;
  (result.features || []).forEach((label) => {
    document.querySelectorAll(`input[type="checkbox"][name^="feature-"][value="${CSS.escape(label)}"]`).forEach((box) => {
      if (!box.checked) {
        box.checked = true;
        matched++;
      }
    });
  });
  refreshFeatureCounts();
  const suggested = result.suggested_features || [];
  renderFeatureSuggestions(suggested);
  return { matched, suggested: suggested.length, error: null };
}

// Lists unmatched feature suggestions, important first and others collapsed, as click-to-add items.
function renderFeatureSuggestions(suggestions) {
  const card = document.getElementById("suggestedFeaturesCard");
  const importantList = document.getElementById("suggestedFeaturesImportant");
  const otherDetails = document.getElementById("suggestedFeaturesOtherDetails");
  const otherSummary = document.getElementById("suggestedFeaturesOtherSummary");
  const otherList = document.getElementById("suggestedFeaturesOther");
  importantList.innerHTML = "";
  otherList.innerHTML = "";

  if (!suggestions.length) {
    card.hidden = true;
    otherDetails.hidden = true;
    return;
  }
  card.hidden = false;

  // Adds a suggestion list item that appends its label to Additional Info and removes itself on click.
  const addBullet = (label, targetList) => {
    const item = document.createElement("li");
    item.textContent = label;
    item.title = "Click to add to Additional Info";
    item.addEventListener("click", () => {
      addFeatureAsBulletPoint(label);
      item.remove();
      if (!importantList.children.length && !otherList.children.length) card.hidden = true;
      if (!otherList.children.length) otherDetails.hidden = true;
    });
    targetList.appendChild(item);
  };

  const other = suggestions.filter((s) => !s.important);
  suggestions.filter((s) => s.important).forEach((s) => addBullet(s.label, importantList));
  if (other.length) {
    otherSummary.textContent = `Show ${other.length} more`;
    other.forEach((s) => addBullet(s.label, otherList));
    otherDetails.hidden = false;
  } else {
    otherDetails.hidden = true;
  }
}

// Appends a label as a new line in the Additional Info field unless it is already present.
function addFeatureAsBulletPoint(label) {
  const field = document.getElementById("additionalInfo");
  const lines = field.value.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.includes(label)) return;
  lines.push(label);
  field.value = lines.join("\n");
}

// Returns id, label, type and options for every field rendered in the specifications grid.
function scrapeAvailableFields() {
  return Array.from(document.querySelectorAll("#specGrid .field")).map((fieldDiv) => {
    const select = fieldDiv.querySelector("select");
    const input = fieldDiv.querySelector("input");
    const el = select || input;
    if (!el || !el.id) return null;
    const labelEl = fieldDiv.querySelector("label");
    const label = labelEl ? labelEl.textContent.replace("*", "").trim() : el.id;
    if (select) {
      const options = Array.from(select.options).map((o) => o.value).filter((v) => v !== "");
      return { id: el.id, label, type: "select", options };
    }
    return { id: el.id, label, type: "text", options: [] };
  }).filter(Boolean);
}

// Posts specs and features to /match-all in one request, then ticks features and fills unlocked fields.
async function matchAllWithAI(data, skipFields) {
  skipFields = skipFields || new Set();
  const availableFeatures = scrapeAvailableFeatures();
  const availableFields = scrapeAvailableFields();
  const rawSpecifications = data.raw_specifications || [];
  if (!availableFeatures.length && !availableFields.length) {
    return { featuresMatched: 0, suggested: 0, fieldsMatched: 0 };
  }

  const res = await fetch("/match-all", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      raw_specifications: rawSpecifications,
      raw_features: data.raw_features || [],
      description: data.description || "",
      available_fields: availableFields,
      available_features: availableFeatures,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(errorDetailToMessage(err, `Server returned ${res.status}`));
  }
  const result = await res.json();

  let featuresMatched = 0;
  (result.features || []).forEach((label) => {
    document.querySelectorAll(`input[type="checkbox"][name^="feature-"][value="${CSS.escape(label)}"]`).forEach((box) => {
      if (!box.checked) {
        box.checked = true;
        featuresMatched++;
      }
    });
  });
  refreshFeatureCounts();
  const suggested = result.suggested_features || [];
  renderFeatureSuggestions(suggested);

  let fieldsMatched = 0;
  Object.entries(result.fields || {}).forEach(([fieldId, value]) => {
    if (skipFields.has(fieldId)) return;
    if (value === null || value === undefined || String(value) === "") return;
    setSelectValue(fieldId, value);
    fieldsMatched++;
  });

  return { featuresMatched, suggested: suggested.length, fieldsMatched };
}


// Posts raw specifications to /match-fields and fills each returned field not in skipFields.
async function matchFieldsWithAI(rawSpecifications, description, skipFields) {
  skipFields = skipFields || new Set();
  const availableFields = scrapeAvailableFields();
  if (!availableFields.length || !rawSpecifications || !rawSpecifications.length) {
    return { matched: 0, error: null };
  }

  const res = await fetch("/match-fields", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      raw_specifications: rawSpecifications,
      description: description || "",
      available_fields: availableFields,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(errorDetailToMessage(err, `Server returned ${res.status}`));
  }
  const result = await res.json();
  let matched = 0;
  Object.entries(result.fields || {}).forEach(([fieldId, value]) => {
    if (skipFields.has(fieldId)) return;
    if (value === null || value === undefined || String(value) === "") return;
    setSelectValue(fieldId, value);
    matched++;
  });
  return { matched, error: null };
}

// Fills vehicle identity and colour fields, locks them, shows colour choices and returns unmatched colours.
function applyVehicleFields(vehicle, skipFields) {
  if (!vehicle) return [];
  document.getElementById("stockId").value = vehicle.stock_id || "";
  setSelectValue("brand", vehicle.brand);
  document.getElementById("brand").dispatchEvent(new Event("change"));
  setSelectValue("model", vehicle.model);
  document.getElementById("model").dispatchEvent(new Event("change"));
  setSelectValue("trim", vehicle.trim);
  setSelectValue("year", vehicle.year);
  setSelectValue("fuelType", vehicle.fuel_type);
  setSelectValue("engineSize", vehicle.engine_size);
  setSelectValue("transmission", vehicle.transmission);

  const unresolved = [];
  [["color", vehicle.exterior_color, "Exterior colour"],
   ["interiorColor", vehicle.interior_color, "Interior colour"]].forEach(([fieldId, res, label]) => {
    if (!res) return;
    if (res.status === "matched" && res.name) {
      setSelectValue(fieldId, res.name);
      skipFields.add(fieldId);
    } else {
      const el = document.getElementById(fieldId);
      if (el) el.classList.add("field-unresolved");
      skipFields.add(fieldId);
      if (res.status === "ambiguous") {
        return;
      }
      unresolved.push(`${label} ("${res.raw}")`);
    }
  });

  renderColorChoices(vehicle.color_choices, skipFields);

  ["brand", "model", "trim", "year", "fuelType", "engineSize", "transmission"].forEach((f) => skipFields.add(f));
  return unresolved;
}

// Renders clickable colour variants that set both colour fields, noting values not in the list.
function renderColorChoices(choices, skipFields) {
  const card = document.getElementById("colorChoiceCard");
  const list = document.getElementById("colorChoiceList");
  if (!card || !list) return;

  list.innerHTML = "";
  if (!choices || !choices.length) {
    card.hidden = true;
    return;
  }

  choices.forEach((choice) => {
    const item = document.createElement("li");
    item.textContent = `${choice.exterior} — interior ${choice.interior}`;
    item.title = "Click to set both colour fields to this";
    item.addEventListener("click", () => {
      const ext = setSelectValue("color", choice.exterior);
      const int = setSelectValue("interiorColor", choice.interior);
      if (skipFields) { skipFields.add("color"); skipFields.add("interiorColor"); }

      const missed = [];
      if (ext && ext.status === "not_in_list") missed.push(`"${choice.exterior}"`);
      if (int && int.status === "not_in_list") missed.push(`"${choice.interior}"`);
      if (missed.length) {
        card.querySelector("legend").insertAdjacentHTML("beforeend",
          ` <span style="color:#b45309;">${missed.join(" and ")} ${missed.length > 1 ? "are" : "is"} not in the panel's colour list — set by hand.</span>`);
        return;
      }
      card.hidden = true;
    });
    list.appendChild(item);
  });
  card.hidden = false;
}

// Fills the AED price field from the stock sheet value when the field is still empty.
function applyPriceFromExcel(priceAed) {
  if (priceAed === null || priceAed === undefined || priceAed === "") return;
  const el = document.getElementById("priceAed");
  if (el && !el.value) el.value = priceAed;
}

// Runs combined AI matching and returns a status message suffix summarizing matches or the failure.
async function _runAiMatching(extraction, skipFields) {
  try {
    const r = await matchAllWithAI(extraction, skipFields);
    let suffix = r.featuresMatched
      ? ` AI matched ${r.featuresMatched} additional feature(s) by meaning.`
      : ` AI feature matching found no further matches.`;
    if (r.suggested) {
      suffix += ` ${r.suggested} feature(s) found on the sheet have no matching checkbox — review the Suggested Features section below.`;
    }
    suffix += r.fieldsMatched
      ? ` AI filled ${r.fieldsMatched} specification field(s).`
      : ` AI field matching found nothing to fill.`;
    return suffix;
  } catch (e) {
    return ` AI matching failed (${e.message}) — checkboxes ticked above are still applied, but no specification fields were filled.`;
  }
}

// Returns a collapsible HTML block showing the raw extraction JSON, or an empty string.
function _rawDetailsHtml(extraction) {
  if (!extraction) return "";
  return `<details style="margin-top:6px;"><summary style="cursor:pointer;">View raw extracted JSON</summary><pre style="white-space:pre-wrap; font-size:11px; max-height:300px; overflow:auto;">${JSON.stringify(extraction, null, 2).replace(/</g, "&lt;")}</pre></details>`;
}

let _autoFilledFor = null;

let _incompleteExtraction = null;

const TRUNCATION_MARKER = "TRUNCATED:";

// Separates the TRUNCATED note from the other low-confidence field entries.
function _splitTruncationNote(lowConfidenceFields) {
  const notes = lowConfidenceFields || [];
  const truncation = notes.find((n) => typeof n === "string" && n.startsWith(TRUNCATION_MARKER));
  const rest = notes.filter((n) => n !== truncation);
  return { truncation, rest };
}

// Stores the truncation note and shows or hides the incomplete-extraction warning, resetting its checkbox.
function _setIncompleteExtraction(note) {
  _incompleteExtraction = note || null;
  const box = document.getElementById("incompleteWarning");
  const body = document.getElementById("incompleteWarningBody");
  const ack = document.getElementById("incompleteAck");
  if (!box) return;
  if (!note) {
    box.hidden = true;
    if (ack) ack.checked = false;
    return;
  }
  body.textContent = note.replace(TRUNCATION_MARKER, "").trim();
  if (ack) ack.checked = false;
  box.hidden = false;
}

// Requests auto-fill for a Stock ID, then applies vehicle fields, price, extraction, photos and AI matching.
async function _runAutoFill(modelNumber) {
  const status = document.getElementById("extractionStatus");

  status.hidden = false;
  status.className = "extraction-status";
  status.textContent = `Locating "${modelNumber}" in SharePoint and reading its spec sheet…`;

  try {
    const res = await fetch(`/inventory/new/${encodeURIComponent(modelNumber)}/auto-fill`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(errorDetailToMessage(err, `Server returned ${res.status}`));
    }
    const data = await res.json();
    console.log("Auto-fill result:", data);

    const skipFields = new Set();
    let unresolvedColors = [];
    try {
      unresolvedColors = applyVehicleFields(data.vehicle, skipFields);
      applyPriceFromExcel(data.price_aed);
    } catch (e) {
      console.error("Applying model_code/price fields failed (continuing):", e);
    }

    let message;
    let applyErrors = [];
    let truncation = null;
    if (data.extraction) {
      applyErrors = applyExtractedData(data.extraction, skipFields);
      const split = _splitTruncationNote(data.extraction.low_confidence_fields);
      truncation = split.truncation;
      message = truncation
        ? `INCOMPLETE — only part of the spec sheet could be read. ${truncation.replace(TRUNCATION_MARKER, "").trim()}`
        : split.rest.length
        ? `Filled from the spec sheet. Please double-check: ${split.rest.join(", ")}.`
        : `Filled from the spec sheet. Please review before publishing.`;
      if (truncation && split.rest.length) {
        message += ` Also double-check: ${split.rest.join(", ")}.`;
      }
      if (applyErrors.length) {
        message += ` Some sections didn't match the expected format and were skipped: ${applyErrors.join("; ")}.`;
      }
    } else {
      message = `Spec sheet extraction failed (${data.extraction_error}) — fields above are from the Model Code/Product Description only; fill the rest manually, or upload the spec sheet PDF directly below.`;
    }
    if (unresolvedColors.length) {
      message += ` ${unresolvedColors.join(" and ")} had no match in the panel's colour list — set ${unresolvedColors.length > 1 ? "them" : "it"} by hand.`;
    }
    _setIncompleteExtraction(truncation);

    if (data.photos) {
      (data.photos.exterior || []).forEach((p) => addThumb("exterior", p.url, p.name));
      (data.photos.interior || []).forEach((p) => addThumb("interior", p.url, p.name));
      message += ` Added ${data.photos.exterior.length} exterior and ${data.photos.interior.length} interior photo(s).`;
      if (data.photos.unclassified && data.photos.unclassified.length) {
        message += ` ${data.photos.unclassified.length} photo(s) couldn't be confidently classified — add them manually below if needed.`;
      }
    } else if (data.photos_error) {
      message += ` Photo classification failed (${data.photos_error}) — add photos manually below.`;
    }

    const rawDetails = _rawDetailsHtml(data.extraction);
    status.className = truncation
      ? "extraction-status status--error"
      : applyErrors.length || !data.extraction
      ? "extraction-status"
      : "extraction-status status--ok";
    status.innerHTML = message + (data.extraction ? " Matching fields and features with AI…" : "") + rawDetails;

    if (data.extraction) {
      message += await _runAiMatching(data.extraction, skipFields);
      status.innerHTML = message + rawDetails;
    }
  } catch (e) {
    status.className = "extraction-status status--error";
    status.textContent = `Couldn't auto-fill "${modelNumber}": ${e.message} — upload the spec sheet PDF directly below instead.`;
  }
}

// Uploads a spec sheet for extraction, then applies vehicle fields, price, extraction and AI matching.
async function _runManualUpload(modelNumber, file) {
  const status = document.getElementById("extractionStatus");

  status.hidden = false;
  status.className = "extraction-status";
  status.textContent = `Reading "${file.name}"…`;

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch(`/inventory/new/${encodeURIComponent(modelNumber || "manual-upload")}/extract-upload`, {
      method: "POST", body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(errorDetailToMessage(err, `Server returned ${res.status}`));
    }
    const data = await res.json();
    console.log("Manual upload result:", data);

    const skipFields = new Set();
    let unresolvedColors = [];
    try {
      unresolvedColors = applyVehicleFields(data.vehicle, skipFields);
      applyPriceFromExcel(data.price_aed);
    } catch (e) {
      console.error("Applying model_code/price fields failed (continuing):", e);
    }

    let message;
    let applyErrors = [];
    let truncation = null;
    if (data.extraction) {
      applyErrors = applyExtractedData(data.extraction, skipFields);
      const split = _splitTruncationNote(data.extraction.low_confidence_fields);
      truncation = split.truncation;
      message = truncation
        ? `INCOMPLETE — only part of "${file.name}" could be read. ${truncation.replace(TRUNCATION_MARKER, "").trim()}`
        : split.rest.length
        ? `Filled from "${file.name}". Please double-check: ${split.rest.join(", ")}.`
        : `Filled from "${file.name}". Please review before publishing.`;
      if (truncation && split.rest.length) {
        message += ` Also double-check: ${split.rest.join(", ")}.`;
      }
      if (applyErrors.length) {
        message += ` Some sections didn't match the expected format and were skipped: ${applyErrors.join("; ")}.`;
      }
    } else {
      message = `Couldn't extract from "${file.name}": ${data.extraction_error}`;
    }
    if (unresolvedColors.length) {
      message += ` ${unresolvedColors.join(" and ")} had no match in the panel's colour list — set ${unresolvedColors.length > 1 ? "them" : "it"} by hand.`;
    }
    _setIncompleteExtraction(truncation);

    const rawDetails = _rawDetailsHtml(data.extraction);
    status.className = truncation
      ? "extraction-status status--error"
      : applyErrors.length || !data.extraction
      ? "extraction-status"
      : "extraction-status status--ok";
    status.innerHTML = message + (data.extraction ? " Matching fields and features with AI…" : "") + rawDetails;

    if (data.extraction) {
      message += await _runAiMatching(data.extraction, skipFields);
      status.innerHTML = message + rawDetails;
    }
  } catch (e) {
    status.className = "extraction-status status--error";
    status.textContent = `Couldn't extract from "${file.name}": ${e.message}`;
  }
}

// Triggers auto-fill when the Stock ID field blurs with a new, non-empty value.
function wireAutoFill() {
  document.getElementById("stockId").addEventListener("blur", () => {
    const modelNumber = document.getElementById("stockId").value.trim();
    if (modelNumber && modelNumber !== _autoFilledFor) {
      _autoFilledFor = modelNumber;
      _runAutoFill(modelNumber);
    }
  });
}

// Runs the manual spec sheet upload when a file is chosen in the fallback input.
function wireUploadFallback() {
  const input = document.getElementById("specSheetInput");
  input.addEventListener("change", () => {
    const file = input.files[0];
    if (!file) return;
    const modelNumber = document.getElementById("stockId").value.trim();
    _runManualUpload(modelNumber, file);
    input.value = "";
  });
}

// Collects all form values into a listing record, shows it with photo counts, and returns the record.
function renderReviewSummary() {
  // Returns an element's value by id, or an empty string.
  const val = (id) => document.getElementById(id)?.value || "";
  // Returns the values of all checked inputs with the given name.
  const checked = (name) => Array.from(document.querySelectorAll(`input[name="${name}"]:checked`)).map((c) => c.value);

  const record = {
    tags: checked("tags"),
    stock_id: val("stockId"),
    brand: val("brand"), model: val("model"), trim: val("trim"), year: val("year"),
    additional_info: val("additionalInfo"),
    price_aed: val("priceAed"), price_usd: val("priceUsd"),
    specifications: Object.fromEntries(SPEC_FIELDS.map((f) => [f.id, val(f.id)])),
    description: document.getElementById("descriptionEditor").innerText.trim(),
    features: Object.fromEntries(Object.keys(FEATURE_GROUPS).map((key) => [key, checked(`feature-${key}`)])),
    photos: {
      exterior: Array.from(document.querySelectorAll("#thumbs-exterior .thumb img")).map((img) => img.src),
      interior: Array.from(document.querySelectorAll("#thumbs-interior .thumb img")).map((img) => img.src),
    },
  };
  const displayRecord = { ...record, photos: { exterior: record.photos.exterior.length, interior: record.photos.interior.length } };
  document.getElementById("reviewJson").textContent = JSON.stringify(displayRecord, null, 2);
  return record;
}

// Wires Publish to require a Stock ID and incomplete-extraction acknowledgement, then POST the listing to approve.
function wirePublishButton() {
  document.getElementById("publishBtn").addEventListener("click", async () => {
    const status = document.getElementById("publishStatus");
    const modelNumber = document.getElementById("stockId").value.trim();
    if (!modelNumber) {
      status.hidden = false;
      status.className = "extraction-status status--error";
      status.textContent = "Enter a Stock ID before publishing — it's used as this listing's unique identifier " +
        "(and, for a car sourced from Stock comparison, must match the Excel's Model Code exactly).";
      return;
    }

    const ack = document.getElementById("incompleteAck");
    if (_incompleteExtraction && ack && !ack.checked) {
      status.hidden = false;
      status.className = "extraction-status status--error";
      status.textContent = "This extraction was incomplete — parts of the spec sheet were never read. " +
        "Check the missing sections against the sheet, then tick the confirmation above to publish.";
      document.getElementById("incompleteWarning").scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }

    const record = renderReviewSummary();
    const payload = {
      brand: record.brand, model: record.model, trim: record.trim,
      year: parseInt(record.year, 10) || record.year || null,
      price_aed: parseFloat(record.price_aed) || record.price_aed || null,
      specifications: record.specifications,
      description: record.description,
      features: record.features,
      photos: record.photos,
    };

    status.hidden = false;
    status.className = "extraction-status";
    status.textContent = "Publishing…";
    try {
      const res = await fetch(`/inventory/new/${encodeURIComponent(modelNumber)}/approve`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(errorDetailToMessage(err, `Server returned ${res.status}`));
      }
      status.className = "extraction-status status--ok";
      status.textContent = `Published ${modelNumber} to inventory.`;
    } catch (e) {
      status.className = "extraction-status status--error";
      status.textContent = "Publish failed: " + e.message;
    }
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  wireAutoFill();
  wireUploadFallback();
  wireStepNav();
  wireEditorToolbar();
  wireDropzone("exterior");
  wireDropzone("interior");
  wirePublishButton();

  const prefillStockId = new URLSearchParams(window.location.search).get("stock_id");
  if (prefillStockId) {
    const stockIdInput = document.getElementById("stockId");
    if (stockIdInput) stockIdInput.value = prefillStockId;
    _autoFilledFor = prefillStockId;
    _runAutoFill(prefillStockId);
  }

  try {
    await loadFormSchema();
    renderSpecFields();
    renderFeatureGrids();
    populateBrandDropdown();
    wireCascadingDropdowns();
  } catch (e) {
    console.error("Failed to load /schema.json:", e);
    const banner = document.createElement("div");
    banner.className = "extraction-status status--error";
    banner.style.margin = "0 0 20px";
    banner.textContent = "Failed to load /schema.json — specifications and feature checkboxes won't render. Check the browser console (F12) for the actual error, and confirm the server is running and schema.json exists in public/.";
    document.querySelector(".ai-upload-bar").insertAdjacentElement("afterend", banner);
  }
});
