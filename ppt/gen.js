const pptxgen = require("pptxgenjs");
const p = new pptxgen();
p.layout = "LAYOUT_WIDE"; // 13.333 x 7.5 in
p.author = "ResearchAdvisorLM";
p.title = "ResearchAdvisorLM - Project Review";

// ---- palette ----
const NAVY = "0E2338", NAVY2 = "15304A";
const TEAL = "1C7293", SEA = "138A7C", ICE = "CFE3EE";
const GOLD = "E7B24B";
const WHITE = "FFFFFF", TEXT = "16273A", MUTED = "5C6B7A";
const CARD = "F2F6F9", CARD2 = "E7EFF4", LINE = "D3DEE6";
const RED = "CF4B45", AMBER = "DE8E1C", GREEN = "2E8B6F";
const TF = "Cambria", BF = "Calibri";

const sh = () => ({ type: "outer", color: "0E2338", blur: 7, offset: 3, angle: 90, opacity: 0.16 });

function bg(s, c) { s.background = { color: c }; }

function title(s, t, col) {
  s.addText(t, { x: 0.6, y: 0.35, w: 12.1, h: 0.85, fontFace: TF, fontSize: 34, bold: true, color: col || TEXT, align: "left", valign: "middle", margin: 0 });
}

function kicker(s, t, col) {
  s.addText(t.toUpperCase(), { x: 0.62, y: 0.18, w: 12, h: 0.3, fontFace: BF, fontSize: 12, bold: true, color: col || TEAL, charSpacing: 2, align: "left", margin: 0 });
}

function roundRect(s, x, y, w, h, fill, opts = {}) {
  s.addShape(p.ShapeType.roundRect, Object.assign({ x, y, w, h, fill: { color: fill }, line: { color: opts.line || fill, width: opts.lw || 1 }, rectRadius: opts.r == null ? 0.09 : opts.r, shadow: opts.shadow ? sh() : undefined }, {}));
}

function ell(s, x, y, d, fill) {
  s.addShape(p.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill, width: 1 } });
}

function arrow(s, x1, y1, x2, y2, col, dbl) {
  const o = { x: x1, y: y1, w: x2 - x1, h: y2 - y1, line: { color: col || TEAL, width: 1.75, endArrowType: "triangle" } };
  if (dbl) o.line.beginArrowType = "triangle";
  s.addShape(p.ShapeType.line, o);
}

function bullets(s, items, x, y, w, h, opts = {}) {
  const arr = items.map((it, i) => ({ text: it, options: { bullet: { code: "2022", indent: 14 }, color: opts.color || TEXT, fontFace: BF, fontSize: opts.size || 15, paraSpaceAfter: opts.gap == null ? 10 : opts.gap, breakLine: i !== items.length - 1 } }));
  s.addText(arr, { x, y, w, h, valign: "top", align: "left", margin: 0, lineSpacingMultiple: 1.05 });
}

// icon-circle with number/letter
function badge(s, x, y, d, fill, label, lc) {
  ell(s, x, y, d, fill);
  s.addText(label, { x, y: y - 0.01, w: d, h: d, align: "center", valign: "middle", fontFace: TF, bold: true, fontSize: 15, color: lc || WHITE, margin: 0 });
}

// content card with title + body
function infoCard(s, x, y, w, h, head, body, accent) {
  roundRect(s, x, y, w, h, WHITE, { line: LINE, lw: 1, shadow: true, r: 0.08 });
  ell(s, x + 0.22, y + 0.24, 0.34, accent);
  s.addText(head, { x: x + 0.7, y: y + 0.18, w: w - 0.9, h: 0.45, fontFace: BF, bold: true, fontSize: 15.5, color: TEXT, align: "left", valign: "middle", margin: 0 });
  s.addText(body, { x: x + 0.24, y: y + 0.66, w: w - 0.48, h: h - 0.82, fontFace: BF, fontSize: 12.5, color: MUTED, align: "left", valign: "top", margin: 0, lineSpacingMultiple: 1.03 });
}

function foot(s, dark) {
  s.addText("ResearchAdvisorLM", { x: 0.6, y: 7.06, w: 6, h: 0.3, fontFace: BF, fontSize: 9, color: dark ? ICE : MUTED, align: "left", margin: 0 });
}

