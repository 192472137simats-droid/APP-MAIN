"""Evaluation harness for ResearchAdvisorLM.

Runs the dataset through a live instance and scores four things:

  1. Extraction accuracy   - year, datasets, method terms
  2. Summary faithfulness  - numbers in the summary that appear in the paper
  3. Gap yield             - gaps produced, and how many survive grounding as open
  4. Grounding effect      - how many "open" gaps grounding overturns

Only (4) is the contribution claim, and it is computed, not asserted: the same
gaps are judged with and without literature checking, and the difference is
reported.

Usage:
    python eval/run.py                              # against localhost:8000
    python eval/run.py --base-url https://your.app  # against a deployment
    python eval/run.py --limit 3                    # quick smoke run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset.json"
RESULTS_JSON = ROOT / "results.json"
RESULTS_MD = ROOT / "results.md"
REVIEW_CSV = ROOT / "gaps_to_review.csv"

# Numbers that carry no claim - ignore them when checking faithfulness.
_TRIVIAL = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "100"}


def _numbers(text: str) -> set[str]:
    return {n for n in re.findall(r"\d+\.\d+|\d{2,}", text or "") if n not in _TRIVIAL}


def _contains(haystack: str, needle: str) -> bool:
    return needle.lower().strip() in (haystack or "").lower()


class Runner:
    def __init__(self, base_url: str, timeout: float = 600.0) -> None:
        self.base = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Retry transient failures.

        A run takes minutes, and a redeploy mid-run produces 502s while the
        container restarts. Without this, one restart voids the whole run.
        """
        last: Exception | None = None
        for attempt in range(4):
            try:
                response = self.client.request(method, f"{self.base}{path}", **kwargs)
                if response.status_code in (502, 503, 504):
                    last = RuntimeError(f"HTTP {response.status_code} (server restarting?)")
                    time.sleep(15 * (attempt + 1))
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as exc:
                last = exc
                time.sleep(10 * (attempt + 1))
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"{exc.response.status_code} {exc.response.text[:200]}"
                ) from exc
            except httpx.RequestError as exc:
                last = exc
                time.sleep(10 * (attempt + 1))
        raise RuntimeError(f"gave up after retries: {last}")

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json=payload)

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def evaluate(self, entry: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        record: dict[str, Any] = {"arxiv": entry["arxiv"], "title": entry["title"]}

        imported = self._post("/api/import", {"url": entry["arxiv"]})
        paper_id = imported["paper_id"]
        analysis = imported["analysis"]
        record["paper_id"] = paper_id
        record["import_seconds"] = round(time.time() - started, 1)

        # ---- 1. extraction accuracy -------------------------------------
        metadata = analysis.get("metadata") or {}
        year_ok = metadata.get("year") == entry["year"]

        found_datasets = " | ".join(analysis.get("datasets") or [])
        expected = entry.get("datasets") or []
        hit = [d for d in expected if _contains(found_datasets, d)]
        dataset_recall = len(hit) / len(expected) if expected else 1.0

        method_text = f"{analysis.get('method', '')} {analysis.get('objective', '')}"
        terms = entry.get("method_terms") or []
        term_hits = [t for t in terms if _contains(method_text, t)]
        method_recall = len(term_hits) / len(terms) if terms else 1.0

        record["extraction"] = {
            "year_expected": entry["year"],
            "year_found": metadata.get("year"),
            "year_ok": year_ok,
            "datasets_expected": expected,
            "datasets_found": analysis.get("datasets") or [],
            "dataset_recall": round(dataset_recall, 3),
            "method_terms_missing": [t for t in terms if t not in term_hits],
            "method_recall": round(method_recall, 3),
            "authors_found": len(metadata.get("authors") or []),
        }

        # ---- 2. summary faithfulness ------------------------------------
        detail = self._get(f"/api/papers/{paper_id}", include_text=True)
        full_text = detail.get("full_text", "")
        summary_text = " ".join((analysis.get("summary") or {}).values())
        claimed = _numbers(summary_text)
        unsupported = sorted(n for n in claimed if n not in full_text)
        record["faithfulness"] = {
            "numbers_in_summary": len(claimed),
            "unsupported_numbers": unsupported,
            "faithful": not unsupported,
        }

        # ---- 3 + 4. gaps, before and after grounding --------------------
        ungrounded = analysis.get("gaps") or []
        grounding = self._post(f"/api/papers/{paper_id}/ground", {})
        grounded = grounding.get("gaps") or []

        verdicts: dict[str, int] = {}
        for gap in grounded:
            verdicts[gap.get("verdict", "unverified")] = (
                verdicts.get(gap.get("verdict", "unverified"), 0) + 1
            )

        # Without grounding every gap is implicitly "open" - that is the whole
        # weakness of single-paper gap finding. Count what grounding overturns.
        overturned = sum(1 for g in grounded if g.get("verdict") in ("addressed", "partial"))
        with_citations = sum(1 for g in grounded if g.get("evidence"))

        # Citations must postdate the paper, and "open" must carry no evidence.
        paper_year = grounding.get("paper_year")
        bad_citations = 0
        contradictions = 0
        for gap in grounded:
            for work in gap.get("evidence") or []:
                if work.get("year") and paper_year and work["year"] < paper_year:
                    bad_citations += 1
            if gap.get("verdict") == "open" and gap.get("evidence"):
                contradictions += 1

        record["gaps"] = {
            "count": len(grounded),
            "meets_min": len(grounded) >= entry.get("min_gaps", 0),
            "ungrounded_count": len(ungrounded),
            "verdicts": verdicts,
            "overturned_by_grounding": overturned,
            "overturn_rate": round(overturned / len(grounded), 3) if grounded else 0.0,
            "gaps_with_citations": with_citations,
            "corpus_size": grounding.get("corpus_size", 0),
            "matched_in_openalex": grounding.get("matched_paper", False),
        }
        record["integrity"] = {
            "citations_predating_paper": bad_citations,
            "open_verdicts_with_evidence": contradictions,
            "clean": bad_citations == 0 and contradictions == 0,
        }
        record["gap_details"] = [
            {
                "id": g.get("id"),
                "title": g.get("title"),
                "verdict": g.get("verdict"),
                "confidence": g.get("confidence"),
                "citations": len(g.get("evidence") or []),
            }
            for g in grounded
        ]
        record["total_seconds"] = round(time.time() - started, 1)
        return record


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "extraction" in r]
    if not ok:
        return {}

    total_gaps = sum(r["gaps"]["count"] for r in ok)
    overturned = sum(r["gaps"]["overturned_by_grounding"] for r in ok)

    return {
        "papers_evaluated": len(ok),
        "papers_failed": len(records) - len(ok),
        "year_accuracy": round(sum(r["extraction"]["year_ok"] for r in ok) / len(ok), 3),
        "dataset_recall": round(
            sum(r["extraction"]["dataset_recall"] for r in ok) / len(ok), 3
        ),
        "method_recall": round(
            sum(r["extraction"]["method_recall"] for r in ok) / len(ok), 3
        ),
        "authors_found_rate": round(
            sum(1 for r in ok if r["extraction"]["authors_found"] > 0) / len(ok), 3
        ),
        "summary_faithful_rate": round(
            sum(r["faithfulness"]["faithful"] for r in ok) / len(ok), 3
        ),
        "total_gaps": total_gaps,
        "gaps_per_paper": round(total_gaps / len(ok), 2),
        "overturned_by_grounding": overturned,
        "grounding_overturn_rate": round(overturned / total_gaps, 3) if total_gaps else 0.0,
        "mean_corpus_size": round(
            sum(r["gaps"]["corpus_size"] for r in ok) / len(ok), 1
        ),
        "integrity_clean_rate": round(
            sum(r["integrity"]["clean"] for r in ok) / len(ok), 3
        ),
        "mean_seconds_per_paper": round(
            sum(r["total_seconds"] for r in ok) / len(ok), 1
        ),
    }


