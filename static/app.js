let currentIndex = window.APP_CONFIG.startIndex || 0;
let stopped = false;
let currentDatasetMode = window.APP_CONFIG?.dataset?.mode || "csv";
let currentFormatPreset = "";
let currentRowAgentKey = "";
let agentTagged = false;
let datasetLoaded = false;
let apiBusy = false;
let imageLoading = false;
const prefetcher = new Image();
let filterIndices = null;
let filterPos = 0;
let resumeIndex = window.APP_CONFIG.startIndex || 0;
let suppressNextImageError = false;
let selectedCategories = [];

console.log(`[INIT] currentIndex=${currentIndex}, resumeIndex=${resumeIndex}`);

const elements = {
  progress: document.getElementById("progress"),
  indexLabel: document.getElementById("indexLabel"),
  currentCsv: document.getElementById("currentCsv"),
  datasetSelect: document.getElementById("datasetSelect"),
  datasetPath: document.getElementById("datasetPath"),
  urlColumnSelect: document.getElementById("urlColumnSelect"),
  labelColumnSelect: document.getElementById("labelColumnSelect"),
  loadDataset: document.getElementById("loadDataset"),
  resetReview: document.getElementById("resetReview"),
  resetAndDeleteOutputs: document.getElementById("resetAndDeleteOutputs"),
  agentKeySelect: document.getElementById("agentKeySelect"),
  loadAgent: document.getElementById("loadAgent"),
  exportScope: document.getElementById("exportScope"),
  exportFormat: document.getElementById("exportFormat"),
  exportData: document.getElementById("exportData"),
  previewLimit: document.getElementById("previewLimit"),
  previewExport: document.getElementById("previewExport"),
  previewPanel: document.getElementById("previewPanel"),
  previewTitle: document.getElementById("previewTitle"),
  previewContent: document.getElementById("previewContent"),
  closePreview: document.getElementById("closePreview"),
  categoryTitle: document.getElementById("categoryTitle"),
  categoryEditor: document.getElementById("categoryEditor"),
  categoryInput: document.getElementById("categoryInput"),
  categoryList: document.getElementById("categoryList"),
  categoryChips: document.getElementById("categoryChips"),
  descriptionTitle: document.getElementById("descriptionTitle"),
  descriptionEditor: document.getElementById("descriptionEditor"),
  descriptionSelect: document.getElementById("descriptionSelect"),
  descriptionText: document.getElementById("descriptionText"),
  expectedTitle: document.getElementById("expectedTitle"),
  expectedEditor: document.getElementById("expectedEditor"),
  expectedSelect: document.getElementById("expectedSelect"),
  image: document.getElementById("vehicleImage"),
  imageSpinner: document.getElementById("imageSpinner"),
  imageError: document.getElementById("imageError"),
  reviewStatus: document.getElementById("reviewStatus"),
  nopol: document.getElementById("nopol"),
  label: document.getElementById("label"),
  groundTruthTitle: document.getElementById("groundTruthTitle"),
  groundTruth: document.getElementById("groundTruth"),
  reason: document.getElementById("reason"),
  sourceUrl: document.getElementById("sourceUrl"),
  stnkReading: document.getElementById("stnkReading"),
  vehicleReading: document.getElementById("vehicleReading"),
  countKeep: document.getElementById("countKeep"),
  countDeleted: document.getElementById("countDeleted"),
  countSkipped: document.getElementById("countSkipped"),
  countRaw: document.getElementById("countRaw"),
  message: document.getElementById("message"),
  buttons: Array.from(document.querySelectorAll("button[data-action]")),
  filterScope: document.getElementById("filterScope"),
  filterNoCategory: document.getElementById("filterNoCategory"),
  applyFilter: document.getElementById("applyFilter"),
  resumeLabeling: document.getElementById("resumeLabeling"),
  clearFilter: document.getElementById("clearFilter"),
  filterStatus: document.getElementById("filterStatus"),
  continueBtn: document.getElementById("continueBtn"),
  agentRequiredHint: document.getElementById("agentRequiredHint"),
  reviewerNotesTitle: document.getElementById("reviewerNotesTitle"),
  reviewerNotesEditor: document.getElementById("reviewerNotesEditor"),
  reviewerNotesText: document.getElementById("reviewerNotesText"),
  agentKeyColLabel: document.getElementById("agentKeyColLabel"),
  agentKeyColSelect: document.getElementById("agentKeyColSelect"),
  retryImage: document.getElementById("retryImage"),
  formatPreset: document.getElementById("formatPreset"),
  uploadFile: document.getElementById("uploadFile"),
};

function statusText(status) {
  if (status === "keep") return "APPROVED";
  if (status === "deleted") return "REJECTED";
  if (status === "skipped") return "SKIP";
  return "raw";
}