// ============================================================ S1 TITLE
let s = p.addSlide(); bg(s, NAVY);
roundRect(s, 0, 0, 13.333, 7.5, NAVY, { line: NAVY, r: 0 });
// motif: three verdict dots
ell(s, 10.55, 1.0, 0.5, RED); ell(s, 11.25, 1.0, 0.5, AMBER); ell(s, 11.95, 1.0, 0.5, GREEN);
s.addText("ResearchAdvisorLM", { x: 0.85, y: 2.35, w: 11.6, h: 1.1, fontFace: TF, bold: true, fontSize: 54, color: WHITE, align: "left", margin: 0 });
s.addText("AI-powered research paper summarization & literature-grounded gap analysis", { x: 0.9, y: 3.5, w: 11.2, h: 0.6, fontFace: BF, fontSize: 21, color: ICE, align: "left", margin: 0 });
roundRect(s, 0.9, 4.45, 6.6, 0.62, NAVY2, { line: TEAL, lw: 1.25 });
s.addText("Find what the paper didn't do - and whether it's still open.", { x: 1.05, y: 4.45, w: 6.3, h: 0.62, fontFace: BF, italic: true, fontSize: 14, color: GOLD, align: "left", valign: "middle", margin: 0 });
s.addText([{ text: "Project Review", options: { bold: true, color: WHITE, breakLine: true } }, { text: "August 2026", options: { color: ICE } }], { x: 0.9, y: 6.35, w: 6, h: 0.7, fontFace: BF, fontSize: 13, align: "left", margin: 0, lineSpacingMultiple: 1.1 });
s.addNotes("Title slide. ResearchAdvisorLM: an AI web app that reads a research paper, breaks it down, and - uniquely - checks each open research direction against the literature published since.");

// ============================================================ S2 INTRO
s = p.addSlide(); kicker(s, "Overview", TEAL); title(s, "Introduction");
bullets(s, [
  "ResearchAdvisorLM reads a research paper and produces a faithful, structured breakdown.",
  "It surfaces the open research directions - the 'gaps' - that the paper leaves behind.",
  "Uniquely, it checks each gap against literature published since, labelling it open, partial, or addressed with citations.",
  "Delivered as a web app plus a native Android client, all backed by one deployable service."
], 0.65, 1.55, 6.6, 4.6, { size: 16, gap: 16 });
// right visual: paper -> analyse -> grounded
const rx = 8.15;
roundRect(s, rx, 1.5, 4.5, 5.0, CARD, { line: LINE, shadow: true, r: 0.08 });
const steps = [["Paper (PDF / arXiv)", TEAL], ["Structured analysis + gaps", SEA], ["Gaps grounded in literature", GOLD]];
let yy = 1.95;
steps.forEach((st, i) => {
  roundRect(s, rx + 0.35, yy, 3.8, 0.85, WHITE, { line: LINE, lw: 1, shadow: true });
  badge(s, rx + 0.55, yy + 0.235, 0.4, st[1], String(i + 1));
  s.addText(st[0], { x: rx + 1.1, y: yy, w: 3.0, h: 0.85, fontFace: BF, bold: true, fontSize: 14, color: TEXT, valign: "middle", align: "left", margin: 0 });
  if (i < 2) arrow(s, rx + 2.25, yy + 0.9, rx + 2.25, yy + 1.28, TEAL);
  yy += 1.35;
});
roundRect(s, rx + 0.35, 5.85, 3.8, 0.5, NAVY, { line: NAVY });
s.addText("open   /   partial   /   addressed", { x: rx + 0.35, y: 5.85, w: 3.8, h: 0.5, align: "center", valign: "middle", fontFace: BF, bold: true, fontSize: 13, color: WHITE, margin: 0 });
foot(s);
s.addNotes("It's an AI research advisor: not just a summary, but an evidence-backed map of what is still unsolved.");

