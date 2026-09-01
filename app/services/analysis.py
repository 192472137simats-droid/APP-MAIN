"""Prompts + orchestration: paper -> analysis -> grounded gaps -> chat."""

from typing import Any

from . import citations, literature
from .llm_service import (
    UNTRUSTED_RULE,
    fence_untrusted,
    generate_from_images,
    generate_json,
    generate_text,
)

# --------------------------------------------------------------------------
# Schemas (OpenAPI subset accepted by Gemini's responseSchema)
# --------------------------------------------------------------------------

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "metadata": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING"},
                "authors": {"type": "ARRAY", "items": {"type": "STRING"}},
                "year": {"type": "INTEGER"},
                "venue": {"type": "STRING"},
                "domain": {"type": "STRING"},
                "keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
            "required": ["title"],
        },
        "summary": {
            "type": "OBJECT",
            "properties": {
                "one_line": {"type": "STRING"},
                "short": {"type": "STRING"},
                "detailed": {"type": "STRING"},
                "eli5": {"type": "STRING"},
            },
            "required": ["one_line", "short", "detailed", "eli5"],
        },
        "objective": {"type": "STRING"},
        "method": {"type": "STRING"},
        "datasets": {"type": "ARRAY", "items": {"type": "STRING"}},
        "metrics": {"type": "ARRAY", "items": {"type": "STRING"}},
        "contributions": {"type": "ARRAY", "items": {"type": "STRING"}},
        "findings": {"type": "ARRAY", "items": {"type": "STRING"}},
        "conclusion": {"type": "STRING"},
        "stated_limitations": {"type": "ARRAY", "items": {"type": "STRING"}},
        "stated_future_work": {"type": "ARRAY", "items": {"type": "STRING"}},
        "gaps": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "title": {"type": "STRING"},
                    "category": {
                        "type": "STRING",
                        "enum": [
                            "dataset",
                            "evaluation",
                            "generalization",
                            "comparison",
                            "scalability",
                            "methodological",
                            "reproducibility",
                            "application",
                        ],
                    },
                    "description": {"type": "STRING"},
                    "evidence_from_paper": {"type": "STRING"},
                    "origin": {"type": "STRING", "enum": ["stated", "inferred"]},
                    "search_query": {"type": "STRING"},
                },
                "required": [
                    "id",
                    "title",
                    "category",
                    "description",
                    "evidence_from_paper",
                    "origin",
                    "search_query",
                ],
            },
        },
    },
    "required": ["metadata", "summary", "objective", "method", "gaps"],
}

GROUNDING_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "gaps": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "verdict": {"type": "STRING", "enum": ["open", "partial", "addressed"]},
                    "confidence": {"type": "STRING", "enum": ["low", "medium", "high"]},
                    "reasoning": {"type": "STRING"},
                    "evidence_refs": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                    "what_remains": {"type": "STRING"},
                },
                "required": ["id", "verdict", "confidence", "reasoning", "evidence_refs"],
            },
        }
    },
    "required": ["gaps"],
}

COMPARE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "rows": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "aspect": {"type": "STRING"},
                    "values": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["aspect", "values"],
            },
        },
        "shared_gaps": {"type": "ARRAY", "items": {"type": "STRING"}},
        "contradictions": {"type": "ARRAY", "items": {"type": "STRING"}},
        "synthesis": {"type": "STRING"},
    },
    "required": ["rows", "shared_gaps", "synthesis"],
}


# --------------------------------------------------------------------------
# Stage 1 - analyse the paper
# --------------------------------------------------------------------------

