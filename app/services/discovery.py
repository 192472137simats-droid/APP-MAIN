"""Research Discovery Mode - an evidence-first, bounded, agentic pipeline.

    topic -> ranked papers        (Stage 1: literature.search_topic)
          -> multi-paper analysis (Stage 2: analyse_papers)
          -> cross-paper synthesis(Stage 3: synthesize_gaps)
          -> adversarial validation (Stage 4: validate_gap)
          -> opportunity ranking  (Stage 5: rank_opportunities)
          -> bounded orchestration (Stage 6: run_discovery)

Design principles (see the product roadmap):
- Reuse the existing engine: analysis.analyse_paper, literature.*, llm_service.*.
- Keep deterministic data-shaping (clustering, scoring) separate from LLM calls.
- Every LLM step is bounded by a hard Budget (call cap + wall-clock deadline) so a
  run can never become expensive or infinite; one failing paper never sinks the run.
- Evidence stays traceable: supporting papers / quotes are computed from real
  analysis output, not invented by a single "find the gaps" prompt.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import time
from typing import Any, Callable

import httpx

from . import analysis, literature
from .llm_service import UNTRUSTED_RULE, LLMError, fence_untrusted, generate_json

# --------------------------------------------------------------------------
# Hard limits (env-overridable). Defaults are conservative on purpose so a run
# stays affordable on a free LLM tier. Raise them only with quota to spare.
# --------------------------------------------------------------------------
MAX_PAPERS = int(os.getenv("DISCOVERY_MAX_PAPERS", "6"))
ANALYSIS_CONCURRENCY = int(os.getenv("DISCOVERY_ANALYSIS_CONCURRENCY", "2"))
MAX_GAPS = int(os.getenv("DISCOVERY_MAX_GAPS", "8"))
MAX_VALIDATIONS = int(os.getenv("DISCOVERY_MAX_VALIDATIONS", "6"))
MAX_LLM_CALLS = int(os.getenv("DISCOVERY_MAX_LLM_CALLS", "40"))
MAX_ITERATIONS = int(os.getenv("DISCOVERY_MAX_ITERATIONS", "2"))
MIN_PAPERS = int(os.getenv("DISCOVERY_MIN_PAPERS", "3"))
# Measured 2026-08-31 on nvidia/nemotron-3.5-lightning:free: a 4-paper run
# spends ~10.5 min analysing and ~3.5 min synthesising, so a 900s deadline
# starved validation and returned "unverified" gaps - the one verdict the
# product must not default to. Runs execute in the background and are polled,
# so a longer ceiling costs nothing but wall-clock.
DEADLINE_SECONDS = float(os.getenv("DISCOVERY_DEADLINE_SECONDS", "2400"))
VALIDATION_CORPUS_SIZE = int(os.getenv("DISCOVERY_VALIDATION_CORPUS", "12"))

ProgressCb = Callable[[str, dict[str, Any]], None] | None

_STOPWORDS = {
    "this", "that", "with", "from", "have", "into", "such", "than", "then",
    "when", "which", "while", "these", "those", "using", "used", "based",
    "paper", "study", "work", "approach", "method", "methods", "model",
    "models", "results", "data", "dataset", "datasets", "research", "gap",
    "gaps", "limitation", "limitations", "does", "only", "more", "also",
}


class BudgetExceeded(Exception):
    """Raised when a discovery run hits its LLM-call cap or wall-clock deadline."""


class Budget:
    """Bounds a discovery run: a maximum number of LLM calls and a deadline."""

    def __init__(self, max_calls: int = MAX_LLM_CALLS, deadline_seconds: float = DEADLINE_SECONDS) -> None:
        self.max_calls = max_calls
        self._deadline = time.monotonic() + deadline_seconds
        self.calls = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.calls)

    def expired(self) -> bool:
        return time.monotonic() > self._deadline

    def spend(self, n: int = 1) -> None:
        if self.expired():
            raise BudgetExceeded("discovery deadline exceeded")
        if self.calls + n > self.max_calls:
            raise BudgetExceeded(f"LLM call budget ({self.max_calls}) exhausted")
        self.calls += n


def _noop(_step: str, _data: dict[str, Any]) -> None:  # default progress sink
    return None


# --------------------------------------------------------------------------
# Small deterministic helpers (no LLM)
# --------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]{4,}", (text or "").lower())} - _STOPWORDS


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _work_to_body(work: dict[str, Any]) -> str:
    """Turn an OpenAlex work dict into text analyse_paper can consume.

    Discovery analyses abstracts (not full PDFs) - so downstream gaps are
    treated as candidate signals and always sent through validation.
    """
    authors = ", ".join((work.get("authors") or [])[:6])
    lines = [f"Title: {work.get('title', '')}"]
    if authors:
        lines.append(f"Authors: {authors}")
    if work.get("year"):
        lines.append(f"Year: {work['year']}")
    if work.get("venue"):
        lines.append(f"Venue: {work['venue']}")
    lines += ["", "Abstract:", work.get("abstract") or "(no abstract available)"]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Stage 2 - multi-paper analysis (reuses analysis.analyse_paper)
# --------------------------------------------------------------------------


async def analyse_papers(
    papers: list[dict[str, Any]],
    *,
    limit: int = MAX_PAPERS,
    concurrency: int = ANALYSIS_CONCURRENCY,
    budget: Budget | None = None,
    on_progress: ProgressCb = None,
) -> list[dict[str, Any]]:
    """Analyse several discovered papers concurrently, isolating failures.

    Returns one record per input paper: {paper, ok, analysis|error}. A failing
    paper (LLM error, timeout, budget) never blocks the others.
    """
    progress = on_progress or _noop
    papers = papers[: max(1, min(limit, MAX_PAPERS))]
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(index: int, work: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            if budget is not None:
                try:
                    budget.spend(1)
                except BudgetExceeded as exc:
                    return {"index": index, "paper": work, "ok": False, "error": str(exc)}
            try:
                result = await analysis.analyse_paper(_work_to_body(work))
                record = {"index": index, "paper": work, "ok": True, "analysis": result}
            except Exception as exc:  # noqa: BLE001 - isolate one paper's failure
                record = {"index": index, "paper": work, "ok": False, "error": str(exc)[:200]}
            progress("paper_analysed", {"ok": record["ok"], "title": work.get("title")})
            return record

    records = await asyncio.gather(*(_one(i, w) for i, w in enumerate(papers)))
    return sorted(records, key=lambda r: r["index"])


# --------------------------------------------------------------------------
# Stage 3 - cross-paper gap synthesis (structured pre-clustering + one LLM pass)
# --------------------------------------------------------------------------

_SYNTHESIS_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "gaps": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "cluster_index": {"type": "INTEGER"},
                    "title": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "category": {
                        "type": "STRING",
                        "enum": [
                            "dataset", "evaluation", "generalization", "comparison",
                            "scalability", "methodological", "reproducibility", "application",
                        ],
                    },
                    "research_area": {"type": "STRING"},
                    "affected_methods": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "affected_datasets": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "confidence": {"type": "STRING", "enum": ["low", "medium", "high"]},
                },
                "required": ["cluster_index", "title", "description", "category", "confidence"],
            },
        }
    },
    "required": ["gaps"],
}

_SYNTHESIS_PROMPT = """You are synthesising research gaps ACROSS several papers on one topic.

