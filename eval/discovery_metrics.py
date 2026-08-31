"""Discovery-mode evaluation metrics.

Every number here is COMPUTED from a real run's output. Nothing is asserted,
estimated, or filled in from expectation - if a signal cannot be derived from
the run, it is reported as null rather than guessed.

The headline metric is the validation overturn rate: how often adversarial
validation refuses to confirm a gap that synthesis proposed. A system that
never overturns its own hypotheses is not checking them.

Usage:
    # score run JSON already on disk (no API calls, no cost)
    python eval/discovery_metrics.py --from-file run.json [run2.json ...]

    # execute topics against a running instance, then score them
    python eval/discovery_metrics.py --base-url http://127.0.0.1:8000 \
        --topic "LLM hallucination detection" --topic "graph neural network robustness"
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RESULTS_JSON = ROOT / "discovery_results.json"
RESULTS_MD = ROOT / "discovery_results.md"

# Verdicts meaning validation declined to confirm the gap as proposed.
_OVERTURNED = {"addressed", "partial"}


def _pct(numerator: int, denominator: int) -> float | None:
    """Percentage, or None when there is nothing to divide by.

    None is deliberate: reporting 0% for an empty denominator would read as a
    measured result rather than an absent one.
    """
    if not denominator:
        return None
    return round(100.0 * numerator / denominator, 1)


def score_run(run: dict[str, Any]) -> dict[str, Any]:
    """Derive metrics from one discovery run result."""
    opportunities = run.get("opportunities") or []
    papers = run.get("papers") or []
    analysed = run.get("papers_analysed") or 0
    candidates = run.get("candidate_gaps") or 0

    verdicts: dict[str, int] = {}
    for opportunity in opportunities:
        verdict = opportunity.get("verdict") or "unverified"
        verdicts[verdict] = verdicts.get(verdict, 0) + 1

    judged = sum(count for name, count in verdicts.items() if name != "unverified")
    overturned = sum(verdicts.get(name, 0) for name in _OVERTURNED)

    # A gap called open with no supporting evidence is the failure mode the
    # product exists to avoid: novelty asserted from an absence of results.
    unsupported_open = sum(
        1
        for o in opportunities
        if o.get("verdict") == "open" and not (o.get("supporting_evidence") or [])
    )
    open_count = verdicts.get("open", 0)

    with_evidence = sum(
        1
        for o in opportunities
        if (o.get("supporting_evidence") or []) or (o.get("contradicting_evidence") or [])
    )

    # Citation integrity: a cited work must carry a title and a URL, or the
    # claim cannot be followed back to its source.
    cited = broken = 0
    for opportunity in opportunities:
        for key in ("supporting_evidence", "contradicting_evidence", "recent_evidence"):
            for work in opportunity.get(key) or []:
                cited += 1
                if not work.get("url") or not work.get("title"):
                    broken += 1

    # Duplicate rate in retrieval, by OpenAlex id and by normalised title.
    ids = [p.get("id") for p in papers if p.get("id")]
    titles = [t for t in ((p.get("title") or "").strip().lower() for p in papers) if t]
    duplicates = (len(ids) - len(set(ids))) + (len(titles) - len(set(titles)))

    scores = [
        o.get("system_score") for o in opportunities
        if isinstance(o.get("system_score"), (int, float))
    ]
    ranking_sorted = scores == sorted(scores, reverse=True)

    return {
        "query": run.get("query"),
        "goal": run.get("goal"),
        "llm_calls": run.get("llm_calls"),
        "papers_returned": len(papers),
        "papers_analysed": analysed,
        "analysis_success_rate_pct": _pct(analysed, len(papers)),
        "retrieval_duplicate_count": duplicates,
        "candidate_gaps": candidates,
        "opportunities": len(opportunities),
        "gap_survival_rate_pct": _pct(len(opportunities), candidates),
        "verdicts": verdicts,
        # The headline: of the gaps validation could actually judge, how many
        # did it refuse to confirm as proposed?
        "validation_overturn_rate_pct": _pct(overturned, judged),
        "validated_judged": judged,
        "unverified_count": verdicts.get("unverified", 0),
        "unsupported_open_gaps": unsupported_open,
        "unsupported_open_rate_pct": _pct(unsupported_open, open_count),
        "evidence_coverage_pct": _pct(with_evidence, len(opportunities)),
        "citations_total": cited,
        "citations_broken": broken,
        "citation_integrity_pct": _pct(cited - broken, cited),
        "ranking_is_monotonic": ranking_sorted,
        "median_system_score": round(statistics.median(scores), 1) if scores else None,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals across runs. Rates are recomputed from counts, never averaged."""

    def total(key: str) -> int:
        return sum(r.get(key) or 0 for r in rows)

    judged = total("validated_judged")
    overturned = sum(
        sum(r["verdicts"].get(name, 0) for name in _OVERTURNED) for r in rows
    )
    open_total = sum(r["verdicts"].get("open", 0) for r in rows)

    return {
        "runs": len(rows),
        "papers_analysed": total("papers_analysed"),
        "candidate_gaps": total("candidate_gaps"),
        "opportunities": total("opportunities"),
        "validation_overturn_rate_pct": _pct(overturned, judged),
        "unsupported_open_rate_pct": _pct(total("unsupported_open_gaps"), open_total),
        "citation_integrity_pct": _pct(
            total("citations_total") - total("citations_broken"), total("citations_total")
        ),
        "ranking_monotonic_runs": sum(1 for r in rows if r["ranking_is_monotonic"]),
        "llm_calls": total("llm_calls"),
    }


