/* ResearchAdvisorLM — web client */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  paper: null,       // full paper object currently open
  depth: "short",
  compareIds: new Set(),
};

/* ---------------- helpers ---------------- */

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

function toast(message, ms = 2600) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), ms);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function show(view) {
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  $$(".navbtn").forEach((b) => b.classList.toggle("active", b.dataset.nav === view));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setStep(name, cls) {
  const step = $(`.step[data-step="${name}"]`);
  if (step) step.className = `step ${cls}`;
}

function resetSteps() {
  ["extract", "analyze", "search", "validate"].forEach((s) => setStep(s, ""));
}

/* ---------------- navigation ---------------- */

$$("[data-nav]").forEach((el) =>
  el.addEventListener("click", () => {
    const view = el.dataset.nav;
    if (view === "library") loadLibrary();
    if (view === "compare") loadComparePicker();
    show(view);
  })
);

$$(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    $$(".tabpanel").forEach((p) => p.classList.add("hidden"));
    $(`#tab-${tab.dataset.tab}`).classList.remove("hidden");
  })
);

/* ---------------- upload ---------------- */

const dropzone = $("#dropzone");
const fileInput = $("#file-input");

$("#browse-btn").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) handleUpload(fileInput.files[0]);
});

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("drag");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag");
  })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) handleUpload(file);
});

async function handleUpload(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    return showUploadError("Only PDF files are supported.");
  }

  $("#upload-error").classList.add("hidden");
  $("#progress").classList.remove("hidden");
  resetSteps();
  setStep("extract", "active");
  dropzone.style.opacity = ".45";

  const form = new FormData();
  form.append("file", file);

  try {
    setStep("extract", "done");
    setStep("analyze", "active");
    const result = await api("/api/upload", { method: "POST", body: form });
    setStep("analyze", "done");

    // Open the paper immediately — grounding continues in the background.
    await openPaper(result.paper_id, result.warnings);

    setStep("search", "active");
    try {
      const grounding = await api(`/api/papers/${result.paper_id}/ground`, { method: "POST" });
      setStep("search", "done");
      setStep("validate", "done");
      state.paper.grounding = grounding;
      renderGaps();
      toast(`Checked ${grounding.corpus_size} papers from the literature`);
    } catch (err) {
      setStep("search", "");
      toast(`Gap grounding failed: ${err.message}`);
    }
  } catch (err) {
    showUploadError(err.message);
  } finally {
    dropzone.style.opacity = "1";
    fileInput.value = "";
    setTimeout(() => $("#progress").classList.add("hidden"), 1200);
  }
}

function showUploadError(message) {
  const el = $("#upload-error");
  el.textContent = message;
  el.classList.remove("hidden");
  $("#progress").classList.add("hidden");
  dropzone.style.opacity = "1";
}

/* ---------------- paper view ---------------- */

async function openPaper(paperId, warnings = []) {
  state.paper = await api(`/api/papers/${paperId}`);
  state.paper.warnings = warnings;
  renderPaper();
  show("paper");
}

function renderPaper() {
  const paper = state.paper;
  const analysis = paper.analysis || {};
  const meta = analysis.metadata || {};

  $("#paper-title").textContent = meta.title || paper.filename;

  const chips = [
    (meta.authors || []).slice(0, 3).join(", ") + ((meta.authors || []).length > 3 ? " et al." : ""),
    meta.year,
    meta.venue,
    meta.domain,
    `${paper.pages} pages`,
  ].filter(Boolean);
  $("#paper-meta").innerHTML = chips.map((c) => `<span class="chip">${esc(c)}</span>`).join("");

  $("#paper-warnings").innerHTML = (paper.warnings || [])
    .map((w) => `<div class="alert warn">${esc(w)}</div>`)
    .join("");

  renderSummary();
  renderGaps();
  renderChat();
}