function updateButtonStates() {
  const actionsDisabled = apiBusy || imageLoading || stopped || !agentTagged;
  elements.buttons.forEach((button) => {
    button.disabled = actionsDisabled;
  });
  elements.imageSpinner.hidden = !imageLoading;
  // Info panel fields must also be locked while image is loading or busy
  const fieldsDisabled = apiBusy || imageLoading;
  elements.expectedSelect.disabled = fieldsDisabled;
  elements.reviewerNotesText.disabled = fieldsDisabled;
  elements.categoryInput.disabled = fieldsDisabled;
  elements.descriptionSelect.disabled = fieldsDisabled;
  elements.descriptionText.disabled = fieldsDisabled;
  elements.loadDataset.disabled = apiBusy;
  elements.resetReview.disabled = apiBusy;
  elements.resetAndDeleteOutputs.disabled = apiBusy;
  elements.loadAgent.disabled = apiBusy || !datasetLoaded;
  elements.agentKeySelect.disabled = apiBusy || !datasetLoaded;
  elements.exportData.disabled = apiBusy || !agentTagged;
  elements.previewExport.disabled = apiBusy || !agentTagged;
  elements.applyFilter.disabled = apiBusy || !agentTagged;
  elements.resumeLabeling.disabled = apiBusy;
  elements.clearFilter.disabled = apiBusy;
  elements.continueBtn.hidden = !stopped;
  elements.continueBtn.disabled = apiBusy || !agentTagged;
  const needsAgentPick = !agentTagged && !apiBusy && datasetLoaded;
  elements.loadAgent.classList.toggle("agent-required-pulse", needsAgentPick && currentDatasetMode === "finetune_json");
  elements.agentKeySelect.classList.toggle("agent-required-pulse", needsAgentPick && currentDatasetMode !== "finetune_json");
  elements.agentRequiredHint.hidden = agentTagged || !datasetLoaded;
}

function setBusy(isBusy) {
  apiBusy = isBusy;
  updateButtonStates();
}

function fillColumnSelect(select, columns, selected) {
  select.replaceChildren();
  columns.forEach((column) => {
    const option = document.createElement("option");
    option.value = column;
    option.textContent = column;
    option.selected = column === selected;
    select.appendChild(option);
  });
}

function fillSelect(select, values, selected, includeEmpty = false) {
  select.replaceChildren();
  if (includeEmpty) {
    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = "-";
    emptyOption.selected = !selected;
    select.appendChild(emptyOption);
  }

  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = value === selected;
    select.appendChild(option);
  });

}

function renderDataset(dataset) {
  if (!dataset) return;

  elements.currentCsv.textContent = `CSV: ${dataset.path}`;
  elements.datasetPath.value = dataset.path;
  fillColumnSelect(elements.urlColumnSelect, dataset.columns || [], dataset.url_col);
  fillColumnSelect(elements.labelColumnSelect, dataset.columns || [], dataset.label_col);

  Array.from(elements.datasetSelect.options).forEach((option) => {
    option.selected = option.value === dataset.path;
  });

  if (dataset.finetune) {
    const selectedAgentKey = dataset.mode === "finetune_json"
      ? dataset.finetune.agent_key
      : (dataset.finetune.csv_tag_agent_key || dataset.finetune.agent_key);
    const agents = (dataset.finetune.agents || []).map((agent) => agent.agent_key);
    // CSV mode: include empty first option so user must actively pick an agent
    fillSelect(elements.agentKeySelect, agents, selectedAgentKey, dataset.mode !== "finetune_json");
    if (dataset.mode === "finetune_json") {
      elements.currentCsv.textContent = `JSON: ${dataset.finetune.json_path} | Agent: ${dataset.finetune.agent_key}`;
    } else if (dataset.finetune.csv_tag_agent_key) {
      elements.currentCsv.textContent = `CSV: ${dataset.path} | Tag: ${dataset.finetune.csv_tag_group_key} / ${dataset.finetune.csv_tag_agent_key}`;
    }
  }
  currentDatasetMode = dataset.mode || "csv";
  if (dataset.format_preset) {
    currentFormatPreset = dataset.format_preset;
    elements.formatPreset.value = dataset.format_preset;
  }

  const isDataTrainDataset = currentFormatPreset === "data_train";
  document.querySelector(".finetune-bar").hidden = isDataTrainDataset;
  const exportOptDataTrain = document.getElementById("exportOptDataTrain");
  if (exportOptDataTrain) exportOptDataTrain.hidden = !isDataTrainDataset;

  // "Tandai Agent" button loads records in finetune_json; csv uses dropdown change handler
  elements.loadAgent.hidden = currentDatasetMode !== "finetune_json";

  // Restore agent key column selector for data_train format
  elements.agentKeyColLabel.hidden = !isDataTrainDataset;
  if (isDataTrainDataset && dataset.columns && dataset.columns.length > 0) {
    const agentKeyCol = dataset.agent_key_col || "agent_key";
    fillColumnSelect(elements.agentKeyColSelect, dataset.columns, agentKeyCol);
  }

  agentTagged = isDataTrainDataset
    ? true
    : currentDatasetMode === "finetune_json"
      ? !!(dataset.finetune && dataset.finetune.agent_key)
      : !!(dataset.finetune && dataset.finetune.csv_tag_agent_key);

  // Toggle dashboard fields based on loaded dataset's label column
  const isDashboard = dataset.label_col === "Status";
  document.querySelectorAll(".dashboard-field").forEach((el) => {
    el.hidden = !isDashboard;
  });

  updateButtonStates();
}

function setFinetuneEditorsVisible(isVisible, showCatDesc = true, showReviewerNotes = false) {
  const showCategoryDescription = isVisible && showCatDesc;
  [elements.categoryTitle, elements.categoryEditor, elements.descriptionTitle, elements.descriptionEditor].forEach((el) => {
    el.hidden = !showCategoryDescription;
  });
  [elements.expectedTitle, elements.expectedEditor].forEach((el) => {
    el.hidden = !isVisible;
  });
  const showNotes = isVisible && showReviewerNotes;
  [elements.reviewerNotesTitle, elements.reviewerNotesEditor].forEach((el) => {
    el.hidden = !showNotes;
  });
}