// ============================================================ S3 OBJECTIVE
s = p.addSlide(); kicker(s, "Goals", TEAL); title(s, "Objective");
const obj = [
  ["Beyond summaries", "Turn one paper into a map of open research opportunities, not just a paraphrase.", TEAL, "1"],
  ["Verify, don't guess", "Check whether each stated / inferred gap is actually still unsolved.", SEA, "2"],
  ["Ground every claim", "Back verdicts with evidence and citations retrieved from real literature.", GOLD, "3"],
  ["Accessible & flexible", "Web + mobile access, with a provider-agnostic AI backend.", NAVY, "4"]
];
let ox = 0.65, oy = 1.6, ow = 5.9, oh = 2.25;
obj.forEach((c, i) => {
  const x = ox + (i % 2) * (ow + 0.35), y = oy + Math.floor(i / 2) * (oh + 0.3);
  roundRect(s, x, y, ow, oh, WHITE, { line: LINE, shadow: true, r: 0.08 });
  badge(s, x + 0.3, y + 0.32, 0.55, c[2], c[3]);
  s.addText(c[0], { x: x + 1.05, y: y + 0.3, w: ow - 1.3, h: 0.6, fontFace: BF, bold: true, fontSize: 19, color: TEXT, valign: "middle", align: "left", margin: 0 });
  s.addText(c[1], { x: x + 0.32, y: y + 1.05, w: ow - 0.6, h: 1.0, fontFace: BF, fontSize: 14, color: MUTED, valign: "top", align: "left", margin: 0, lineSpacingMultiple: 1.05 });
});
foot(s);
s.addNotes("Four objectives: go beyond summaries, verify gaps, ground claims in literature, and stay accessible and provider-agnostic.");

// ============================================================ S4 PROBLEM
s = p.addSlide(); kicker(s, "Motivation", TEAL); title(s, "Problem statement");
roundRect(s, 0.65, 1.6, 5.85, 3.7, CARD, { line: LINE, shadow: true, r: 0.08 });
s.addText("The problem", { x: 0.9, y: 1.8, w: 5.3, h: 0.5, fontFace: BF, bold: true, fontSize: 18, color: TEXT, align: "left", margin: 0 });
bullets(s, [
  "Typical 'gap finders' just paraphrase a paper's Limitations & Future Work.",
  "Every gap they report is one the authors already admitted.",
  "None of them check whether the gap has since been solved."
], 0.9, 2.4, 5.35, 2.7, { size: 15, gap: 14 });
roundRect(s, 6.85, 1.6, 5.8, 3.7, NAVY, { line: NAVY, shadow: true, r: 0.08 });
s.addText("The consequence", { x: 7.1, y: 1.8, w: 5.3, h: 0.5, fontFace: BF, bold: true, fontSize: 18, color: WHITE, align: "left", margin: 0 });
bullets(s, [
  "A gap from a 2017 paper may have been solved by 2019 - yet is still reported as 'open.'",
  "Verdicts become stale and misleading.",
  "Students waste effort on directions that are already closed."
], 7.1, 2.4, 5.3, 2.7, { size: 15, gap: 14, color: ICE });
roundRect(s, 0.65, 5.55, 12.0, 0.85, CARD2, { line: TEAL, lw: 1.25, r: 0.08 });
s.addText("Listing gaps is not verifying gaps.", { x: 0.65, y: 5.55, w: 12.0, h: 0.85, align: "center", valign: "middle", fontFace: TF, italic: true, bold: true, fontSize: 22, color: TEAL, margin: 0 });
foot(s);
s.addNotes("Core problem: existing tools only restate future-work; they can't tell you if a direction is still open. That staleness is exactly what this project fixes.");

// ---- module slide helper ----
function moduleSlide(num, name, tagline, points, visualFn) {
  const sl = p.addSlide(); kicker(sl, "Module " + num, TEAL);
  title(sl, "Module " + num + " - " + name);
  sl.addText(tagline, { x: 0.65, y: 1.2, w: 12, h: 0.4, fontFace: BF, italic: true, fontSize: 14.5, color: TEAL, align: "left", margin: 0 });
  bullets(sl, points, 0.65, 1.75, 7.2, 4.5, { size: 15.5, gap: 15 });
  if (visualFn) visualFn(sl);
  foot(sl);
  return sl;
}

