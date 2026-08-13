"""Citation string generation. Pure formatting - no model call needed."""

import re
from typing import Any


def _split_name(name: str) -> tuple[str, str]:
    """Return (given names, family name) from 'Kaiming He' or 'He, Kaiming'."""
    name = re.sub(r"\s+", " ", name).strip().strip(",")
    if not name:
        return "", ""
    if "," in name:
        family, _, given = name.partition(",")
        return given.strip(), family.strip()
    parts = name.split(" ")
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]


def _initials(given: str) -> str:
    return " ".join(f"{part[0]}." for part in given.split() if part)


def _ieee_author(name: str) -> str:
    given, family = _split_name(name)
    return f"{_initials(given)} {family}".strip()


def _apa_author(name: str) -> str:
    given, family = _split_name(name)
    return f"{family}, {_initials(given)}".strip().strip(",")


def _join(items: list[str], final: str = "and") -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])}, {final} {items[-1]}"


def _key(authors: list[str], year: Any, title: str) -> str:
    family = _split_name(authors[0])[1].lower() if authors else "unknown"
    family = re.sub(r"[^a-z]", "", family) or "unknown"
    word = re.sub(r"[^a-z]", "", (title.split() or ["paper"])[0].lower()) or "paper"
    return f"{family}{year or 'nd'}{word}"


def build(metadata: dict[str, Any], doi: str = "") -> dict[str, str]:
    """Every supported citation style for one paper."""
    authors: list[str] = [a for a in (metadata.get("authors") or []) if a.strip()]
    title = (metadata.get("title") or "Untitled").strip().rstrip(".")
    year = metadata.get("year")
    venue = (metadata.get("venue") or "").strip()
    year_text = str(year) if year else "n.d."

    # IEEE: K. He, X. Zhang, "Title," Venue, 2016.
    if not authors:
        ieee_authors = ""
    elif len(authors) > 6:
        # "et al." replaces the remaining names - it never takes an "and".
        ieee_authors = ", ".join(_ieee_author(a) for a in authors[:6]) + ", et al., "
    else:
        ieee_authors = _join([_ieee_author(a) for a in authors]) + ", "
    ieee = f'{ieee_authors}"{title}," ' + (f"{venue}, " if venue else "") + f"{year_text}."

    # APA: He, K., & Zhang, X. (2016). Title. Venue.
    if authors:
        apa_names = [_apa_author(a) for a in authors[:20]]
        apa_authors = (
            apa_names[0] if len(apa_names) == 1
            else f"{', '.join(apa_names[:-1])}, & {apa_names[-1]}"
        )
    else:
        apa_authors = ""
    apa = (f"{apa_authors} " if apa_authors else "") + f"({year_text}). {title}."
    if venue:
        apa += f" {venue}."
    if doi:
        apa += f" https://doi.org/{doi}"

    # MLA: He, Kaiming, et al. "Title." Venue, 2016.
    if authors:
        given, family = _split_name(authors[0])
        first = f"{family}, {given}".strip().strip(",")
        mla_authors = f"{first}, et al." if len(authors) > 2 else first
        if len(authors) == 2:
            g2, f2 = _split_name(authors[1])
            mla_authors = f"{first}, and {g2} {f2}".strip()
    else:
        mla_authors = ""
    # "et al." already ends in a period - don't add a second one.
    mla = (f"{mla_authors.rstrip('.')}. " if mla_authors else "") + f'"{title}." '
    if venue:
        mla += f"{venue}, "
    mla += f"{year_text}."

    # Chicago: He, Kaiming, and Xiangyu Zhang. "Title." Venue (2016).
    chicago_names = []
    for index, author in enumerate(authors[:10]):
        given, family = _split_name(author)
        chicago_names.append(
            f"{family}, {given}".strip().strip(",") if index == 0
            else f"{given} {family}".strip()
        )
    chicago = (f"{_join(chicago_names)}. " if chicago_names else "") + f'"{title}." '
    if venue:
        chicago += f"{venue} "
    chicago += f"({year_text})."

    bibtex_authors = " and ".join(
        f"{family}, {given}".strip().strip(",")
        for given, family in (_split_name(a) for a in authors)
    )
    bibtex = "\n".join(
        [
            f"@article{{{_key(authors, year, title)},",
            f"  title   = {{{title}}},",
            f"  author  = {{{bibtex_authors}}},",
            *([f"  journal = {{{venue}}},"] if venue else []),
            *([f"  year    = {{{year}}},"] if year else []),
            *([f"  doi     = {{{doi}}},"] if doi else []),
            "}",
        ]
    )

    return {
        "ieee": re.sub(r"\s+", " ", ieee).strip(),
        "apa": re.sub(r"\s+", " ", apa).strip(),
        "mla": re.sub(r"\s+", " ", mla).strip(),
        "chicago": re.sub(r"\s+", " ", chicago).strip(),
        "bibtex": bibtex,
    }