function splitCategories(value) {
  return String(value || "")
    .split(";")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

function renderCategoryChips() {
  if (!elements.categoryChips) return;
  elements.categoryChips.replaceChildren();
  selectedCategories.forEach((cat, index) => {
    const chip = document.createElement("span");
    chip.className = "category-chip";
    const label = document.createElement("span");
    label.textContent = cat;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "category-chip-remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Hapus ${cat}`);
    remove.addEventListener("click", () => {
      selectedCategories.splice(index, 1);
      renderCategoryChips();
    });
    chip.appendChild(label);
    chip.appendChild(remove);
    elements.categoryChips.appendChild(chip);
  });
}

function addCategory(value) {
  const name = String(value || "").trim();
  if (!name) return;
  if (!selectedCategories.includes(name)) {
    selectedCategories.push(name);
    renderCategoryChips();
  }
}

function renderAnnotation(row) {
  const isFinetune = row.dataset && row.dataset.mode === "finetune_json";
  const isCsv = row.dataset && row.dataset.mode === "csv";
  const showEditors = isFinetune || isCsv;
  const showCatDesc = isFinetune || (isCsv && currentFormatPreset !== "data_train");
  const showReviewerNotes = isCsv && currentFormatPreset === "data_train";
  setFinetuneEditorsVisible(showEditors, showCatDesc, showReviewerNotes);
  if (!showEditors) return;

  const annotation = row.annotation || {};
  const categories = (row.dataset && row.dataset.curated_categories) || [];

  elements.categoryList.replaceChildren();
  categories.forEach((cat) => {
    const opt = document.createElement("option");
    opt.value = cat;
    elements.categoryList.appendChild(opt);
  });
  selectedCategories = splitCategories(annotation.category);
  renderCategoryChips();
  elements.categoryInput.value = "";

  fillSelect(elements.descriptionSelect, row.description_options || (row.dataset.finetune && row.dataset.finetune.descriptions) || [], annotation.description || "", true);
  elements.descriptionText.value = annotation.description || "";
  elements.expectedSelect.value = annotation.expected || "REJECTED";
  elements.reviewerNotesText.value = annotation.reviewer_notes || "";
  currentRowAgentKey = annotation.agent_key || "";
}

function currentAnnotation() {
  // data_train: agent key comes from the CSV column per-row, not from the global dropdown
  const agentKey = currentFormatPreset === "data_train"
    ? currentRowAgentKey
    : (elements.agentKeySelect ? elements.agentKeySelect.value : "");
  return {
    expected: elements.expectedSelect.value,
    category: selectedCategories.join("; "),
    description: elements.descriptionText.value,
    agent_key: agentKey,
    reviewer_notes: elements.reviewerNotesText ? elements.reviewerNotesText.value : "",
  };
}

function renderReading(container, reading) {
  container.replaceChildren();
  const entries = Object.entries(reading || {});
  if (entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "reading-empty";
    empty.textContent = "-";
    container.appendChild(empty);
    return;
  }

  entries.forEach(([key, value]) => {
    const keyElement = document.createElement("span");
    keyElement.className = "reading-key";
    keyElement.textContent = key;

    const valueElement = document.createElement("span");
    valueElement.className = "reading-value";
    valueElement.textContent = value === null || value === undefined ? "" : String(value);

    container.appendChild(keyElement);
    container.appendChild(valueElement);
  });
}

function renderRow(row, { preserveResume = false } = {}) {
  currentIndex = row.idx;
  const prevResume = resumeIndex;
  if (!filterIndices && !preserveResume) resumeIndex = currentIndex;
  console.log(`[renderRow] row.idx=${row.idx} | filterIndices=${filterIndices ? filterIndices.length + ' items' : 'null'} | preserveResume=${preserveResume} | resumeIndex: ${prevResume} → ${resumeIndex}`);
  renderDataset(row.dataset);
  renderAnnotation(row);

  if (filterIndices && filterIndices.length > 0) {
    elements.progress.max = Math.max(filterIndices.length - 1, 1);
    elements.progress.value = filterPos;
    elements.indexLabel.textContent = `${filterPos + 1} / ${filterIndices.length} (filter)`;
  } else {
    elements.progress.max = Math.max(row.total - 1, 1);
    elements.progress.value = row.idx;
    elements.indexLabel.textContent = `${row.row_number} / ${row.total}`;
  }

  elements.reviewStatus.textContent = statusText(row.status);
  elements.nopol.textContent = row.nopol || "-";
  elements.label.textContent = row.label || "-";
  
  if (row.ai_info && row.ai_info.ground_truth) {
    elements.groundTruthTitle.hidden = false;
    elements.groundTruth.hidden = false;
    elements.groundTruth.textContent = row.ai_info.ground_truth;
  } else {
    elements.groundTruthTitle.hidden = true;
    elements.groundTruth.hidden = true;
    elements.groundTruth.textContent = "-";
  }

  elements.reason.textContent = row.reason || "-";
  renderReading(elements.stnkReading, row.readings ? row.readings.stnk : {});
  renderReading(elements.vehicleReading, row.readings ? row.readings.vehicle : {});

  const extraDataContainer = document.getElementById("extraDataContainer");
  if (row.extra_data && Object.keys(row.extra_data).length > 0 && row.dataset.label_col !== "Status") {
    extraDataContainer.hidden = false;
    extraDataContainer.replaceChildren();
    Object.entries(row.extra_data).forEach(([key, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      // Add a style to ensure long data breaks properly
      dd.style.overflowWrap = "anywhere";
      dd.style.whiteSpace = "pre-wrap";
      dd.style.wordBreak = "break-word";
      dd.textContent = value === "" ? "-" : value;
      extraDataContainer.appendChild(dt);
      extraDataContainer.appendChild(dd);
    });
  } else {
    extraDataContainer.hidden = true;
    extraDataContainer.replaceChildren();
  }

  if (row.url) {
    elements.sourceUrl.textContent = row.url;
    elements.sourceUrl.href = row.url;
  } else {
    elements.sourceUrl.textContent = "-";
    elements.sourceUrl.removeAttribute("href");
  }

  elements.countKeep.textContent = row.counts.keep;
  elements.countDeleted.textContent = row.counts.deleted;
  elements.countSkipped.textContent = row.counts.skipped || 0;
  elements.countRaw.textContent = row.counts.raw;

  elements.imageError.hidden = true;
  if (row.total > 0 && row.url) {
    elements.image.hidden = false;
    imageLoading = true;
    updateButtonStates();
    elements.image.src = row.url;
    prefetchNext(row.idx + 1);
  } else {
    elements.image.hidden = true;
    elements.image.removeAttribute("src");
    imageLoading = false;
    updateButtonStates();
  }
}

function filterNavNext(fromIdx) {
  if (!filterIndices) return null;
  const pos = filterIndices.findIndex((i) => i > fromIdx);
  return pos >= 0 ? pos : null;
}

function filterNavPrev(fromIdx) {
  if (!filterIndices) return null;
  let pos = -1;
  for (let i = filterIndices.length - 1; i >= 0; i--) {
    if (filterIndices[i] < fromIdx) { pos = i; break; }
  }
  return pos >= 0 ? pos : null;
}

function updateFilterUi() {
  const active = filterIndices !== null;
  elements.clearFilter.hidden = !active;
  elements.resumeLabeling.hidden = false;
  if (active) {
    const scopeLabel = elements.filterScope.value || "Semua";
    const noCatLabel = elements.filterNoCategory.checked ? " + Kategori kosong" : "";
    elements.filterStatus.textContent =
      `Filter aktif: ${scopeLabel}${noCatLabel} (${filterIndices.length} item, posisi ${filterPos + 1}/${filterIndices.length})`;
  } else {
    elements.filterStatus.textContent = "";
  }
}

async function applyFilter() {
  const scope = elements.filterScope.value;
  const noCategory = elements.filterNoCategory.checked;
  console.log(`[applyFilter] scope="${scope}" noCategory=${noCategory} | currentIndex=${currentIndex} resumeIndex=${resumeIndex}`);
  if (!scope && !noCategory) { await resumeLabeling(); return; }

  // Snapshot the current labeling position before entering filter mode
  resumeIndex = currentIndex;

  setBusy(true);
  elements.message.textContent = "Memuat filter...";
  try {
    const params = new URLSearchParams({ scope });
    if (noCategory) params.set("no_category", "1");
    const res = await fetch(`/api/scope/indices?${params}`);
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error || "Gagal memuat filter");

    filterIndices = payload.indices;
    if (filterIndices.length === 0) {
      elements.message.textContent = `Tidak ada item dengan status "${scope}".`;
      filterIndices = null;
      filterPos = 0;
      updateFilterUi();
      setBusy(false);
      return;
    }

    // Start from closest to current index
    filterPos = filterIndices.findIndex((i) => i >= currentIndex);
    if (filterPos < 0) filterPos = 0;

    updateFilterUi();
    elements.message.textContent = `Filter "${scope}" aktif — ${filterIndices.length} item.`;
    await loadRow(filterIndices[filterPos], { saveProgress: false });
  } catch (err) {
    elements.message.textContent = `Gagal terapkan filter: ${err.message}`;
  } finally {
    setBusy(false);
  }
}

function clearFilter() {
  console.log(`[clearFilter] filterIndices was ${filterIndices ? filterIndices.length + ' items' : 'null'} | resumeIndex=${resumeIndex} currentIndex=${currentIndex}`);
  filterIndices = null;
  filterPos = 0;
  elements.filterScope.value = "";
  elements.filterNoCategory.checked = false;
  updateFilterUi();
  elements.message.textContent = "Filter dihapus.";
}

async function resumeLabeling() {
  console.log(`[resumeLabeling] START | resumeIndex=${resumeIndex} currentIndex=${currentIndex}`);
  clearFilter();
  elements.message.textContent = "Mencari posisi progress...";

  try {
    // Always ask server for the first unreviewed (raw) item — the true progress position
    const res = await fetch("/api/scope/indices?scope=raw");
    const payload = await res.json();
    console.log(`[resumeLabeling] raw indices count=${payload.indices?.length}, first=${payload.indices?.[0]}`);

    let targetIndex;
    if (payload.indices && payload.indices.length > 0) {
      targetIndex = payload.indices[0];
    } else {
      // All items reviewed — stay at current position
      targetIndex = currentIndex;
    }

    resumeIndex = targetIndex;
    console.log(`[resumeLabeling] navigating to targetIndex=${targetIndex}`);
    elements.message.textContent = `Kembali ke progress sekarang (baris ${targetIndex + 1}).`;
    await loadRow(targetIndex);
    console.log(`[resumeLabeling] done. currentIndex=${currentIndex} resumeIndex=${resumeIndex}`);
  } catch (err) {
    elements.message.textContent = `Gagal menemukan progress: ${err.message}`;
    console.error(`[resumeLabeling] error:`, err);
  }
}

async function loadDatasets() {
  const response = await fetch("/api/datasets");
  if (!response.ok) throw new Error(await response.text());
  const payload = await response.json();

  elements.datasetSelect.replaceChildren();
  payload.datasets.forEach((dataset) => {
    const option = document.createElement("option");
    option.value = dataset.path;
    option.textContent = dataset.rows === null ? dataset.path : `${dataset.path} (${dataset.rows})`;
    option.selected = dataset.path === payload.current.path;
    elements.datasetSelect.appendChild(option);
  });
  renderDataset(payload.current);
}

async function loadFinetuneMeta() {
  const response = await fetch("/api/finetune/meta");
  if (!response.ok) return;
  const payload = await response.json();
  const currentSelected = elements.agentKeySelect.value;
  fillSelect(
    elements.agentKeySelect,
    (payload.agents || []).map((agent) => agent.agent_key),
    currentSelected,
    false,
  );
}

async function switchAgent() {
  setBusy(true);
  stopped = false;

  const agentKey = elements.agentKeySelect.value;

  // In finetune_json mode: load records from JSON for the selected agent.
  // In csv mode: just tag the current CSV dataset with the chosen agent key.
  if (currentDatasetMode === "finetune_json") {
    elements.message.textContent = "Memuat agent dari JSON...";
    try {
      const response = await fetch("/api/finetune/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_key: agentKey }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Gagal memuat agent");
      renderDataset(payload.dataset);
      renderRow(payload.row);
      elements.message.textContent = "";
    } catch (error) {
      elements.message.textContent = `Gagal memuat agent: ${error.message}`;
    }
  } else {
    elements.message.textContent = "Menandai agent key...";
    try {
      const response = await fetch("/api/tag/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_key: agentKey }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Gagal menandai agent");
      renderDataset(payload.dataset);
      renderRow(payload.row);
      elements.message.textContent = `Agent key ditandai: ${agentKey}. Export "JSON siap labelling" akan menggunakan struktur yang sesuai.`;
    } catch (error) {
      elements.message.textContent = `Gagal menandai agent: ${error.message}`;
    }
  }

  setBusy(false);
}

async function switchDataset() {
  setBusy(true);
  stopped = false;
  elements.message.textContent = "Memuat dataset...";

  try {
    const isDataTrain = elements.formatPreset.value === "data_train";
    const body = {
      path: elements.datasetPath.value,
      url_col: elements.urlColumnSelect.value,
      label_col: elements.labelColumnSelect.value,
      format_preset: elements.formatPreset.value,
    };
    if (isDataTrain && elements.agentKeyColSelect.value) {
      body.agent_key_col = elements.agentKeyColSelect.value;
    }
    const response = await fetch("/api/dataset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Gagal memuat dataset");

    datasetLoaded = true;
    currentFormatPreset = elements.formatPreset.value;
    document.querySelector(".finetune-bar").hidden = isDataTrain;
    if (isDataTrain) { agentTagged = true; updateButtonStates(); }
    renderDataset(payload.dataset);
    renderRow(payload.row);
    elements.message.textContent = "";
  } catch (error) {
    elements.message.textContent = `Gagal memuat dataset: ${error.message}`;
  } finally {
    setBusy(false);
  }
}

async function resetReview(deleteOutputs) {
  const detail = deleteOutputs
    ? "Reset anotasi dataset aktif dan hapus output approved/rejected/raw?"
    : "Reset anotasi dataset aktif dari awal?";
  if (!window.confirm(detail)) return;

  setBusy(true);
  stopped = false;
  elements.message.textContent = "Mereset anotasi...";

  try {
    const response = await fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delete_outputs: deleteOutputs }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Reset gagal");

    renderDataset(payload.dataset);
    renderRow(payload.row);
    elements.message.textContent = deleteOutputs
      ? `Reset selesai. Output dihapus: ${payload.reset.deleted_outputs.length}`
      : "Reset selesai. Mulai anotasi dari baris pertama.";
  } catch (error) {
    elements.message.textContent = `Gagal reset: ${error.message}`;
  } finally {
    setBusy(false);
  }
}