// ============================================================ S5 MODULE 1
moduleSlide("1", "Ingestion & Analysis", "From a raw PDF to a structured, machine-readable understanding of the paper.", [
  "Input: upload a PDF or paste an arXiv / DOI link (with SSRF-safe URL handling).",
  "Extraction: PyMuPDF pulls column-aware text with page markers; scanned PDFs fall back to vision OCR.",
  "Analysis: the LLM returns structured JSON - metadata, four summary depths, method, datasets, metrics, findings.",
  "Gap finding: stated + inferred gaps, each with an evidence quote and a literature search query."
], (sl) => {
  const x = 8.35, w = 4.3; let y = 1.75;
  const st = [["PDF / arXiv input", TEAL], ["Extract text (PyMuPDF)", TEAL], ["Analyse (LLM -> JSON)", SEA], ["Gaps + evidence", GOLD]];
  roundRect(sl, x, 1.55, w, 4.9, CARD, { line: LINE, shadow: true, r: 0.08 });
  y = 1.95;
  st.forEach((t, i) => {
    roundRect(sl, x + 0.3, y, 3.7, 0.78, WHITE, { line: LINE, shadow: true });
    badge(sl, x + 0.5, y + 0.19, 0.4, t[1], String(i + 1));
    sl.addText(t[0], { x: x + 1.05, y, w: 2.9, h: 0.78, fontFace: BF, bold: true, fontSize: 13.5, color: TEXT, valign: "middle", align: "left", margin: 0 });
    if (i < st.length - 1) arrow(sl, x + 2.15, y + 0.82, x + 2.15, y + 1.16, TEAL);
    y += 1.16;
  });
});

// ============================================================ S6 MODULE 2
moduleSlide("2", "Literature Grounding", "The core innovation: check every gap against what was published afterwards.", [
  "For each gap, query OpenAlex (~250M scholarly works).",
  "Retrieve papers citing the source + later topical work; keep only later work; rank; take the top 40.",
  "The LLM judges each gap only against this numbered corpus - verdict, confidence, reasoning, citations.",
  "Honest by design: 'open' means not found in the corpus we checked - and the corpus size is always shown."
], (sl) => {
  const x = 8.35, w = 4.3;
  roundRect(sl, x, 1.55, w, 4.9, CARD, { line: LINE, shadow: true, r: 0.08 });
  sl.addText("Every gap -> one verdict", { x: x + 0.3, y: 1.75, w: w - 0.6, h: 0.4, fontFace: BF, bold: true, fontSize: 14, color: TEXT, align: "center", margin: 0 });
  const v = [["Still open", RED, "nothing found"], ["Partial", AMBER, "adjacent work"], ["Addressed", GREEN, "cited authors"]];
  let y = 2.35;
  v.forEach((it) => {
    roundRect(sl, x + 0.35, y, 3.6, 0.75, it[1], { line: it[1] });
    sl.addText([{ text: it[0], options: { bold: true, fontSize: 15, color: WHITE, breakLine: true } }, { text: it[2], options: { fontSize: 11.5, color: "FFFFFF" } }], { x: x + 0.55, y, w: 3.2, h: 0.75, valign: "middle", align: "left", fontFace: BF, margin: 0 });
    y += 0.9;
  });
  roundRect(sl, x + 0.35, 5.35, 3.6, 0.85, NAVY, { line: NAVY });
  sl.addText([{ text: "40", options: { bold: true, fontSize: 26, color: GOLD, breakLine: false } }, { text: "  papers checked / run", options: { fontSize: 12.5, color: ICE } }], { x: x + 0.55, y: 5.35, w: 3.3, h: 0.85, valign: "middle", align: "left", fontFace: BF, margin: 0 });
});