ANALYSIS_PROMPT = """You are an expert research analyst. Analyse the research paper below.

Produce a faithful structured analysis. Rules:
- Use ONLY what the paper states. Never invent numbers, datasets, baselines or citations.
- {rule}
- `metadata.authors`: the author names printed under the title on the first page,
  in order, as "First Last". These are almost always present even when no
  "Authors:" label appears - look at the lines between the title and the
  abstract, and ignore affiliations, emails and footnote markers. Return an
  empty list only if the first page genuinely shows no names.
- If something is not in the paper, return an empty string or empty list for it.
- `stated_limitations` and `stated_future_work` must be things the AUTHORS wrote.
- `gaps` is different: these are research directions this paper leaves open.
  * Include every stated future-work item as a gap with origin="stated".
  * Then add gaps a careful reviewer would raise that the authors did NOT admit,
    with origin="inferred" (e.g. single dataset, no comparison against newer
    methods, no ablation, evaluated on one language/population, no cost analysis,
    no reproducibility artefacts).
  * `evidence_from_paper` must quote or closely paraphrase the specific part of
    the paper that shows this gap exists. No evidence -> do not list the gap.
  * `search_query` is a short literature-search phrase (5-12 words, no boolean
    operators) that would find later work addressing this specific gap.
  * Give each gap a stable id: "g1", "g2", ...
- Aim for 4-7 gaps. Quality over quantity.
- The text contains [page N] markers. End each entry in `findings` and
  `contributions` with the page it came from, like "(p. 4)". Use the marker
  that precedes the text you drew it from. Omit it if you genuinely cannot tell.

PAPER:
---
{paper}
---"""


async def analyse_paper(body: str) -> dict[str, Any]:
    return await generate_json(
        ANALYSIS_PROMPT.format(paper=fence_untrusted(body), rule=UNTRUSTED_RULE),
        ANALYSIS_SCHEMA,
        temperature=0.2,
    )


# --------------------------------------------------------------------------
# Stage 2 - ground the gaps against the literature
# --------------------------------------------------------------------------

GROUNDING_PROMPT = """You are checking whether research gaps are still open.

SOURCE PAPER: "{title}" ({year})

Below are research gaps this paper left open, and a corpus of later papers
retrieved from OpenAlex. For each gap, decide whether the later work has closed it.

Verdicts:
- "addressed": at least one corpus paper clearly does the thing the gap asks for.
- "partial": related work exists but only covers part of it (different domain,
  smaller scope, adjacent problem).
- "open": nothing in the corpus addresses it.

Rules:
- Judge ONLY against the corpus below. You have no other knowledge of what exists.
- `evidence_refs` must list the [ref] numbers you relied on. For "open", return [].
- Never cite a ref number that is not in the corpus.
- Be conservative: a vaguely similar title is not evidence. If the abstract does
  not show the gap being addressed, it is not addressed.
- `confidence` reflects how well the corpus covers the topic, not how sure you
  feel. A small or off-topic corpus means low confidence.
- `what_remains` : one sentence on what a new project could still contribute.

GAPS:
{gaps}

CORPUS ({corpus_size} papers published from {year} onward):
{corpus}"""


def _format_gaps(gaps: list[dict[str, Any]]) -> str:
    lines = []
    for gap in gaps:
        lines.append(
            f"[{gap['id']}] ({gap.get('category', 'general')}) {gap['title']}\n"
            f"    {gap.get('description', '')}"
        )
    return "\n".join(lines)


def _format_corpus(corpus: list[dict[str, Any]]) -> str:
    if not corpus:
        return "(no papers retrieved)"
    lines = []
    for work in corpus:
        authors = ", ".join(work["authors"][:2]) or "unknown"
        abstract = work["abstract"][:600] or "(no abstract available)"
        lines.append(
            f"[{work['ref']}] {work['title']} ({work['year'] or 'n.d.'}) - {authors}"
            f" - {work['citations']} citations\n    {abstract}"
        )
    return "\n".join(lines)


async def ground_gaps(analysis: dict[str, Any]) -> dict[str, Any]:
    """Retrieve later literature, then judge each gap against it."""
    gaps = analysis.get("gaps") or []
    metadata = analysis.get("metadata") or {}
    title = metadata.get("title", "")
    year = metadata.get("year")

    queries = [g.get("search_query", "") for g in gaps if g.get("search_query")]
    if not queries and title:
        queries = [title]

    retrieval = await literature.build_corpus(title, year, queries)
    corpus = retrieval["corpus"]

    if not gaps:
        return {**retrieval, "gaps": []}

    if not corpus:
        # Retrieval failed - be honest rather than guessing.
        judged = [
            {
                **gap,
                "verdict": "unverified",
                "confidence": "low",
                "reasoning": "No later literature could be retrieved, so this gap "
                "could not be checked against published work.",
                "evidence": [],
                "what_remains": "",
            }
            for gap in gaps
        ]
        return {**retrieval, "gaps": judged}

    verdicts = await generate_json(
        GROUNDING_PROMPT.format(
            title=title or "Untitled",
            year=retrieval.get("paper_year") or "n.d.",
            gaps=_format_gaps(gaps),
            corpus=_format_corpus(corpus),
            corpus_size=len(corpus),
        ),
        GROUNDING_SCHEMA,
        temperature=0.1,
    )

    by_id = {v["id"]: v for v in verdicts.get("gaps", [])}
    by_ref = {w["ref"]: w for w in corpus}

    judged = []
    for gap in gaps:
        verdict = by_id.get(gap["id"], {})
        refs = [r for r in verdict.get("evidence_refs", []) if r in by_ref]
        judged.append(
            {
                **gap,
                "verdict": verdict.get("verdict", "unverified"),
                "confidence": verdict.get("confidence", "low"),
                "reasoning": verdict.get("reasoning", ""),
                "what_remains": verdict.get("what_remains", ""),
                "evidence": [by_ref[r] for r in refs],
            }
        )

    return {**retrieval, "gaps": judged}