function renderSummary() {
  const analysis = state.paper.analysis || {};
  const summary = analysis.summary || {};
  const list = (items) =>
    (items || []).length
      ? `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`
      : `<p class="quote">Not stated in this paper.</p>`;

  const depths = [
    ["one_line", "One line"],
    ["short", "Short"],
    ["detailed", "Detailed"],
    ["eli5", "Explain simply"],
  ];

  $("#tab-summary").innerHTML = `
    <div class="block">
      <h3>Summary</h3>
      <div class="depth">
        ${depths
          .map(
            ([key, label]) =>
              `<button data-depth="${key}" class="${state.depth === key ? "active" : ""}">${label}</button>`
          )
          .join("")}
      </div>
      <p id="summary-text">${esc(summary[state.depth] || summary.short || "")}</p>
    </div>

    <div class="block"><h3>Objective</h3><p>${esc(analysis.objective || "Not stated.")}</p></div>
    <div class="block"><h3>Method</h3><p>${esc(analysis.method || "Not stated.")}</p></div>

    <div class="block">
      <h3>Datasets</h3>
      <div class="tag-row">${
        (analysis.datasets || []).length
          ? analysis.datasets.map((d) => `<span class="tag">${esc(d)}</span>`).join("")
          : `<span class="quote">Not stated in this paper.</span>`
      }</div>
    </div>

    <div class="block"><h3>Contributions</h3>${list(analysis.contributions)}</div>
    <div class="block"><h3>Key findings</h3>${list(analysis.findings)}</div>
    <div class="block"><h3>Stated limitations</h3>${list(analysis.stated_limitations)}</div>
    <div class="block"><h3>Conclusion</h3><p>${esc(analysis.conclusion || "Not stated.")}</p></div>

    <div class="block">
      <h3>Keywords</h3>
      <div class="tag-row">${(analysis.metadata?.keywords || [])
        .map((k) => `<span class="tag">${esc(k)}</span>`)
        .join("")}</div>
    </div>
  `;

  $$("#tab-summary .depth button").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.depth = btn.dataset.depth;
      renderSummary();
    })
  );
}

const VERDICT_LABEL = {
  open: "Still open",
  partial: "Partially addressed",
  addressed: "Addressed",
  unverified: "Unverified",
};

function renderGaps() {
  const paper = state.paper;
  const grounding = paper.grounding;
  const gaps = grounding?.gaps || paper.analysis?.gaps || [];

  $("#gap-count").textContent = gaps.length || "";

  if (grounding) {
    const open = gaps.filter((g) => g.verdict === "open").length;
    $("#grounding-banner").innerHTML = `
      <div class="alert info">
        <b>${open} of ${gaps.length}</b> directions still look open.
        Checked against <b>${grounding.corpus_size}</b> papers retrieved from OpenAlex,
        published from ${esc(grounding.paper_year || "n.d.")} onward.
        ${grounding.matched_paper ? "" : " The source paper was not found in OpenAlex, so citing-paper evidence is unavailable."}
        <br /><span class="ev-meta">This is a bounded corpus, not the whole literature — treat verdicts as evidence, not proof.</span>
      </div>`;
  } else {
    $("#grounding-banner").innerHTML = `
      <div class="alert warn">
        These gaps come from the paper alone and have <b>not</b> been checked against later
        literature yet. <button class="btn ghost" id="ground-now">Check literature now</button>
      </div>`;
    $("#ground-now")?.addEventListener("click", groundNow);
  }

  $("#gaps-list").innerHTML =
    gaps
      .map((gap, index) => {
        const verdict = gap.verdict || "unverified";
        const evidence = gap.evidence || [];
        return `
      <div class="gap ${verdict}">
        <div class="gap-top">
          <div class="gap-title">Gap ${index + 1} — ${esc(gap.title)}</div>
          <span class="verdict ${verdict}">${VERDICT_LABEL[verdict] || verdict}</span>
        </div>
        <div class="gap-meta">
          <span class="chip">${esc(gap.category || "general")}</span>
          <span class="chip">${gap.origin === "stated" ? "authors stated it" : "inferred by review"}</span>
          ${gap.confidence ? `<span class="chip">confidence: ${esc(gap.confidence)}</span>` : ""}
        </div>
        <p>${esc(gap.description)}</p>
        <p class="quote">In the paper: ${esc(gap.evidence_from_paper)}</p>
        ${gap.reasoning ? `<p><b>Literature check:</b> ${esc(gap.reasoning)}</p>` : ""}
        ${
          evidence.length
            ? `<details class="evidence">
                 <summary>${evidence.length} paper${evidence.length > 1 ? "s" : ""} supporting this verdict</summary>
                 ${evidence
                   .map(
                     (w) => `
                   <div class="ev-item">
                     <a href="${esc(w.url)}" target="_blank" rel="noopener">${esc(w.title)}</a>
                     <div class="ev-meta">${esc((w.authors || []).join(", "))} · ${esc(w.year || "n.d.")}
                       · ${w.citations} citations${w.venue ? " · " + esc(w.venue) : ""}</div>
                   </div>`
                   )
                   .join("")}
               </details>`
            : ""
        }
        ${gap.what_remains ? `<div class="remains"><b>What a new project could do:</b> ${esc(gap.what_remains)}</div>` : ""}
      </div>`;
      })
      .join("") || `<div class="empty">No gaps identified.</div>`;
}