async function exportData() {
  setBusy(true);
  elements.message.textContent = "Export data...";

  try {
    const response = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scope: elements.exportScope.value,
        format: elements.exportFormat.value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Export gagal");

    elements.message.textContent =
      `Export selesai (${payload.export.count} item): ${payload.export.path}`;
  } catch (error) {
    elements.message.textContent = `Gagal export: ${error.message}`;
  } finally {
    setBusy(false);
  }
}

async function previewExport() {
  setBusy(true);
  elements.message.textContent = "Memuat preview...";

  try {
    const response = await fetch("/api/export/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scope: elements.exportScope.value,
        format: elements.exportFormat.value,
        limit: Number(elements.previewLimit.value || 3),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Preview gagal");

    elements.previewTitle.textContent =
      `Preview ${payload.export.format} (${payload.export.shown}/${payload.export.total})`;
    elements.previewContent.textContent = JSON.stringify(payload.export.preview, null, 2);
    elements.previewPanel.hidden = false;
    elements.message.textContent = "";
  } catch (error) {
    elements.message.textContent = `Gagal preview: ${error.message}`;
  } finally {
    setBusy(false);
  }
}

async function loadRow(idx, { saveProgress = true } = {}) {
  console.log(`[loadRow] idx=${idx} saveProgress=${saveProgress} | resumeIndex=${resumeIndex} currentIndex=${currentIndex}`);
  setBusy(true);
  elements.message.textContent = "Memuat data...";

  try {
    const url = saveProgress ? `/api/row/${idx}` : `/api/row/${idx}?peek=1`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(await response.text());
    renderRow(await response.json(), { preserveResume: !saveProgress });
    elements.message.textContent = "";
  } catch (error) {
    elements.message.textContent = `Gagal memuat baris: ${error.message}`;
    console.error(`[loadRow] error:`, error);
  } finally {
    setBusy(false);
    console.log(`[loadRow] finished idx=${idx} | resumeIndex=${resumeIndex} currentIndex=${currentIndex}`);
  }
}

function savedSummary(saved) {
  return `Approved: ${saved.keep}, Rejected: ${saved.deleted}, Skip: ${saved.skipped || 0}, Raw: ${saved.raw}`;
}

async function sendAction(action) {
  if (apiBusy) return;

  // Filter-mode pure navigation (no API call needed)
  if (filterIndices && action === "back") {
    const prev = filterNavPrev(currentIndex);
    if (prev !== null) {
      filterPos = prev;
      updateFilterUi();
      await loadRow(filterIndices[filterPos], { saveProgress: false });
    }
    return;
  }

  setBusy(true);
  elements.message.textContent = "Menyimpan...";

  try {
    const response = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, idx: currentIndex, annotation: currentAnnotation() }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Action failed");

    if (payload.saved) {
      elements.message.textContent = payload.finished
        ? `Selesai dan tersimpan. ${savedSummary(payload.saved)}`
        : `Tersimpan. ${savedSummary(payload.saved)}`;
    } else {
      elements.message.textContent = "";
    }

    if (payload.finished) {
      stopped = true;
      if (payload.row) renderRow(payload.row);
      updateButtonStates();
      return;
    }

    // Filter-mode: navigate to next within filtered set.
    // For save, server returns same idx so we navigate from currentIndex.
    // For approve/reject/skip, server increments idx so we use payload.row.idx - 1.
    if (filterIndices && payload.row) {
      const fromIdx = action === "save" ? currentIndex : Math.max(payload.row.idx - 1, 0);
      const next = filterNavNext(fromIdx);
      if (next !== null) {
        filterPos = next;
        updateFilterUi();
        await loadRow(filterIndices[filterPos], { saveProgress: false });
      } else {
        // Reached end of filter
        if (payload.row) renderRow(payload.row);
        elements.message.textContent = "Sudah sampai akhir item dalam filter ini.";
      }
    } else if (payload.row) {
      renderRow(payload.row);
    }
  } catch (error) {
    elements.message.textContent = `Gagal memproses aksi: ${error.message}`;
  } finally {
    setBusy(false);
  }
}

