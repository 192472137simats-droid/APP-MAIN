const $ = (selector) => document.querySelector(selector);
const MAX_PDF_BYTES = 25 * 1024 * 1024;

const state = {
  papers: [],
  selectedId: null,
  selectedPaper: null,
  uploadFile: null,
  uploading: false,
  analysisConfigured: false,
  model: "",
  pollTimer: null,
};

function showStatus(message, isError = false) {
  const status = $("#upload-status");
  status.textContent = message;
  status.className = isError ? "status error" : "status";
  status.hidden = !message;
}

async function requestJson(path, options) {
  const response = await fetch(path, options);
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(`Server returned an unreadable response (${response.status}).`);
  }
  if (!response.ok) {
    throw new Error(body.detail || `Request failed (${response.status}).`);
  }
  return body;
}

function renderLibrary() {
  const list = $("#library-list");
  list.replaceChildren();
  $("#paper-count").textContent = state.papers.length;

  if (!state.papers.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No papers yet. Upload a text-layer PDF to start your library.";
    list.append(empty);
    return;
  }

  for (const paper of state.papers) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "paper-card";
    card.setAttribute("aria-pressed", String(paper.id === state.selectedId));

    const title = document.createElement("span");
    title.className = "paper-card-title";
    title.textContent = paper.title || paper.filename;

    const meta = document.createElement("span");
    meta.className = "paper-card-meta";
    meta.textContent = `${paper.pages} ${paper.pages === 1 ? "page" : "pages"} · ${Number(paper.characters).toLocaleString()} characters`;

    card.append(title, meta);
    card.addEventListener("click", () => selectPaper(paper.id));
    list.append(card);
  }
}

function showDetailPlaceholder(message) {
  $("#detail-empty").hidden = false;
  $("#detail-content").hidden = true;
  $("#detail-empty h3").textContent = message;
}

function addAnalysisBlock(container, title, content) {
  if (!content) return;
  const section = document.createElement("section");
  section.className = "analysis-block";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const paragraph = document.createElement("p");
  paragraph.textContent = content;
  section.append(heading, paragraph);
  container.append(section);
}

function addAnalysisList(container, title, values) {
  if (!Array.isArray(values) || !values.length) return;
  const section = document.createElement("section");
  section.className = "analysis-block";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const list = document.createElement("ul");
  for (const value of values) {
    const item = document.createElement("li");
    item.textContent = value;
    list.append(item);
  }
  section.append(heading, list);
  container.append(section);
}

function renderAnalysis(paper) {
  const button = $("#analyze-button");
  const message = $("#analysis-message");
  const content = $("#analysis-content");
  const status = paper.analysis_status || "extracted";
  content.replaceChildren();
  message.className = "analysis-message";
  button.hidden = status === "pending" || status === "done" || !state.analysisConfigured;
  button.disabled = false;
  button.textContent = status === "error" ? "Retry analysis" : "Analyze paper";

  if (status === "pending") {
    message.textContent = "The model is reviewing this paper. You can leave this page and return later.";
    return;
  }
  if (status === "error") {
    message.classList.add("error");
    message.textContent = paper.analysis_error || "Analysis failed. You can retry it.";
    return;
  }
  if (status !== "done" || !paper.analysis) {
    message.textContent = state.analysisConfigured
      ? `Ready to analyze with ${state.model}. This sends extracted text to your model provider; charges may apply.`
      : "Set LLM_API_KEY and LLM_MODEL in the server environment or local .env to enable analysis.";
    return;
  }

  const analysis = paper.analysis;
  message.textContent = "AI-generated analysis. Check every claim against the paper text below.";
  addAnalysisBlock(content, "Short summary", analysis.summary?.short);
  addAnalysisBlock(content, "Detailed summary", analysis.summary?.detailed);
  addAnalysisBlock(content, "Objective", analysis.objective);
  addAnalysisBlock(content, "Method", analysis.method);
  addAnalysisList(content, "Contributions", analysis.contributions);
  addAnalysisList(content, "Findings", analysis.findings);
  addAnalysisList(content, "Stated limitations", analysis.stated_limitations);
  addAnalysisList(content, "Stated future work", analysis.stated_future_work);

  const gaps = Array.isArray(analysis.gaps) ? analysis.gaps : [];
  const gapSection = document.createElement("section");
  gapSection.className = "analysis-block";
  const gapHeading = document.createElement("h4");
  gapHeading.textContent = `Research directions (${gaps.length})`;
  gapSection.append(gapHeading);
  if (!gaps.length) {
    const empty = document.createElement("p");
    empty.textContent = "No evidence-backed directions were identified in this analysis.";
    gapSection.append(empty);
  }
  for (const gap of gaps) {
    const item = document.createElement("div");
    item.className = "gap-item";
    const title = document.createElement("strong");
    title.textContent = gap.title || "Untitled direction";
    const meta = document.createElement("span");
    meta.textContent = `${gap.origin || "inferred"} · ${gap.category || "general"}`;
    const description = document.createElement("p");
    description.textContent = gap.description || "";
    const evidence = document.createElement("p");
    evidence.textContent = `Paper evidence: ${gap.evidence_from_paper || "Not supplied"}`;
    item.append(title, meta, description, evidence);
    gapSection.append(item);
  }
  content.append(gapSection);
}