// ============================================================ S7 MODULE 3
s = p.addSlide(); kicker(s, "Module 3", TEAL); title(s, "Module 3 - Interaction & Output");
s.addText("Everything a user does with an analysed paper - ask, compare, plan, and export.", { x: 0.65, y: 1.2, w: 12, h: 0.4, fontFace: BF, italic: true, fontSize: 14.5, color: TEAL, margin: 0 });
const feats = [
  ["Chat with a paper", "Q&A grounded in the text; answers name their section. Plus library-wide chat.", TEAL],
  ["Compare papers", "2-5 papers side by side - and the gaps none of them address.", SEA],
  ["Topic generator", "Turn one open gap into a concrete, startable project proposal.", GOLD],
  ["Auto literature review", "A 'Related Work' chapter written across several papers.", TEAL],
  ["Citations", "IEEE / APA / MLA / Chicago / BibTeX, generated automatically.", SEA],
  ["Export", "Download the full report as Markdown, PDF, or DOCX.", GOLD]
];
let fx = 0.65, fy = 1.75, fw = 3.82, fh = 2.35;
feats.forEach((c, i) => {
  const x = fx + (i % 3) * (fw + 0.28), y = fy + Math.floor(i / 3) * (fh + 0.28);
  infoCard(s, x, y, fw, fh, c[0], c[1], c[2]);
});
foot(s);
s.addNotes("Six user-facing capabilities built on the same analysis + grounding core.");

// ============================================================ S8 ARCHITECTURE
s = p.addSlide(); kicker(s, "System design", TEAL); title(s, "Architecture");
function node(x, y, w, h, fill, t, sub, tcol, scol) {
  roundRect(s, x, y, w, h, fill, { line: fill, shadow: true, r: 0.08 });
  if (sub) {
    s.addText([{ text: t, options: { bold: true, fontSize: 13.5, color: tcol || WHITE, breakLine: true } }, { text: sub, options: { fontSize: 10.5, color: scol || WHITE } }], { x, y, w, h, align: "center", valign: "middle", fontFace: BF, margin: 0, lineSpacing: 15 });
  } else {
    s.addText(t, { x, y, w, h, align: "center", valign: "middle", fontFace: BF, bold: true, fontSize: 13, color: tcol || WHITE, margin: 0 });
  }
}
// clients
node(5.35, 1.3, 1.25, 0.46, MUTED, "Web app", null, WHITE);
node(6.75, 1.3, 1.25, 0.46, MUTED, "Android", null, WHITE);
arrow(s, 5.975, 1.76, 6.4, 1.95, TEAL); arrow(s, 7.375, 1.76, 6.9, 1.95, TEAL);
// FastAPI
node(5.35, 1.95, 2.65, 0.55, TEAL, "FastAPI service", "API + web UI");
// SQLite right, aligned FastAPI
node(9.55, 1.95, 2.5, 0.55, "6E7C8A", "SQLite", "papers + chat");
arrow(s, 8.0, 2.225, 9.55, 2.225, MUTED, true);
// pipeline
const stages = [["1  Extract PDF", "PyMuPDF + OCR"], ["2  Analyse paper", "summary + gaps"], ["3  Retrieve later work", "citing + topical"], ["4  Ground gaps", "judge vs corpus"]];
let sy = 2.7;
stages.forEach((st, i) => { node(5.35, sy, 2.65, 0.52, SEA, st[0], st[1]); if (i < 3) arrow(s, 6.675, sy + 0.52, 6.675, sy + 0.66, SEA); sy += 0.66; });
arrow(s, 6.675, 2.5, 6.675, 2.7, TEAL);
// OpenRouter tall right (spans Analyse..Ground)
node(9.55, 3.36, 2.5, 1.84, "C96A4B", "OpenRouter", "LLM provider");
arrow(s, 8.0, 3.62, 9.55, 3.8, "C96A4B"); // analyse -> LLM
arrow(s, 8.0, 4.94, 9.55, 4.76, "C96A4B"); // ground -> LLM
// OpenAlex left aligned retrieve
node(1.25, 4.02, 2.4, 0.52, "3A7CA5", "OpenAlex", "~250M papers");
arrow(s, 5.35, 4.28, 3.65, 4.28, "3A7CA5", true);
// verdicts
node(3.35, 5.55, 1.95, 0.58, RED, "Still open", "not found");
node(5.5, 5.55, 1.75, 0.58, AMBER, "Partial", "adjacent");
node(7.45, 5.55, 2.3, 0.58, GREEN, "Addressed", "with citations");
arrow(s, 6.675, 5.2, 4.325, 5.55, MUTED);
arrow(s, 6.675, 5.2, 6.375, 5.55, MUTED);
arrow(s, 6.675, 5.2, 8.6, 5.55, MUTED);
// legend note
s.addText("Teal = pipeline stages   /   coral = external services   /   red / amber / green = gap verdicts", { x: 0.65, y: 6.45, w: 12, h: 0.35, fontFace: BF, fontSize: 11.5, color: MUTED, align: "center", margin: 0 });
foot(s);
s.addNotes("One FastAPI service. Four-stage pipeline. OpenRouter provides the LLM for Analyse and Ground; OpenAlex feeds Retrieve; SQLite stores results; every gap exits as open, partial, or addressed.");