def to_markdown(rows: list[dict[str, Any]], totals: dict[str, Any]) -> str:
    lines = [
        "# Discovery evaluation",
        "",
        "Computed from real runs. `null` means the metric had no denominator in",
        "this sample - it is not a zero.",
        "",
        f"- Runs: **{totals['runs']}**",
        f"- Papers analysed: **{totals['papers_analysed']}**",
        f"- Candidate gaps: **{totals['candidate_gaps']}** "
        f"-> opportunities: **{totals['opportunities']}**",
        f"- **Validation overturn rate: {totals['validation_overturn_rate_pct']}%** "
        "(of gaps validation could judge, the share it refused to confirm as proposed)",
        f"- Unsupported 'open' gaps: **{totals['unsupported_open_rate_pct']}%** "
        "(open verdict with no supporting evidence - lower is better)",
        f"- Citation integrity: **{totals['citation_integrity_pct']}%** "
        "(cited works carrying both a title and a URL)",
        f"- Ranking monotonic in {totals['ranking_monotonic_runs']}/{totals['runs']} runs",
        f"- Total LLM calls: **{totals['llm_calls']}**",
        "",
        "## Per run",
        "",
        "| Topic | Analysed | Gaps | Opps | Overturn % | Unsupported open | Evidence % |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['query']} | {row['papers_analysed']}/{row['papers_returned']} "
            f"| {row['candidate_gaps']} | {row['opportunities']} "
            f"| {row['validation_overturn_rate_pct']} | {row['unsupported_open_gaps']} "
            f"| {row['evidence_coverage_pct']} |"
        )
    return "\n".join(lines) + "\n"


def _run_topics(base_url: str, topics: list[str], max_papers: int) -> list[dict[str, Any]]:
    import httpx

    results = []
    with httpx.Client(base_url=base_url, timeout=1800) as client:
        for topic in topics:
            print(f"running: {topic}", flush=True)
            response = client.post(
                "/api/discover/run",
                json={"query": topic, "goal": "gaps", "max_papers": max_papers},
            )
            response.raise_for_status()
            results.append(response.json())
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-file", nargs="*", default=[], help="run result JSON files")
    parser.add_argument("--base-url", default=None, help="score live runs against this app")
    parser.add_argument("--topic", action="append", default=[], help="topic to run")
    parser.add_argument("--max-papers", type=int, default=4)
    args = parser.parse_args()

    runs: list[dict[str, Any]] = []
    for path in args.from_file:
        runs.append(json.loads(Path(path).read_text(encoding="utf-8")))
    if args.base_url:
        if not args.topic:
            print("--base-url needs at least one --topic", file=sys.stderr)
            return 2
        runs.extend(_run_topics(args.base_url, args.topic, args.max_papers))

    if not runs:
        print("nothing to score: pass --from-file or --base-url/--topic", file=sys.stderr)
        return 2

    rows = [score_run(run) for run in runs]
    totals = aggregate(rows)

    RESULTS_JSON.write_text(
        json.dumps({"totals": totals, "runs": rows}, indent=2), encoding="utf-8"
    )
    RESULTS_MD.write_text(to_markdown(rows, totals), encoding="utf-8")

    print(json.dumps(totals, indent=2))
    print(f"\nwrote {RESULTS_JSON.name} and {RESULTS_MD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