def write_markdown(summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    pct = lambda v: f"{v * 100:.0f}%"  # noqa: E731

    lines = [
        "# Evaluation results",
        "",
        f"Papers evaluated: **{summary['papers_evaluated']}**"
        + (f" (failed: {summary['papers_failed']})" if summary["papers_failed"] else ""),
        "",
        "## Headline",
        "",
        "| Metric | Result | What it measures |",
        "|---|---|---|",
        f"| Year accuracy | {pct(summary['year_accuracy'])} | Correct publication year extracted |",
        f"| Dataset recall | {pct(summary['dataset_recall'])} | Expected datasets found |",
        f"| Method recall | {pct(summary['method_recall'])} | Expected method terms present |",
        f"| Authors extracted | {pct(summary['authors_found_rate'])} | Papers with author list recovered |",
        f"| Summary faithfulness | {pct(summary['summary_faithful_rate'])} | Summaries with no invented numbers |",
        f"| Integrity clean | {pct(summary['integrity_clean_rate'])} | No pre-dating citations, no contradictory verdicts |",
        "",
        "## Grounding effect (the contribution)",
        "",
        f"Across {summary['total_gaps']} gaps ({summary['gaps_per_paper']} per paper), literature "
        f"grounding overturned **{summary['overturned_by_grounding']}** "
        f"(**{pct(summary['grounding_overturn_rate'])}**) that single-paper analysis would "
        "have reported as open research directions.",
        "",
        f"Mean corpus checked per paper: {summary['mean_corpus_size']} papers. "
        f"Mean runtime: {summary['mean_seconds_per_paper']}s per paper.",
        "",
        "## Per paper",
        "",
        "| Paper | Year | Datasets | Gaps | Open | Overturned | Faithful | Clean |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for record in records:
        if "extraction" not in record:
            lines.append(f"| {record['title'][:40]} | ERROR: {record.get('error', '')[:40]} | | | | | | |")
            continue
        gaps = record["gaps"]
        lines.append(
            f"| {record['title'][:40]} "
            f"| {'OK' if record['extraction']['year_ok'] else 'wrong'} "
            f"| {pct(record['extraction']['dataset_recall'])} "
            f"| {gaps['count']} "
            f"| {gaps['verdicts'].get('open', 0)} "
            f"| {gaps['overturned_by_grounding']} "
            f"| {'yes' if record['faithfulness']['faithful'] else 'NO'} "
            f"| {'yes' if record['integrity']['clean'] else 'NO'} |"
        )

    lines += [
        "",
        "## Gap precision (needs your judgement)",
        "",
        f"`{REVIEW_CSV.name}` lists every gap marked still-open. Read the paper, put "
        "`y` or `n` in the `is_real_gap` column, and gap precision is the share of `y`. "
        "This is the one number a human has to supply - everything above is computed.",
        "",
    ]

    RESULTS_MD.write_text("\n".join(lines), encoding="utf-8")


def write_review_csv(records: list[dict[str, Any]]) -> None:
    rows = ["paper,gap_id,verdict,confidence,gap_title,is_real_gap"]
    for record in records:
        for gap in record.get("gap_details", []):
            if gap.get("verdict") != "open":
                continue
            title = (gap.get("title") or "").replace('"', "'")
            rows.append(
                f'"{record["title"][:50]}",{gap["id"]},{gap["verdict"]},'
                f'{gap.get("confidence")},"{title}",'
            )
    REVIEW_CSV.write_text("\n".join(rows), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=0, help="evaluate only the first N")
    parser.add_argument("--dataset", default=str(DATASET))
    args = parser.parse_args()

    entries = json.loads(Path(args.dataset).read_text(encoding="utf-8"))["papers"]
    if args.limit:
        entries = entries[: args.limit]

    runner = Runner(args.base_url)
    records: list[dict[str, Any]] = []

    try:
        for index, entry in enumerate(entries, start=1):
            print(f"[{index}/{len(entries)}] {entry['title'][:60]} ...", flush=True)
            record: dict[str, Any] = {}
            # A paper can be lost entirely if the server restarts between its
            # import and its grounding call (the store is ephemeral), so give
            # the whole paper a second attempt from scratch.
            for attempt in range(2):
                try:
                    record = runner.evaluate(entry)
                    gaps = record["gaps"]
                    print(
                        f"      {gaps['count']} gaps, {gaps['overturned_by_grounding']} "
                        f"overturned, corpus {gaps['corpus_size']}, {record['total_seconds']}s",
                        flush=True,
                    )
                    break
                except Exception as exc:  # noqa: BLE001 - one bad paper must not stop the run
                    if attempt == 0:
                        print(f"      retrying after: {str(exc)[:90]}", flush=True)
                        time.sleep(20)
                        continue
                    print(f"      FAILED: {exc}", flush=True)
                    record = {
                        "arxiv": entry["arxiv"],
                        "title": entry["title"],
                        "error": str(exc),
                    }
            records.append(record)
    finally:
        runner.close()

    summary = summarise(records)
    if not summary:
        print("\nNo papers evaluated successfully.")
        RESULTS_JSON.write_text(
            json.dumps({"records": records}, indent=2), encoding="utf-8"
        )
        return 1

    RESULTS_JSON.write_text(
        json.dumps({"summary": summary, "records": records}, indent=2), encoding="utf-8"
    )
    write_markdown(summary, records)
    write_review_csv(records)

    print("\n" + "=" * 62)
    for key, value in summary.items():
        print(f"  {key:28} {value}")
    print("=" * 62)
    print(f"\nWrote {RESULTS_JSON.name}, {RESULTS_MD.name}, {REVIEW_CSV.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