// ---- tools slide helper ----
function toolsSlide(part, heading, rows, note) {
  const sl = p.addSlide(); kicker(sl, "Tools - " + part + " of 3", TEAL); title(sl, heading);
  let y = 1.6; const h = note ? 1.02 : 1.15;
  rows.forEach((r) => {
    roundRect(sl, 0.65, y, 12.0, h, WHITE, { line: LINE, shadow: true, r: 0.08 });
    roundRect(sl, 0.65, y, 2.75, h, r[2], { line: r[2], r: 0.08 });
    sl.addText(r[0], { x: 0.75, y, w: 2.55, h, align: "center", valign: "middle", fontFace: TF, bold: true, fontSize: 17, color: WHITE, margin: 0 });
    sl.addText(r[1], { x: 3.65, y: y + 0.05, w: 8.75, h: h - 0.1, align: "left", valign: "middle", fontFace: BF, fontSize: 14, color: TEXT, margin: 0, lineSpacingMultiple: 1.03 });
    y += h + 0.22;
  });
  if (note) {
    roundRect(sl, 0.65, y + 0.02, 12.0, 0.72, NAVY, { line: NAVY, r: 0.08 });
    sl.addText(note, { x: 0.9, y: y + 0.02, w: 11.5, h: 0.72, align: "left", valign: "middle", fontFace: BF, italic: true, fontSize: 13.5, color: ICE, margin: 0 });
  }
  foot(sl);
  return sl;
}

// ============================================================ S9 TOOLS 1
toolsSlide("1", "Tools & technologies - backend & AI", [
  ["FastAPI", "Async Python web framework. One service serves both the JSON API and the web UI; interactive docs at /docs.", TEAL],
  ["LLM layer", "OpenAI-compatible client - swappable across OpenRouter (default), Groq, Mistral, Cerebras or local via 3 env vars. (Originally Google Gemini's 1M-token model.)", SEA],
  ["httpx", "Async HTTP client used for every call to the LLM provider and to OpenAlex.", "C96A4B"],
  ["Pydantic", "Typed request / response models - validation and safety at the API boundary.", NAVY]
]);

// ============================================================ S10 TOOLS 2
toolsSlide("2", "Tools & technologies - data & documents", [
  ["PyMuPDF", "Fast, column-aware PDF text extraction with page markers; also renders pages to images for OCR.", TEAL],
  ["OpenAlex", "Free, no-key scholarly database of ~250M works - the engine behind literature grounding.", SEA],
  ["SQLite", "Zero-config storage for papers and chat history (WAL mode). Swap for Postgres to scale.", "C96A4B"],
  ["Vision OCR", "A vision model transcribes scanned / image-only PDFs - no Tesseract binary needed.", NAVY]
]);

// ============================================================ S11 TOOLS 3
toolsSlide("3", "Tools & technologies - frontend, mobile & DevOps", [
  ["Vanilla JS", "Web UI in plain JS + CSS - no build step; served by the same FastAPI service.", TEAL],
  ["Kotlin + Compose", "Native Android client (OkHttp + kotlinx.serialization). The phone never holds the API key.", SEA],
  ["Railway", "Git-push deploy with health checks, a generated domain, and native auto-deploy on push.", "C96A4B"],
  ["GitHub Actions", "CI builds the Android APK on every push to main.", NAVY]
], "Deliberately avoided: FAISS / vector DB / LangChain / chunking - a whole paper fits in context, so chunking would only lose cross-section meaning.");