Below are clusters of related observations (stated limitations, future work and
reviewer-inferred gaps) already grouped by similarity. For each cluster, write ONE
clean candidate research gap that captures the shared unresolved problem.

Rules:
- Use ONLY the observations given. Do not invent problems not present in them.
- Return exactly one gap per cluster, keyed by its `cluster_index`.
- `title`: a specific, checkable unresolved problem (not "more research is needed").
- `affected_methods` / `affected_datasets`: only those named in the observations.
- `confidence` reflects how consistently the clusters' papers raise this - not certainty.
- Do NOT claim anything is novel; that is decided later by validation.
- {untrusted_rule}

CLUSTERS:
{clusters}"""


def _collect_observations(analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull gap/limitation observations out of successful analyses, tagged by paper."""
    observations: list[dict[str, Any]] = []
    for record in analyses:
        if not record.get("ok"):
            continue
        work = record["paper"]
        paper_ref = {
            "openalex_id": work.get("openalex_id"),
            "title": work.get("title"),
            "year": work.get("year"),
            "url": work.get("url"),
        }
        result = record.get("analysis") or {}
        for gap in result.get("gaps") or []:
            observations.append({
                "paper": paper_ref,
                "text": f"{gap.get('title', '')} {gap.get('description', '')}".strip(),
                "quote": gap.get("evidence_from_paper", ""),
                "category": gap.get("category", "methodological"),
                "kind": gap.get("origin", "inferred"),
            })
        for text in result.get("stated_limitations") or []:
            observations.append({
                "paper": paper_ref, "text": text, "quote": text,
                "category": "methodological", "kind": "stated_limitation",
            })
    return observations