# --------------------------------------------------------------------------
# Stage 3 - chat
# --------------------------------------------------------------------------

CHAT_PROMPT = """You are a research assistant answering questions about one specific paper.

Rules:
- Answer from the paper text below as the primary source.
- If the answer is not in the paper, say "That is not stated in this paper" and
  then, only if useful, say what the paper does cover instead.
- Never invent numbers, citations or results.
- Name the section you drew the answer from, like (Section: Results).
- Be direct. Two or three sentences unless the question needs more.
- {rule}
{extra_context}
PAPER: {title}
---
{paper}
---

QUESTION: {question}"""

RELATED_BLOCK = """
You may also use this retrieved related literature when the question is about
what OTHER work has done. Cite it as [ref N] and never confuse it with the paper.

RELATED WORK:
{corpus}
"""


async def answer_question(
    question: str,
    paper: dict[str, Any],
    history: list[dict[str, str]],
) -> str:
    analysis = paper.get("analysis") or {}
    title = (analysis.get("metadata") or {}).get("title") or paper.get("filename", "paper")

    extra = ""
    grounding = paper.get("grounding") or {}
    corpus = grounding.get("corpus") or []
    if corpus:
        extra = RELATED_BLOCK.format(corpus=_format_corpus(corpus[:15]))

    prompt = CHAT_PROMPT.format(
        rule=UNTRUSTED_RULE,
        extra_context=extra,
        title=title,
        paper=fence_untrusted(paper.get("full_text", ""), limit=180000),
        question=question,
    )
    return await generate_text(prompt, history=history, temperature=0.3)


# --------------------------------------------------------------------------
# Stage 4 - compare papers
# --------------------------------------------------------------------------

COMPARE_PROMPT = """Compare these research papers side by side.

Return rows for these aspects, in this order: Objective, Method, Dataset,
Key result, Stated limitation, Main open gap.
Each row's `values` array must have exactly {count} entries, in the same order
as the papers below. Use "not stated" where the paper does not say.

`shared_gaps`: research directions that NONE of these papers address - this is
the most valuable output, be specific.
`contradictions`: places where the papers disagree, if any.
`synthesis`: 3-4 sentences on where this line of research currently stands and
what a new project could contribute.

{rule}

PAPERS:
{papers}"""