async function prefetchNext(idx) {
  try {
    const res = await fetch(`/api/peek/${idx}`);
    if (!res.ok) return;
    const { url } = await res.json();
    if (url) prefetcher.src = url;
  } catch (_) {}
}

elements.image.addEventListener("load", () => {
  suppressNextImageError = false;
  elements.image.hidden = false;
  elements.imageError.hidden = true;
  imageLoading = false;
  updateButtonStates();
});

elements.image.addEventListener("error", () => {
  if (suppressNextImageError) {
    suppressNextImageError = false;
    return;
  }
  elements.image.hidden = true;
  elements.imageError.hidden = false;
  imageLoading = false;
  updateButtonStates();
});

elements.buttons.forEach((button) => {
  button.addEventListener("click", () => sendAction(button.dataset.action));
});

elements.datasetSelect.addEventListener("change", () => {
  elements.datasetPath.value = elements.datasetSelect.value;
  switchDataset();
});

elements.categoryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addCategory(elements.categoryInput.value);
    elements.categoryInput.value = "";
  }
});
elements.categoryInput.addEventListener("input", () => {
  const options = Array.from(elements.categoryList.options).map((opt) => opt.value);
  if (options.includes(elements.categoryInput.value)) {
    addCategory(elements.categoryInput.value);
    elements.categoryInput.value = "";
  }
});