def _pre_cluster(observations: list[dict[str, Any]], threshold: float = 0.34) -> list[list[dict[str, Any]]]:
    """Greedy Jaccard clustering of observations by keyword overlap.

    Deterministic intermediate structure so the LLM merges *grouped* evidence
    rather than being asked cold to "find gaps".
    """
    clusters: list[dict[str, Any]] = []  # {tokens, members}
    for obs in observations:
        tokens = _tokens(obs["text"])
        best, best_sim = None, 0.0
        for cluster in clusters:
            sim = _jaccard(tokens, cluster["tokens"])
            if sim > best_sim:
                best, best_sim = cluster, sim
        if best is not None and best_sim >= threshold:
            best["members"].append(obs)
            best["tokens"] |= tokens
        else:
            clusters.append({"tokens": tokens, "members": [obs]})
    # Recurring problems first (more supporting papers = more interesting).
    clusters.sort(key=lambda c: len({id(m["paper"]) for m in c["members"]}), reverse=True)
    return [c["members"] for c in clusters]


def _cluster_facts(members: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic, traceable facts for a cluster: supporting papers + evidence."""
    papers, seen = [], set()
    evidence = []
    for obs in members:
        key = obs["paper"].get("openalex_id") or obs["paper"].get("title")
        if key and key not in seen:
            seen.add(key)
            papers.append(obs["paper"])
        if obs.get("quote"):
            evidence.append({"paper": obs["paper"].get("title"), "quote": obs["quote"][:400]})
    return {"supporting_papers": papers, "frequency": len(papers), "evidence": evidence[:6]}


async def synthesize_gaps(
    analyses: list[dict[str, Any]],
    *,
    max_gaps: int = MAX_GAPS,
    budget: Budget | None = None,
    on_progress: ProgressCb = None,
) -> list[dict[str, Any]]:
    """Merge per-paper gaps into cross-paper candidate gaps with traceable evidence."""
    progress = on_progress or _noop
    observations = _collect_observations(analyses)
    if not observations:
        return []

    clusters = _pre_cluster(observations)[:max_gaps]
    blocks = []
    for index, members in enumerate(clusters):
        facts = _cluster_facts(members)
        lines = [f"[cluster {index}] papers: {facts['frequency']}"]
        for obs in members[:6]:
            lines.append(f"  - ({obs['category']}/{obs['kind']}) {obs['text'][:180]}")
        blocks.append("\n".join(lines))

    merged: dict[str, Any] = {"gaps": []}
    if budget is not None:
        try:
            budget.spend(1)
            merged = await generate_json(
                _SYNTHESIS_PROMPT.format(
                    clusters=fence_untrusted("\n\n".join(blocks)),
                    untrusted_rule=UNTRUSTED_RULE,
                ),
                _SYNTHESIS_SCHEMA, temperature=0.2,
            )
        except (LLMError, BudgetExceeded):
            merged = {"gaps": []}
    else:
        try:
            merged = await generate_json(
                _SYNTHESIS_PROMPT.format(
                    clusters=fence_untrusted("\n\n".join(blocks)),
                    untrusted_rule=UNTRUSTED_RULE,
                ),
                _SYNTHESIS_SCHEMA, temperature=0.2,
            )
        except LLMError:
            merged = {"gaps": []}

    by_index = {g.get("cluster_index"): g for g in merged.get("gaps") or []}
    candidates: list[dict[str, Any]] = []
    for index, members in enumerate(clusters):
        facts = _cluster_facts(members)
        llm = by_index.get(index, {})
        candidates.append({
            "id": f"cg{index + 1}",
            "title": llm.get("title") or (members[0]["text"][:90] if members else "Unnamed gap"),
            "description": llm.get("description", ""),
            "category": llm.get("category", members[0]["category"] if members else "methodological"),
            "research_area": llm.get("research_area", ""),
            "affected_methods": llm.get("affected_methods", []),
            "affected_datasets": llm.get("affected_datasets", []),
            "confidence": llm.get("confidence", "low"),
            "frequency": facts["frequency"],
            "supporting_papers": facts["supporting_papers"],
            "evidence": facts["evidence"],
        })
    progress("gaps_synthesised", {"count": len(candidates)})
    return candidates


# --------------------------------------------------------------------------
# Stage 4 - adversarial gap validation (reuses OpenAlex search)
# --------------------------------------------------------------------------

_VALIDATION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "verdict": {"type": "STRING", "enum": ["open", "partial", "addressed", "unverified"]},
        "confidence": {"type": "STRING", "enum": ["low", "medium", "high"]},
        "reasoning": {"type": "STRING"},
        "supporting_refs": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "contradicting_refs": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "what_remains": {"type": "STRING"},
    },
    "required": ["verdict", "confidence", "reasoning"],
}

_VALIDATION_PROMPT = """You are ADVERSARIALLY validating a proposed research gap - actively try to DISPROVE it.

GAP: {title}
{description}

Below is later/related literature retrieved for this gap. Decide whether the problem
is still open, and separate the evidence:

- "addressed": at least one paper clearly solves the gap. Cite it in `contradicting_refs`.
- "partial": related work covers part of it (different scope/domain).
- "open": the corpus does not solve it. `contradicting_refs` = [].
- "unverified": the corpus is too thin/off-topic to judge.

Rules:
- Judge ONLY against the corpus below; you have no other knowledge.
- `supporting_refs`: papers showing the gap is REAL / still unsolved.
- `contradicting_refs`: papers showing it is (partly) SOLVED.
- Never call a gap novel merely because the corpus lacks a solution - use "unverified".
- `what_remains`: one sentence on what a new project could still contribute.
- {untrusted_rule}

CORPUS ({n} papers):
{corpus}"""


def _validation_queries(gap: dict[str, Any]) -> list[str]:
    """Heuristic query variants for a gap (no LLM) - title + method/area terms."""
    base = (gap.get("title") or "").strip()
    queries = [base] if base else []
    methods = gap.get("affected_methods") or []
    area = gap.get("research_area") or ""
    if methods:
        queries.append(f"{base} {methods[0]}".strip())
    if area:
        queries.append(f"{area} {base}".strip())
    seen, out = set(), []
    for q in queries:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:3]


def _format_corpus(corpus: list[dict[str, Any]]) -> str:
    """Render retrieved papers for a prompt.

    Titles and abstracts come from OpenAlex, so they are attacker-controllable:
    publishing a preprint is enough to put chosen text in front of the model.
    The block is fenced so it cannot act as an instruction.
    """
    lines = []
    for work in corpus:
        authors = ", ".join((work.get("authors") or [])[:2]) or "unknown"
        abstract = (work.get("abstract") or "")[:400] or "(no abstract)"
        lines.append(
            f"[{work['ref']}] {work.get('title')} ({work.get('year', 'n.d.')}) - {authors}"
            f" - {work.get('citations', 0)} citations\n    {abstract}"
        )
    return fence_untrusted("\n".join(lines) or "(no papers retrieved)")


async def validate_gap(
    gap: dict[str, Any],
    *,
    budget: Budget | None = None,
    on_progress: ProgressCb = None,
) -> dict[str, Any]:
    """Challenge one candidate gap against fresh OpenAlex evidence."""
    progress = on_progress or _noop
    queries = _validation_queries(gap)

    corpus: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            for query in queries:
                works = await literature.search_recent(client, query, None, limit=8)
                for work in works:
                    key = work["openalex_id"] or work["title"].lower()
                    if key and key not in seen:
                        seen.add(key)
                        corpus.append(work)
    except Exception:  # noqa: BLE001 - retrieval failure must not crash the run
        corpus = []

    corpus.sort(key=lambda w: (w.get("citations") or 0), reverse=True)
    corpus = corpus[:VALIDATION_CORPUS_SIZE]
    for index, work in enumerate(corpus, start=1):
        work["ref"] = index
    by_ref = {w["ref"]: w for w in corpus}

    result = {
        **gap,
        "verdict": "unverified",
        "confidence": "low",
        "reasoning": "No later literature could be retrieved for this gap.",
        "supporting_evidence": [],
        "contradicting_evidence": [],
        "recent_evidence": [],
        "what_remains": "",
        "validation_corpus_size": len(corpus),
    }
    if not corpus:
        progress("gap_validated", {"id": gap.get("id"), "verdict": "unverified"})
        return result

    if budget is not None:
        try:
            budget.spend(1)
        except BudgetExceeded:
            result["reasoning"] = "Validation skipped: LLM budget exhausted."
            progress("gap_validated", {"id": gap.get("id"), "verdict": "unverified"})
            return result
    try:
        verdict = await generate_json(
            _VALIDATION_PROMPT.format(
                # Gap text is derived from paper content, so it is untrusted too.
                title=fence_untrusted(gap.get("title", ""), limit=300),
                description=fence_untrusted(gap.get("description", ""), limit=1200),
                n=len(corpus),
                corpus=_format_corpus(corpus),
                untrusted_rule=UNTRUSTED_RULE,
            ),
            _VALIDATION_SCHEMA, temperature=0.1,
        )
    except LLMError as exc:
        result["reasoning"] = f"Validation LLM call failed: {exc}"
        progress("gap_validated", {"id": gap.get("id"), "verdict": "unverified"})
        return result

    support = [by_ref[r] for r in (verdict.get("supporting_refs") or []) if r in by_ref]
    contra = [by_ref[r] for r in (verdict.get("contradicting_refs") or []) if r in by_ref]
    newest = max((w.get("year") or 0) for w in corpus)
    result.update({
        "verdict": verdict.get("verdict", "unverified"),
        "confidence": verdict.get("confidence", "low"),
        "reasoning": verdict.get("reasoning", ""),
        "supporting_evidence": support,
        "contradicting_evidence": contra,
        "recent_evidence": [w for w in corpus if (w.get("year") or 0) >= newest - 1][:5],
        "what_remains": verdict.get("what_remains", ""),
    })
    progress("gap_validated", {"id": gap.get("id"), "verdict": result["verdict"]})
    return result


# --------------------------------------------------------------------------
# Stage 5 - opportunity ranking (deterministic score + one explanation pass)
# --------------------------------------------------------------------------

_VERDICT_WEIGHT = {"open": 1.0, "partial": 0.6, "unverified": 0.4, "addressed": 0.1}
_CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.66, "low": 0.33}

_EXPLAIN_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {"type": "INTEGER"},
                    "why_it_matters": {"type": "STRING"},
                    "suggested_next_step": {"type": "STRING"},
                    "novelty_assessment": {"type": "STRING"},
                    "feasibility": {"type": "STRING", "enum": ["low", "medium", "high"]},
                },
                "required": ["index", "why_it_matters", "suggested_next_step"],
            },
        }
    },
    "required": ["items"],
}

_EXPLAIN_PROMPT = """For each validated research gap below, explain the opportunity for a student.

Rules:
- `why_it_matters`: 1-2 sentences grounded in the evidence given.
- `suggested_next_step`: a concrete first experiment or study.
- `novelty_assessment`: describe novelty only as "not found addressed in the checked
  corpus" - NEVER claim it is globally novel.
- `feasibility`: low/medium/high, judged on data/compute realism.

GAPS:
{gaps}"""


def _score(gap: dict[str, Any]) -> tuple[float, dict[str, float]]:
    verdict = _VERDICT_WEIGHT.get(gap.get("verdict", "unverified"), 0.4)
    confidence = _CONFIDENCE_WEIGHT.get(gap.get("confidence", "low"), 0.33)
    frequency = min(1.0, math.log1p(gap.get("frequency", 1)) / math.log(6))
    contradicted = 1.0 - min(1.0, 0.5 * len(gap.get("contradicting_evidence") or []))
    support = min(1.0, 0.25 * len(gap.get("supporting_evidence") or []))
    breakdown = {
        "unresolved": round(verdict, 3),
        "recurrence": round(frequency, 3),
        "evidence_strength": round(support, 3),
        "not_contradicted": round(contradicted, 3),
        "confidence": round(confidence, 3),
    }
    score = (
        0.40 * verdict + 0.20 * frequency + 0.15 * support
        + 0.15 * contradicted + 0.10 * confidence
    )
    return round(score * 100, 1), breakdown


async def rank_opportunities(
    validated_gaps: list[dict[str, Any]],
    *,
    budget: Budget | None = None,
    on_progress: ProgressCb = None,
    explain_top: int = 6,
) -> list[dict[str, Any]]:
    """Turn validated gaps into ranked, explained research opportunities."""
    progress = on_progress or _noop
    ranked = []
    for gap in validated_gaps:
        score, breakdown = _score(gap)
        ranked.append({**gap, "system_score": score, "score_breakdown": breakdown})
    ranked.sort(key=lambda g: g["system_score"], reverse=True)

    top = ranked[:explain_top]
    if top and budget is not None and budget.remaining > 0:
        blocks = []
        for index, gap in enumerate(top):
            blocks.append(
                f"[{index}] {gap.get('title')} - verdict={gap.get('verdict')}, "
                f"papers={gap.get('frequency')}, remains={gap.get('what_remains', '')[:120]}"
            )
        try:
            budget.spend(1)
            explained = await generate_json(
                _EXPLAIN_PROMPT.format(gaps="\n".join(blocks)), _EXPLAIN_SCHEMA, temperature=0.3,
            )
            for item in explained.get("items") or []:
                idx = item.get("index")
                if isinstance(idx, int) and 0 <= idx < len(top):
                    top[idx]["why_it_matters"] = item.get("why_it_matters", "")
                    top[idx]["suggested_next_step"] = item.get("suggested_next_step", "")
                    top[idx]["novelty_assessment"] = item.get("novelty_assessment", "")
                    top[idx]["feasibility"] = item.get("feasibility", "medium")
        except (LLMError, BudgetExceeded):
            pass  # ranking still valid without prose explanations

    for gap in ranked:
        gap.setdefault("why_it_matters", "")
        gap.setdefault("novelty_assessment",
                       "Not found addressed in the validation corpus checked.")
        gap.setdefault("feasibility", "medium")
        gap["score_is_system_generated"] = True
    progress("opportunities_ranked", {"count": len(ranked)})
    return ranked


# --------------------------------------------------------------------------
# Stage 6 - orchestrator (bounded; makes light decisions, never loops forever)
# --------------------------------------------------------------------------


async def _refine_query(query: str, budget: Budget | None) -> str:
    """Ask the LLM once for a better/broader search query (best-effort)."""
    if budget is None or budget.remaining <= 0:
        return query
    try:
        budget.spend(1)
        out = await generate_json(
            "Rewrite this research topic into a single broader OpenAlex search query "
            "(<= 12 words, no boolean operators). Return JSON {\"query\": \"...\"}.\n\n"
            f"TOPIC: {query}",
            {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]},
            temperature=0.3,
        )
        return (out.get("query") or query).strip() or query
    except (LLMError, BudgetExceeded):
        return query


async def run_discovery(
    query: str,
    *,
    goal: str = "gaps",
    max_papers: int = MAX_PAPERS,
    max_validations: int = MAX_VALIDATIONS,
    budget: Budget | None = None,
    on_progress: ProgressCb = None,
) -> dict[str, Any]:
    """Autonomously drive topic -> opportunities, bounded by `budget` and caps."""
    query = (query or "").strip()
    if not query:
        raise ValueError("query must be a non-empty string")
    max_papers = max(1, min(max_papers, MAX_PAPERS))
    max_validations = max(1, min(max_validations, MAX_VALIDATIONS))
    budget = budget or Budget()
    progress = on_progress or _noop
    trace: list[dict[str, Any]] = []

    def step(name: str, data: dict[str, Any] | None = None) -> None:
        trace.append({"step": name, "data": data or {}, "t": round(time.monotonic(), 2)})
        progress(name, data or {})

    step("question_understood", {"query": query, "goal": goal})

    # --- discovery, with one bounded "search again if too few" decision -----
    search_query = query
    candidates: list[dict[str, Any]] = []
    for iteration in range(max(1, MAX_ITERATIONS)):
        candidates = await literature.search_topic(search_query, limit=max_papers * 2)
        step("papers_discovered", {"count": len(candidates), "iteration": iteration})
        if len(candidates) >= MIN_PAPERS:
            break
        if iteration + 1 < MAX_ITERATIONS and not budget.expired():
            search_query = await _refine_query(query, budget)
            step("search_refined", {"query": search_query})

    selected = candidates[:max_papers]
    step("papers_selected", {"count": len(selected)})
    if not selected:
        return {"query": query, "goal": goal, "error": "no papers found for this topic",
                "opportunities": [], "trace": trace, "llm_calls": budget.calls}

    # --- analyse -> synthesise -> validate -> rank --------------------------
    analyses = await analyse_papers(selected, limit=max_papers, budget=budget, on_progress=progress)
    analysed_ok = [a for a in analyses if a.get("ok")]
    step("papers_analysed", {"ok": len(analysed_ok), "failed": len(analyses) - len(analysed_ok)})
    if not analysed_ok:
        return {"query": query, "goal": goal, "error": "no papers could be analysed",
                "papers": [a["paper"] for a in analyses], "opportunities": [],
                "trace": trace, "llm_calls": budget.calls}

    candidate_gaps = await synthesize_gaps(analysed_ok, budget=budget, on_progress=progress)
    # Orchestrator decision: recurring gaps first, then cap validation spend.
    candidate_gaps.sort(key=lambda g: g.get("frequency", 0), reverse=True)
    to_validate = candidate_gaps[:max_validations]
    step("gaps_selected_for_validation", {"count": len(to_validate)})

    validated: list[dict[str, Any]] = []
    for gap in to_validate:
        if budget.expired():
            break
        validated.append(await validate_gap(gap, budget=budget, on_progress=progress))

    opportunities = await rank_opportunities(validated, budget=budget, on_progress=progress)
    step("done", {"opportunities": len(opportunities)})

    return {
        "query": query,
        "goal": goal,
        "papers": [a["paper"] for a in analyses],
        "papers_analysed": len(analysed_ok),
        "candidate_gaps": len(candidate_gaps),
        "opportunities": opportunities,
        "llm_calls": budget.calls,
        "trace": trace,
    }