// ============================================================ S12 WORKFLOW
s = p.addSlide(); kicker(s, "How it works", TEAL); title(s, "End-to-end workflow");
const wf = [["1", "Ingest & extract", "PDF or link -> clean text", TEAL], ["2", "Analyse", "structured JSON + gaps", SEA], ["3", "Retrieve", "later work from OpenAlex", "C96A4B"], ["4", "Ground", "judge gaps vs corpus", GOLD]];
let wx = 0.75, wy = 2.15, ww = 2.72, wh = 2.2;
wf.forEach((c, i) => {
  const x = wx + i * (ww + 0.28);
  roundRect(s, x, wy, ww, wh, WHITE, { line: LINE, shadow: true, r: 0.08 });
  badge(s, x + ww / 2 - 0.35, wy + 0.3, 0.7, c[3], c[0]);
  s.addText(c[1], { x: x + 0.1, y: wy + 1.15, w: ww - 0.2, h: 0.45, align: "center", fontFace: BF, bold: true, fontSize: 16, color: TEXT, margin: 0 });
  s.addText(c[2], { x: x + 0.12, y: wy + 1.6, w: ww - 0.24, h: 0.5, align: "center", fontFace: BF, fontSize: 12.5, color: MUTED, margin: 0, lineSpacingMultiple: 1.0 });
  if (i < 3) arrow(s, x + ww, wy + wh / 2, x + ww + 0.28, wy + wh / 2, MUTED);
});
// outcome row
roundRect(s, 0.75, 5.05, 11.83, 0.95, CARD, { line: LINE, shadow: true, r: 0.08 });
s.addText("Output:", { x: 1.0, y: 5.05, w: 1.4, h: 0.95, valign: "middle", fontFace: BF, bold: true, fontSize: 15, color: TEXT, margin: 0 });
[["Still open", RED], ["Partial", AMBER], ["Addressed", GREEN]].forEach((v, i) => {
  const x = 2.5 + i * 3.35;
  roundRect(s, x, 5.28, 3.0, 0.5, v[1], { line: v[1] });
  s.addText(v[0] + "  -  with evidence & citations", { x, y: 5.28, w: 3.0, h: 0.5, align: "center", valign: "middle", fontFace: BF, bold: true, fontSize: 11.5, color: WHITE, margin: 0 });
});
foot(s);
s.addNotes("Two LLM calls (Analyse, Ground) bracket one retrieval step. Each gap ends as an evidence-backed verdict.");

// ============================================================ S13 DESIGN DECISIONS
s = p.addSlide(); kicker(s, "Engineering", TEAL); title(s, "Key design decisions");
const dd = [
  ["Whole paper in context", "No RAG or chunking over the document - a 15-page paper fits whole. Retrieval is reserved for the external literature, where it earns its place."],
  ["Evidence-first gaps", "Every gap ships with the exact quote from the paper that proves it exists - a human can verify in seconds."],
  ["Honest grounding", "Verdicts are judged against a bounded, transparent corpus. 'Open' = not found in what we checked, never 'provably novel.'"],
  ["Provider-agnostic AI", "An OpenAI-compatible layer means the model provider is swappable via environment variables - no lock-in."],
  ["Robust, safe I/O", "SSRF-safe URL imports, chunked upload limits, and defensive JSON parsing with a corrective retry."]
];
let dy = 1.6;
dd.forEach((c, i) => {
  const h = i === 0 ? 1.12 : 0.92;
  roundRect(s, 0.65, dy, 12.0, h, WHITE, { line: LINE, shadow: true, r: 0.08 });
  ell(s, 0.95, dy + h / 2 - 0.19, 0.38, [TEAL, SEA, GOLD, "C96A4B", NAVY][i]);
  s.addText(String(i + 1), { x: 0.95, y: dy + h / 2 - 0.19, w: 0.38, h: 0.38, align: "center", valign: "middle", fontFace: TF, bold: true, fontSize: 14, color: WHITE, margin: 0 });
  s.addText([{ text: c[0] + "  ", options: { bold: true, fontSize: 15, color: TEXT } }, { text: "- " + c[1], options: { fontSize: 13, color: MUTED } }], { x: 1.55, y: dy + 0.05, w: 10.9, h: h - 0.1, valign: "middle", align: "left", fontFace: BF, margin: 0, lineSpacingMultiple: 1.02 });
  dy += h + 0.15;
});
foot(s);
s.addNotes("These are the strongest talking points for Q&A: why no vector DB, why evidence per gap, why bounded-corpus honesty, provider-agnostic design, and safe I/O.");