elements.loadDataset.addEventListener("click", switchDataset);
elements.loadAgent.addEventListener("click", switchAgent);

// CSV mode: auto-tag agent on dropdown change (no button click needed)
elements.agentKeySelect.addEventListener("change", async () => {
  if (currentDatasetMode === "finetune_json") return;
  const agentKey = elements.agentKeySelect.value;
  agentTagged = !!agentKey;
  updateButtonStates();
  if (!agentKey || !datasetLoaded) return;
  try {
    const res = await fetch("/api/tag/agent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_key: agentKey }),
    });
    const payload = await res.json();
    if (res.ok) {
      renderDataset(payload.dataset);
      elements.message.textContent = `Agent key: ${agentKey}`;
    } else {
      elements.message.textContent = `Gagal set agent: ${payload.error}`;
      agentTagged = false;
      updateButtonStates();
    }
  } catch (err) {
    elements.message.textContent = `Gagal set agent: ${err.message}`;
  }
});
elements.exportData.addEventListener("click", exportData);
elements.previewExport.addEventListener("click", previewExport);
elements.closePreview.addEventListener("click", () => {
  elements.previewPanel.hidden = true;
});
elements.resetReview.addEventListener("click", () => resetReview(false));
elements.resetAndDeleteOutputs.addEventListener("click", () => resetReview(true));
elements.applyFilter.addEventListener("click", applyFilter);
elements.resumeLabeling.addEventListener("click", resumeLabeling);
elements.clearFilter.addEventListener("click", clearFilter);
elements.continueBtn.addEventListener("click", () => {
  stopped = false;
  updateButtonStates();
  elements.message.textContent = "Melanjutkan dari posisi terakhir.";
});