async def compare_papers(papers: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = []
    for index, paper in enumerate(papers, start=1):
        analysis = paper.get("analysis") or {}
        metadata = analysis.get("metadata") or {}
        gaps = (paper.get("grounding") or {}).get("gaps") or analysis.get("gaps") or []
        gap_text = "; ".join(g.get("title", "") for g in gaps[:5])
        blocks.append(
            f"### Paper {index}: {metadata.get('title', 'Untitled')} ({metadata.get('year', 'n.d.')})\n"
            f"Objective: {analysis.get('objective', '')}\n"
            f"Method: {analysis.get('method', '')}\n"
            f"Datasets: {', '.join(analysis.get('datasets') or []) or 'not stated'}\n"
            f"Findings: {'; '.join(analysis.get('findings') or [])}\n"
            f"Stated limitations: {'; '.join(analysis.get('stated_limitations') or []) or 'not stated'}\n"
            f"Identified gaps: {gap_text}"
        )

    return await generate_json(
        COMPARE_PROMPT.format(
            count=len(papers),
            papers=fence_untrusted("\n\n".join(blocks)),
            rule=UNTRUSTED_RULE,
        ),
        COMPARE_SCHEMA,
        temperature=0.2,
    )


# --------------------------------------------------------------------------
# Stage 5 - OCR for scanned papers
#
# No Tesseract binary needed: the model reads rendered page images directly,
# which also handles two-column layouts and equations better than classic OCR.
# --------------------------------------------------------------------------

OCR_PROMPT = """Transcribe the text of these research paper pages, in reading order.

Rules:
- Output plain text only. No commentary, no markdown fences.
- Preserve section headings on their own line exactly as printed.
- For two-column layouts, read the full left column before the right column.
- Prefix each page's text with its marker on its own line: [page N], numbering
  from {start} in the order the images are given.
- Skip page furniture (running heads, page numbers, figure pixel noise).
- Transcribe what is printed. Never summarise, correct or invent text."""


async def transcribe_pages(images: list[bytes], start: int = 1) -> str:
    """Read scanned pages via the vision model. Batched to bound request size."""
    if not images:
        return ""

    batch_size = 5
    chunks: list[str] = []
    for offset in range(0, len(images), batch_size):
        batch = images[offset : offset + batch_size]
        text = await generate_from_images(
            OCR_PROMPT.format(start=start + offset), batch, temperature=0.0
        )
        chunks.append(text.strip())
    return "\n\n".join(c for c in chunks if c)


# --------------------------------------------------------------------------
# Stage 6 - turn a gap into a project proposal
# --------------------------------------------------------------------------

TOPIC_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "problem": {"type": "STRING"},
        "why_it_is_open": {"type": "STRING"},
        "proposed_approach": {"type": "STRING"},
        "datasets": {"type": "ARRAY", "items": {"type": "STRING"}},
        "evaluation_plan": {"type": "STRING"},
        "expected_contribution": {"type": "STRING"},
        "prerequisites": {"type": "ARRAY", "items": {"type": "STRING"}},
        "difficulty": {"type": "STRING", "enum": ["mini-project", "capstone", "thesis"]},
        "risks": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [
        "title", "problem", "why_it_is_open", "proposed_approach",
        "evaluation_plan", "expected_contribution", "difficulty",
    ],
}

TOPIC_PROMPT = """Turn this open research gap into a concrete project proposal a
student could actually start.

SOURCE PAPER: "{title}" ({year})
Method used there: {method}
Datasets used there: {datasets}

THE GAP
Title: {gap_title}
Category: {gap_category}
Description: {gap_description}
Evidence from the paper: {gap_evidence}
Literature check: {gap_reasoning}
What remains: {gap_remains}

Rules:
- `title` reads like a real paper title. Specific, not grand.
- `proposed_approach` is concrete enough to start on Monday: name the models,
  the baseline to beat, and the comparison being made.
- `datasets` must be real, publicly available datasets suited to this problem.
  Empty list if you are not confident any exist.
- `evaluation_plan` states the metric and what result would count as success.
- `difficulty` reflects genuine scope, not ambition.
- `risks` are the things most likely to make this fail (compute, data access,
  weak baseline, unclear metric). Be blunt.
- Never claim the gap is globally novel. It is unaddressed in a bounded corpus."""


async def generate_topic(analysis: dict[str, Any], gap: dict[str, Any]) -> dict[str, Any]:
    metadata = analysis.get("metadata") or {}
    return await generate_json(
        TOPIC_PROMPT.format(
            title=metadata.get("title", "Untitled"),
            year=metadata.get("year", "n.d."),
            method=analysis.get("method", "not stated"),
            datasets=", ".join(analysis.get("datasets") or []) or "not stated",
            gap_title=gap.get("title", ""),
            gap_category=gap.get("category", ""),
            gap_description=gap.get("description", ""),
            gap_evidence=gap.get("evidence_from_paper", ""),
            gap_reasoning=gap.get("reasoning", "not checked"),
            gap_remains=gap.get("what_remains", ""),
        ),
        TOPIC_SCHEMA,
        temperature=0.4,
    )


# --------------------------------------------------------------------------
# Stage 7 - literature review across several papers
# --------------------------------------------------------------------------

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "introduction": {"type": "STRING"},
        "themes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "discussion": {"type": "STRING"},
                    "papers": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                },
                "required": ["name", "discussion", "papers"],
            },
        },
        "common_limitations": {"type": "ARRAY", "items": {"type": "STRING"}},
        "unresolved_problems": {"type": "ARRAY", "items": {"type": "STRING"}},
        "research_opportunity": {"type": "STRING"},
        "conclusion": {"type": "STRING"},
    },
    "required": [
        "title", "introduction", "themes", "common_limitations",
        "unresolved_problems", "research_opportunity", "conclusion",
    ],
}