// ============================================================ S14 EVALUATION
s = p.addSlide(); kicker(s, "Results", TEAL); title(s, "Evaluation, results & limitations");
// left results
roundRect(s, 0.65, 1.6, 6.0, 4.7, CARD, { line: LINE, shadow: true, r: 0.08 });
roundRect(s, 0.95, 1.9, 5.4, 1.5, NAVY, { line: NAVY, r: 0.08 });
s.addText([{ text: "~40%", options: { bold: true, fontSize: 46, color: GOLD, breakLine: true } }, { text: "of open-looking gaps overturned by grounding", options: { fontSize: 13, color: ICE } }], { x: 1.15, y: 1.95, w: 5.0, h: 1.4, valign: "middle", align: "left", fontFace: BF, margin: 0, lineSpacing: 30 });
bullets(s, [
  "Extraction accuracy: 100% on year, dataset and method recall (eval set).",
  "Summary faithfulness: no invented numbers detected.",
  "Integrity: no pre-dating citations, no contradictory verdicts.",
  "~40 papers checked per gap; ~50-70s per analysis."
], 0.95, 3.65, 5.4, 2.5, { size: 13.5, gap: 11 });
// right limitations
roundRect(s, 6.9, 1.6, 5.75, 4.7, WHITE, { line: LINE, shadow: true, r: 0.08 });
s.addText("Known limitations", { x: 7.15, y: 1.8, w: 5.3, h: 0.5, fontFace: BF, bold: true, fontSize: 17, color: TEXT, margin: 0 });
bullets(s, [
  "Bounded corpus - judged against ~40 papers, not all of science.",
  "Text-only - some figures, tables and equations are missed.",
  "OCR is vision-based and approximate, not exact.",
  "OpenAlex coverage - very new preprints may be missing.",
  "The LLM can misread - mitigated: every gap carries its source evidence."
], 7.15, 2.4, 5.3, 3.8, { size: 14, gap: 13, color: TEXT });
foot(s);
s.addNotes("Headline: grounding overturned ~40% of gaps a plain summarizer would call open. That number is the quantified value of the whole project. State the limitations plainly.");

// ============================================================ S15 CONCLUSION
s = p.addSlide(); bg(s, NAVY);
roundRect(s, 0, 0, 13.333, 7.5, NAVY, { line: NAVY, r: 0 });
s.addText("Conclusion", { x: 0.85, y: 0.7, w: 11, h: 0.9, fontFace: TF, bold: true, fontSize: 40, color: WHITE, margin: 0 });
bullets(s, [
  "ResearchAdvisorLM turns a single paper into an evidence-backed map of open research - not just a summary.",
  "Its novel contribution is a time-aware literature-grounding loop that overturned ~40% of gaps a summarizer would call open.",
  "Delivered end to end: a deployed web app and a native Android APK, on a provider-agnostic, honest, checkable stack."
], 0.9, 1.85, 11.5, 2.5, { size: 17, gap: 16, color: ICE });
roundRect(s, 0.9, 4.5, 11.5, 1.75, NAVY2, { line: TEAL, lw: 1.25, r: 0.08 });
s.addText("Future work", { x: 1.15, y: 4.62, w: 10, h: 0.4, fontFace: BF, bold: true, fontSize: 15, color: GOLD, margin: 0 });
bullets(s, [
  "Postgres + user accounts for persistence and multi-user access.",
  "Larger 15-20 paper benchmark; per-claim page anchors in summaries.",
  "Improved OCR for scanned papers."
], 1.15, 5.05, 11.0, 1.15, { size: 13.5, gap: 7, color: ICE });
s.addText("Thank you.", { x: 0.9, y: 6.5, w: 11.5, h: 0.5, fontFace: TF, italic: true, bold: true, fontSize: 20, color: WHITE, margin: 0 });
s.addNotes("Close on the headline number and the fact that it's fully deployed. Invite questions.");

p.writeFile({ fileName: "ResearchAdvisorLM_Presentation.pptx" }).then(f => console.log("WROTE", f)).catch(e => { console.error(e); process.exit(1); });