function scheduleAnalysisPoll(id) {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(async () => {
    if (state.selectedId !== id) return;
    try {
      const paper = await requestJson(`/api/papers/${encodeURIComponent(id)}?include_text=true`);
      if (state.selectedId !== id) return;
      state.selectedPaper = paper;
      renderDetail(paper);
      if (paper.analysis_status === "pending") scheduleAnalysisPoll(id);
    } catch (error) {
      $("#analysis-message").textContent = `Could not check analysis status: ${error.message}`;
      scheduleAnalysisPoll(id);
    }
  }, 2000);
}

function renderDetail(paper) {
  $("#detail-empty").hidden = true;
  $("#detail-content").hidden = false;
  $("#paper-title").textContent = paper.title || paper.filename;
  $("#paper-meta").textContent = `${paper.filename} · ${paper.pages} ${paper.pages === 1 ? "page" : "pages"} · ${Number(paper.characters).toLocaleString()} characters`;
  $("#paper-text").textContent = paper.full_text;
  renderAnalysis(paper);
}

async function selectPaper(id) {
  clearTimeout(state.pollTimer);
  state.selectedId = id;
  state.selectedPaper = null;
  renderLibrary();
  showDetailPlaceholder("Loading paper text…");
  try {
    const paper = await requestJson(`/api/papers/${encodeURIComponent(id)}?include_text=true`);
    if (state.selectedId !== id) return;
    state.selectedPaper = paper;
    renderDetail(paper);
    if (paper.analysis_status === "pending") scheduleAnalysisPoll(id);
  } catch (error) {
    if (state.selectedId === id) showDetailPlaceholder(error.message);
  }
}

async function loadPapers(preferredId = state.selectedId) {
  state.papers = await requestJson("/api/papers");
  const selected = state.papers.find((paper) => paper.id === preferredId) || state.papers[0];
  if (selected) {
    await selectPaper(selected.id);
  } else {
    state.selectedId = null;
    state.selectedPaper = null;
    renderLibrary();
    showDetailPlaceholder("Select a paper to read its text");
  }
}

function setUploadFile(file) {
  state.uploadFile = file || null;
  $("#selected-file").textContent = file ? file.name : "Text-layer PDF, up to 25 MB";
  $("#upload-button").disabled = !file || state.uploading;
  showStatus("");
}

$("#pdf-file").addEventListener("change", (event) => setUploadFile(event.target.files[0]));

const dropzone = $("#dropzone");
for (const eventName of ["dragenter", "dragover"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("drag-over");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove("drag-over");
  });
}
dropzone.addEventListener("drop", (event) => setUploadFile(event.dataTransfer.files[0]));

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = state.uploadFile;
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showStatus("Choose a PDF file.", true);
    return;
  }
  if (file.size > MAX_PDF_BYTES) {
    showStatus("PDF exceeds the 25 MB limit.", true);
    return;
  }

  state.uploading = true;
  $("#upload-button").disabled = true;
  showStatus("Extracting text and saving your paper…");
  try {
    const form = new FormData();
    form.append("file", file);
    const uploaded = await requestJson("/api/upload", { method: "POST", body: form });
    await loadPapers(uploaded.paper_id);
    $("#upload-form").reset();
    setUploadFile(null);
    showStatus(`Saved ${uploaded.filename}: ${uploaded.pages} ${uploaded.pages === 1 ? "page" : "pages"} extracted.`);
  } catch (error) {
    showStatus(error.message, true);
  } finally {
    state.uploading = false;
    $("#upload-button").disabled = !state.uploadFile;
  }
});

$("#refresh-button").addEventListener("click", async () => {
  try {
    await loadPapers();
  } catch (error) {
    showStatus(error.message, true);
  }
});

$("#analyze-button").addEventListener("click", async () => {
  const paper = state.selectedPaper;
  if (!paper) return;
  const button = $("#analyze-button");
  button.disabled = true;
  try {
    await requestJson(`/api/papers/${encodeURIComponent(paper.id)}/analyze`, {
      method: "POST",
    });
    if (state.selectedId !== paper.id) return;
    paper.analysis_status = "pending";
    paper.analysis = null;
    renderAnalysis(paper);
    scheduleAnalysisPoll(paper.id);
  } catch (error) {
    if (state.selectedId !== paper.id) return;
    renderAnalysis(paper);
    $("#analysis-message").textContent = error.message;
    $("#analysis-message").classList.add("error");
  }
});

$("#download-button").addEventListener("click", () => {
  const paper = state.selectedPaper;
  if (!paper) return;
  const blob = new Blob([paper.full_text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${paper.filename.replace(/\.pdf$/i, "")}.txt`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

async function start() {
  const health = await requestJson("/api/health");
  state.analysisConfigured = Boolean(health.analysis_configured);
  state.model = health.model || "the configured model";
  await loadPapers();
}

start().catch((error) => {
  $("#library-list").textContent = "Could not load papers.";
  showStatus(error.message, true);
});