REVIEW_PROMPT = """Write a literature review chapter over these {count} papers.

Style: an academic Chapter 2 "Related Work". Continuous prose, third person,
no bullet lists inside the prose fields. Refer to papers by their number as
[1], [2] and so on - the numbering below.

- `themes`: 2-5 groupings of what this body of work is actually about. Each
  theme's `papers` lists the numbers it draws on. Every paper appears in at
  least one theme.
- `common_limitations`: weaknesses recurring across several papers, not one.
- `unresolved_problems`: what NONE of these papers addresses. This is the most
  valuable part - be specific and concrete.
- `research_opportunity`: one paragraph a student could turn into a proposal.
- Use only what is given below. Do not invent findings, numbers or citations.

PAPERS:
{papers}"""


async def generate_review(papers: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = []
    for index, paper in enumerate(papers, start=1):
        analysis = paper.get("analysis") or {}
        metadata = analysis.get("metadata") or {}
        gaps = (paper.get("grounding") or {}).get("gaps") or analysis.get("gaps") or []
        open_gaps = [g.get("title", "") for g in gaps if g.get("verdict") in (None, "open")]
        blocks.append(
            f"### [{index}] {metadata.get('title', 'Untitled')} "
            f"({metadata.get('year', 'n.d.')}) - {metadata.get('venue') or 'unpublished'}\n"
            f"Authors: {', '.join(metadata.get('authors') or []) or 'not stated'}\n"
            f"Objective: {analysis.get('objective', '')}\n"
            f"Method: {analysis.get('method', '')}\n"
            f"Datasets: {', '.join(analysis.get('datasets') or []) or 'not stated'}\n"
            f"Findings: {'; '.join(analysis.get('findings') or [])}\n"
            f"Stated limitations: {'; '.join(analysis.get('stated_limitations') or []) or 'none stated'}\n"
            f"Gaps still open: {'; '.join(open_gaps) or 'none recorded'}"
        )

    result = await generate_json(
        REVIEW_PROMPT.format(count=len(papers), papers="\n\n".join(blocks)),
        REVIEW_SCHEMA,
        temperature=0.3,
    )
    result["references"] = [
        {
            "number": index,
            "id": paper.get("id"),
            "citation": citations.build(
                (paper.get("analysis") or {}).get("metadata") or {}
            )["ieee"],
        }
        for index, paper in enumerate(papers, start=1)
    ]
    return result


# --------------------------------------------------------------------------
# Stage 8 - chat across the whole library
# --------------------------------------------------------------------------

LIBRARY_CHAT_PROMPT = """You are a research assistant with access to summaries of
every paper in this library.

Answer using only what is below. Cite papers by title. If the library does not
contain the answer, say so plainly and name what it does cover instead.
Never invent papers, numbers or findings.
{rule}

LIBRARY ({count} papers):
{papers}

QUESTION: {question}"""


async def answer_across_library(
    question: str,
    papers: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
) -> str:
    if not papers:
        return "There are no papers in the library yet. Upload one to get started."

    blocks = []
    for paper in papers:
        analysis = paper.get("analysis") or {}
        metadata = analysis.get("metadata") or {}
        gaps = (paper.get("grounding") or {}).get("gaps") or analysis.get("gaps") or []
        open_titles = "; ".join(
            g.get("title", "") for g in gaps if g.get("verdict") in (None, "open")
        )
        blocks.append(
            f"### {metadata.get('title', paper.get('filename'))} ({metadata.get('year', 'n.d.')})\n"
            f"Objective: {analysis.get('objective', '')}\n"
            f"Method: {analysis.get('method', '')}\n"
            f"Datasets: {', '.join(analysis.get('datasets') or []) or 'not stated'}\n"
            f"Findings: {'; '.join(analysis.get('findings') or [])}\n"
            f"Open gaps: {open_titles}"
        )

    return await generate_text(
        LIBRARY_CHAT_PROMPT.format(
            count=len(papers),
            papers=fence_untrusted("\n\n".join(blocks)),
            question=question,
            rule=UNTRUSTED_RULE,
        ),
        history=history,
        temperature=0.3,
    )