elements.descriptionSelect.addEventListener("change", () => {
  elements.descriptionText.value = elements.descriptionSelect.value;
});

// Format preset: auto-fill URL/label column when user picks a format
elements.formatPreset.addEventListener("change", () => {
  const preset = (window._formatPresets || []).find((p) => p.id === elements.formatPreset.value);

  // Toggle visibility of standard fields based on preset
  const isDashboard = preset && preset.id === "screen_document";
  document.querySelectorAll(".dashboard-field").forEach((el) => {
    el.hidden = !isDashboard;
  });

  // Show agent key column selector only for data_train format; hide agent key picker
  const isDataTrain = !!(preset && preset.id === "data_train");
  elements.agentKeyColLabel.hidden = !isDataTrain;
  document.querySelector(".finetune-bar").hidden = isDataTrain;
  if (isDataTrain) {
    const cols = Array.from(elements.urlColumnSelect.options).map((o) => o.value);
    if (cols.length > 0) fillColumnSelect(elements.agentKeyColSelect, cols, "agent_key");
  } else {
    elements.agentKeyColSelect.replaceChildren();
  }

  if (!preset || preset.id === "custom") return;
  // Set the column selects to the preset values (if available as options)
  const setSelectValue = (select, value) => {
    const opt = Array.from(select.options).find((o) => o.value === value);
    if (opt) select.value = value;
  };
  setSelectValue(elements.urlColumnSelect, preset.url_col);
  setSelectValue(elements.labelColumnSelect, preset.label_col);
});

// Upload CSV from local disk
elements.uploadFile.addEventListener("change", async () => {
  const file = elements.uploadFile.files[0];
  if (!file) return;
  setBusy(true);
  elements.message.textContent = `Mengupload ${file.name}...`;
  try {
    const preset = (window._formatPresets || []).find((p) => p.id === elements.formatPreset.value);
    const formData = new FormData();
    formData.append("file", file);
    if (preset && preset.url_col) formData.append("url_col", preset.url_col);
    if (preset && preset.label_col) formData.append("label_col", preset.label_col);

    const response = await fetch("/api/upload", { method: "POST", body: formData });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Upload gagal");

    datasetLoaded = true;
    renderDataset(payload.dataset);
    renderRow(payload.row);
    // Refresh dataset list to include the newly uploaded file
    await loadDatasets();
    elements.message.textContent = `Dataset "${file.name}" berhasil dimuat.`;
  } catch (error) {
    elements.message.textContent = `Gagal upload: ${error.message}`;
  } finally {
    setBusy(false);
    // Reset input so the same file can be re-uploaded if needed
    elements.uploadFile.value = "";
  }
});

async function loadFormatPresets() {
  try {
    const res = await fetch("/api/format-presets");
    if (!res.ok) return;
    const { presets } = await res.json();
    window._formatPresets = presets;
    presets.forEach((preset) => {
      const opt = document.createElement("option");
      opt.value = preset.id;
      opt.textContent = preset.label;
      elements.formatPreset.appendChild(opt);
    });
  } catch (_) {}
}

document.addEventListener("keydown", (event) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
  if (event.key === "ArrowLeft") sendAction("back");
  if (event.key === "ArrowRight") sendAction("skip");
  if (event.key.toLowerCase() === "a") sendAction("approve");
  if (event.key.toLowerCase() === "r") sendAction("reject");
  if (event.key.toLowerCase() === "s") sendAction("save");
});

elements.retryImage.addEventListener("click", () => {
  const src = elements.image.src;
  if (!src) return;
  imageLoading = true;
  elements.imageError.hidden = true;
  elements.image.hidden = false;
  updateButtonStates();
  suppressNextImageError = true;
  elements.image.src = "";
  elements.image.src = src;
});

window.addEventListener("offline", () => {
  elements.message.textContent = "Koneksi internet terputus. Progress tersimpan lokal — gambar mungkin tidak bisa dimuat, tapi anotasi tetap bisa dilanjutkan.";
});

window.addEventListener("online", () => {
  elements.message.textContent = "Koneksi kembali.";
});