async function groundNow() {
  const button = $("#ground-now");
  if (button) {
    button.disabled = true;
    button.textContent = "Searching literature…";
  }
  try {
    state.paper.grounding = await api(`/api/papers/${state.paper.id}/ground`, { method: "POST" });
    renderGaps();
    toast("Gap verdicts updated");
  } catch (err) {
    toast(err.message, 4000);
    if (button) {
      button.disabled = false;
      button.textContent = "Check literature now";
    }
  }
}

/* ---------------- chat ---------------- */

const SUGGESTIONS = [
  "Explain this paper in simple terms",
  "What dataset was used?",
  "What are the main results?",
  "What did they NOT test?",
  "How would I extend this?",
];

function renderChat() {
  const log = $("#chat-log");
  const messages = state.paper.messages || [];
  log.innerHTML = messages.length
    ? messages.map((m) => `<div class="msg ${m.role}">${esc(m.content)}</div>`).join("")
    : `<div class="msg assistant">Ask me anything about this paper. I answer from the paper text and name the section I used.</div>`;

  $("#chat-suggestions").innerHTML = SUGGESTIONS.map(
    (s) => `<button type="button">${esc(s)}</button>`
  ).join("");
  $$("#chat-suggestions button").forEach((b) =>
    b.addEventListener("click", () => sendMessage(b.textContent))
  );
}

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("#chat-text").value.trim();
  if (text) sendMessage(text);
});

