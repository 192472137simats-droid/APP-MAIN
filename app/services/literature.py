"""Literature grounding via OpenAlex.

This is what separates a gap *finder* from a limitations *paraphraser*: for a
direction the paper left open, we go and look at what was published afterwards.

OpenAlex is free, needs no API key, and covers ~250M works.
"""

import asyncio
import re
from typing import Any

import httpx

from ..config import GROUNDING_CORPUS_SIZE, OPENALEX_BASE, OPENALEX_MAILTO

_UA = "ResearchAdvisorLM/1.0 (academic capstone project)"


def _params(extra: dict[str, Any]) -> dict[str, Any]:
    params = dict(extra)
    if OPENALEX_MAILTO:
        params["mailto"] = OPENALEX_MAILTO
    return params


def invert_abstract(inverted: dict[str, list[int]] | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}. Put them back in order."""
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        for idx in idxs:
            positions.append((idx, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _work_to_dict(work: dict[str, Any], source: str) -> dict[str, Any]:
    abstract = invert_abstract(work.get("abstract_inverted_index"))
    authorships = work.get("authorships") or []
    primary = (work.get("primary_location") or {}).get("source") or {}
    return {
        "openalex_id": work.get("id", ""),
        "title": _clean(work.get("display_name") or "Untitled"),
        "year": work.get("publication_year"),
        "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
        "url": work.get("doi") or work.get("id") or "",
        "venue": _clean(primary.get("display_name") or ""),
        "citations": work.get("cited_by_count", 0),
        "authors": [
            _clean((a.get("author") or {}).get("display_name") or "")
            for a in authorships[:4]
        ],
        "abstract": _clean(abstract)[:1200],
        "source": source,
    }


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = await client.get(
            f"{OPENALEX_BASE}{path}", params=_params(params), headers={"User-Agent": _UA}
        )
        if response.status_code != 200:
            return {}
        return response.json()
    except (httpx.RequestError, ValueError):
        return {}


async def find_work(client: httpx.AsyncClient, title: str) -> dict[str, Any] | None:
    """Locate the uploaded paper itself in OpenAlex, by title."""
    if not title:
        return None
    data = await _get(
        client,
        "/works",
        {"filter": f"title.search:{title[:200]}", "per-page": 3},
    )
    results = data.get("results") or []
    if not results:
        return None

    # Guard against a loose match: require decent word overlap with the title.
    wanted = {w for w in re.findall(r"[a-z]{4,}", title.lower())}
    for work in results:
        got = {w for w in re.findall(r"[a-z]{4,}", (work.get("display_name") or "").lower())}
        if wanted and len(wanted & got) / len(wanted) >= 0.5:
            return work
    return None


async def citing_works(
    client: httpx.AsyncClient, openalex_id: str, limit: int = 25
) -> list[dict[str, Any]]:
    """Papers that cite this one - the most likely place a gap got closed."""
    if not openalex_id:
        return []
    short_id = openalex_id.rsplit("/", 1)[-1]
    data = await _get(
        client,
        "/works",
        {
            "filter": f"cites:{short_id}",
            "per-page": min(limit, 50),
            "sort": "cited_by_count:desc",
        },
    )
    return [_work_to_dict(w, "cites this paper") for w in data.get("results") or []]


async def search_recent(
    client: httpx.AsyncClient, query: str, from_year: int | None, limit: int = 12
) -> list[dict[str, Any]]:
    """Later work on the same topic, whether or not it cites the paper."""
    if not query:
        return []
    filters = []
    if from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")
    params: dict[str, Any] = {
        "search": query[:250],
        "per-page": min(limit, 50),
        "sort": "relevance_score:desc",
    }
    if filters:
        params["filter"] = ",".join(filters)
    data = await _get(client, "/works", params)
    return [_work_to_dict(w, "later work on this topic") for w in data.get("results") or []]


async def build_corpus(
    title: str,
    year: int | None,
    queries: list[str],
    corpus_size: int = GROUNDING_CORPUS_SIZE,
) -> dict[str, Any]:
    """Assemble the evidence pool a gap gets judged against."""
    async with httpx.AsyncClient(timeout=45) as client:
        self_work = await find_work(client, title)
        self_id = (self_work or {}).get("id", "")
        resolved_year = year or (self_work or {}).get("publication_year")

        tasks: list[Any] = []
        if self_id:
            tasks.append(citing_works(client, self_id, limit=25))
        for query in queries[:4]:
            tasks.append(search_recent(client, query, resolved_year, limit=10))

        results = await asyncio.gather(*tasks, return_exceptions=True)

    corpus: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        if isinstance(result, BaseException):
            continue
        for work in result:
            key = work["openalex_id"] or work["title"].lower()
            if key in seen or key == self_id:
                continue
            # Only later work can close a gap the paper left open.
            if resolved_year and work["year"] and work["year"] < resolved_year:
                continue
            seen.add(key)
            corpus.append(work)

    corpus.sort(key=lambda w: (w["citations"] or 0), reverse=True)
    corpus = corpus[:corpus_size]
    for index, work in enumerate(corpus, start=1):
        work["ref"] = index

    return {
        "matched_paper": bool(self_work),
        "matched_title": _clean((self_work or {}).get("display_name") or ""),
        "paper_year": resolved_year,
        "corpus": corpus,
        "corpus_size": len(corpus),
        "queries": queries[:4],
    }