function showResumeSessionBanner(session) {
  const banner = document.getElementById("resumeSessionBanner");
  if (!banner) return;
  const agentPart = session.tagged_agent_key ? ` | Agent: ${session.tagged_agent_key}` : "";
  const posPart = ` (posisi ${(session.current_index || 0) + 1})`;
  document.getElementById("resumeSessionLabel").textContent =
    `Session terakhir: ${session.dataset_path}${agentPart}${posPart}`;
  banner.hidden = false;

  document.getElementById("resumeSessionBtn").onclick = async () => {
    banner.hidden = true;
    await doResumeSession(session);
  };
  document.getElementById("dismissSessionBanner").onclick = () => {
    banner.hidden = true;
    elements.message.textContent = "Pilih format lalu upload file CSV, atau pilih dari dropdown Dataset.";
  };
}

async function doResumeSession(session) {
  setBusy(true);
  elements.message.textContent = "Melanjutkan session terakhir...";
  try {
    if (session.format_preset && elements.formatPreset) {
      elements.formatPreset.value = session.format_preset;
      elements.formatPreset.dispatchEvent(new Event("change"));
    }
    elements.datasetPath.value = session.dataset_path;

    const response = await fetch("/api/dataset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: session.dataset_path,
        url_col: session.url_col,
        label_col: session.label_col,
        format_preset: session.format_preset,
        agent_key_col: session.agent_key_col || undefined,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Gagal memuat dataset");

    datasetLoaded = true;
    currentFormatPreset = session.format_preset || "";
    renderDataset(payload.dataset);
    renderRow(payload.row);
    elements.message.textContent = `Session dilanjutkan dari baris ${payload.row.row_number}.`;
  } catch (error) {
    elements.message.textContent = `Gagal melanjutkan session: ${error.message}. Silakan pilih dataset manual.`;
  } finally {
    setBusy(false);
  }
}

async function migrateAll() {
  const btn = document.getElementById("migrateBtn");
  const status = document.getElementById("migrateStatus");
  const panel = document.getElementById("migrateResultPanel");
  const content = document.getElementById("migrateResultContent");

  btn.disabled = true;
  status.textContent = "Memindai file progress lama...";
  panel.hidden = true;

  try {
    const res = await fetch("/api/migrate/all", { method: "POST" });
    const data = await res.json();

    const lines = [];

    if (data.message) {
      lines.push(data.message);
      if (data.expected_dir) lines.push(`\nLetakkan file .json/.txt di:\n  ${data.expected_dir}`);
    }

    if (data.migrated && data.migrated.length > 0) {
      lines.push(`\n✓ Berhasil dimigrasikan (${data.migrated.length}):`);
      data.migrated.forEach((m) => {
        lines.push(`  • ${m.file} → ${m.dataset} (${m.annotations} anotasi, posisi ${m.current_index})`);
      });
    }

    if (data.skipped && data.skipped.length > 0) {
      lines.push(`\n↷ Dilewati (${data.skipped.length}):`);
      data.skipped.forEach((s) => lines.push(`  • ${s.file}: ${s.reason}`));
    }

    if (data.failed && data.failed.length > 0) {
      lines.push(`\n✗ Gagal (${data.failed.length}):`);
      data.failed.forEach((f) => {
        lines.push(`  • ${f.file}: ${f.reason}`);
        if (f.expected_file) lines.push(`    → Letakkan file CSV di: ${f.expected_file}`);
        if (f.expected_dir) lines.push(`    → Letakkan file CSV di folder: ${f.expected_dir}`);
      });
    }

    content.textContent = lines.join("\n").trim();
    document.getElementById("migrateResultTitle").textContent =
      data.migrated && data.migrated.length > 0
        ? `Migrasi selesai — ${data.migrated.length} dataset dipindahkan`
        : "Hasil Migrasi";
    panel.hidden = false;

    const total = (data.migrated || []).length;
    const failed = (data.failed || []).length;
    status.textContent = total > 0
      ? `${total} dataset dimigrasikan${failed > 0 ? `, ${failed} gagal` : ""}.`
      : failed > 0
        ? `Migrasi gagal untuk ${failed} file.`
        : "Tidak ada file untuk dimigrasikan.";
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("migrateBtn").addEventListener("click", migrateAll);
document.getElementById("closeMigrateResult").addEventListener("click", () => {
  document.getElementById("migrateResultPanel").hidden = true;
});

loadFormatPresets()
  .then(() => loadDatasets())
  .then(() => loadFinetuneMeta())
  .then(async () => {
    // If the server already has a dataset loaded (server still running after browser refresh),
    // continue from the current position directly.
    if (window.APP_CONFIG?.dataset?.path) {
      datasetLoaded = true;
      return loadRow(currentIndex);
    }
    // Server has no dataset — check SQLite for the most recent session and offer to resume.
    try {
      const res = await fetch("/api/sessions/last");
      const payload = await res.json();
      if (payload.session && payload.session.dataset_path) {
        showResumeSessionBanner(payload.session);
      } else {
        elements.message.textContent = "Pilih format lalu upload file CSV, atau pilih dari dropdown Dataset.";
      }
    } catch (_) {
      elements.message.textContent = "Pilih format lalu upload file CSV, atau pilih dari dropdown Dataset.";
    }
  })
  .catch((error) => {
    elements.message.textContent = `Gagal memuat konfigurasi: ${error.message}`;
  });