async function sendMessage(question) {
  const log = $("#chat-log");
  $("#chat-text").value = "";
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(question)}</div>`);
  log.insertAdjacentHTML("beforeend", `<div class="msg assistant thinking" id="thinking">Reading the paper…</div>`);
  log.scrollTop = log.scrollHeight;

  try {
    const result = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_id: state.paper.id, question }),
    });
    $("#thinking").outerHTML = `<div class="msg assistant">${esc(result.answer)}</div>`;
    state.paper.messages = [
      ...(state.paper.messages || []),
      { role: "user", content: question },
      { role: "assistant", content: result.answer },
    ];
  } catch (err) {
    $("#thinking").outerHTML = `<div class="msg assistant">⚠️ ${esc(err.message)}</div>`;
  }
  log.scrollTop = log.scrollHeight;
}

/* ---------------- export / delete ---------------- */

$("#export-btn").addEventListener("click", () => {
  window.open(`/api/papers/${state.paper.id}/export`, "_blank");
});

$("#delete-btn").addEventListener("click", async () => {
  if (!confirm("Delete this paper and its chat history?")) return;
  await api(`/api/papers/${state.paper.id}`, { method: "DELETE" });
  toast("Paper deleted");
  loadLibrary();
  show("library");
});

/* ---------------- library ---------------- */

function paperCard(paper, selectable = false) {
  const gapText = paper.grounded
    ? `<span>${paper.open_gaps} open / ${paper.total_gaps} gaps</span>`
    : `<span>${paper.total_gaps} gaps · not grounded</span>`;
  return `
    <div class="card ${selectable && state.compareIds.has(paper.id) ? "selected" : ""}" data-id="${paper.id}">
      <div class="card-title">${esc(paper.title || paper.filename)}</div>
      <div class="card-meta">
        ${paper.year ? `<span>${esc(paper.year)}</span>` : ""}
        <span>${paper.pages}p</span>
        ${gapText}
      </div>
    </div>`;
}

async function loadLibrary() {
  const papers = await api("/api/papers");
  $("#library-list").innerHTML = papers.length
    ? papers.map((p) => paperCard(p)).join("")
    : `<div class="empty">No papers yet. Upload one to get started.</div>`;
  $$("#library-list .card").forEach((card) =>
    card.addEventListener("click", () => openPaper(card.dataset.id))
  );
}

/* ---------------- compare ---------------- */

async function loadComparePicker() {
  const papers = await api("/api/papers");
  $("#compare-picker").innerHTML = papers.length
    ? papers.map((p) => paperCard(p, true)).join("")
    : `<div class="empty">Upload at least two papers to compare.</div>`;
  $$("#compare-picker .card").forEach((card) =>
    card.addEventListener("click", () => {
      const id = card.dataset.id;
      if (state.compareIds.has(id)) state.compareIds.delete(id);
      else if (state.compareIds.size >= 5) return toast("Maximum of 5 papers");
      else state.compareIds.add(id);
      card.classList.toggle("selected");
    })
  );
}

$("#run-compare").addEventListener("click", async () => {
  if (state.compareIds.size < 2) return toast("Select at least 2 papers");
  const button = $("#run-compare");
  button.disabled = true;
  button.textContent = "Comparing…";
  $("#compare-result").innerHTML = "";

  try {
    const result = await api("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_ids: [...state.compareIds] }),
    });

    const headers = result.papers.map((p) => `<th>${esc(p.title)}</th>`).join("");
    const rows = result.rows
      .map(
        (row) =>
          `<tr><td>${esc(row.aspect)}</td>${row.values
            .map((v) => `<td>${esc(v)}</td>`)
            .join("")}</tr>`
      )
      .join("");

    $("#compare-result").innerHTML = `
      <table class="ctable"><thead><tr><th></th>${headers}</tr></thead><tbody>${rows}</tbody></table>
      <div class="block" style="margin-top:20px">
        <h3>Gaps none of them address</h3>
        <ul>${(result.shared_gaps || []).map((g) => `<li>${esc(g)}</li>`).join("")}</ul>
      </div>
      ${
        (result.contradictions || []).length
          ? `<div class="block"><h3>Contradictions</h3><ul>${result.contradictions
              .map((c) => `<li>${esc(c)}</li>`)
              .join("")}</ul></div>`
          : ""
      }
      <div class="block"><h3>Synthesis</h3><p>${esc(result.synthesis)}</p></div>`;
  } catch (err) {
    $("#compare-result").innerHTML = `<div class="alert error">${esc(err.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = "Compare selected";
  }
});

/* ---------------- boot ---------------- */

(async function boot() {
  try {
    const health = await api("/api/health");
    if (!health.key_configured) {
      showUploadError("No LLM_API_KEY configured on the server. Set it in your .env file or Railway variables.");
    }
    if (health.papers > 0) loadLibrary();
  } catch {
    /* server not reachable yet */
  }
  show("upload");
})();
