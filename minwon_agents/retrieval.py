from __future__ import annotations

"""Local, deterministic evidence retrieval.

The catalog is intentionally a small educational index, not a copy of the
statutes and not a legal opinion.  A result is eligible only when the complaint
text itself contains a concrete catalog signal.  Categories and LLM-produced
law queries can improve the rank, but cannot create a match by themselves.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlparse

from .contracts import AnalysisArtifact, EvidenceBundle, EvidenceItem


DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "evidence_catalog.json"

# These words describe almost every complaint in this dataset.  They may be
# displayed in a catalog excerpt but never qualify an item as evidence alone.
_GENERAL_TERMS = {
    "공무원",
    "국가",
    "규정",
    "법령",
    "관련",
    "문의",
    "질의",
    "기준",
    "경우",
    "여부",
    "업무",
    "처리",
    "지급",
    "사항",
    "임용",
}


@dataclass(frozen=True)
class _CatalogEntry:
    id: str
    title: str
    source: str
    excerpt: str
    source_url: str
    checked_at: str
    categories: tuple[str, ...]
    terms: tuple[str, ...]


class EvidenceCatalog:
    """Validated in-memory view of ``data/evidence_catalog.json``."""

    def __init__(self, entries: Sequence[_CatalogEntry], *, version: str, notice: str) -> None:
        if not entries:
            raise ValueError("evidence catalog must contain at least one item")
        ids = [entry.id for entry in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence catalog ids must be unique")
        self._entries = tuple(entries)
        self.version = version
        self.notice = notice

    @property
    def entries(self) -> tuple[_CatalogEntry, ...]:
        return self._entries

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CATALOG_PATH) -> "EvidenceCatalog":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if type(raw) is not dict:
            raise ValueError("evidence catalog root must be an object")
        version = _required_string(raw, "catalog_version")
        notice = _required_string(raw, "notice")
        raw_items = raw.get("items")
        if type(raw_items) is not list:
            raise ValueError("evidence catalog items must be a list")

        entries: list[_CatalogEntry] = []
        for index, item in enumerate(raw_items):
            if type(item) is not dict:
                raise ValueError(f"evidence catalog items[{index}] must be an object")
            entry = _CatalogEntry(
                id=_required_string(item, "id", index),
                title=_required_string(item, "title", index),
                source=_required_string(item, "source", index),
                excerpt=_required_string(item, "excerpt", index),
                source_url=_required_string(item, "source_url", index),
                checked_at=_required_string(item, "checked_at", index),
                categories=_string_tuple(item.get("categories"), "categories", index),
                terms=_string_tuple(item.get("terms"), "terms", index),
            )
            _validate_entry(entry, index)
            entries.append(entry)
        return cls(entries, version=version, notice=notice)

    def search(
        self,
        text: str,
        analysis: AnalysisArtifact | Mapping[str, object] | None = None,
        *,
        limit: int = 3,
    ) -> list[EvidenceItem]:
        """Return up to three evidence candidates ranked against original text.

        ``analysis`` supplies a small ranking boost only.  An incorrect category
        or a hallucinated ``law_queries`` value cannot select an item unless a
        distinctive term is also present in ``text``.
        """

        if type(limit) is not int or not 1 <= limit <= 3:
            raise ValueError("limit must be an integer between 1 and 3")
        compact_text = _compact(text)
        if not compact_text:
            return []

        categories = _analysis_categories(analysis)
        law_queries = _analysis_strings(analysis, "law_queries")
        analysis_keywords = _analysis_strings(analysis, "keywords")
        scored: list[tuple[int, int, EvidenceItem]] = []

        for order, entry in enumerate(self._entries):
            matched: list[str] = []
            score = 0
            title_match = _compact(entry.title) in compact_text
            if title_match:
                score += 14
                matched.append(entry.title)

            for term in entry.terms:
                compact_term = _compact(term)
                if not compact_term or _normalize(term) in _GENERAL_TERMS:
                    continue
                if compact_term not in compact_text:
                    continue
                if term not in matched:
                    matched.append(term)
                    score += _direct_term_score(compact_term)

            # Hard eligibility gate: metadata produced by a classifier cannot
            # substitute for a term actually found in the complaint.
            if not matched:
                continue

            if categories.intersection(entry.categories):
                score += 2

            for query in law_queries:
                if _same_or_contains(query, entry.title):
                    score += 4
                    break

            # Keywords add only a small tie-breaking boost and must themselves
            # occur in the original text and in this catalog entry.
            entry_haystack = _compact(f"{entry.title} {' '.join(entry.terms)}")
            for keyword in analysis_keywords:
                compact_keyword = _compact(keyword)
                if (
                    compact_keyword
                    and _normalize(keyword) not in _GENERAL_TERMS
                    and compact_keyword in compact_text
                    and compact_keyword in entry_haystack
                ):
                    score += 1

            # Six points corresponds to one concrete domain term plus a category
            # match, or a sufficiently specific phrase without metadata help.
            if score < 6:
                continue
            evidence = EvidenceItem(
                id=entry.id,
                title=entry.title,
                source=entry.source,
                excerpt=entry.excerpt,
                source_url=entry.source_url,
                checked_at=entry.checked_at,
                matched_terms=tuple(matched[:20]),
                score=score,
            )
            scored.append((score, order, evidence))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item for _, _, item in scored[:limit]]


def retrieve_evidence(
    text: str,
    analysis: AnalysisArtifact | Mapping[str, object] | None = None,
    *,
    limit: int = 3,
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
) -> EvidenceBundle:
    """Search the local catalog and return the pipeline's strict bundle type."""

    catalog = EvidenceCatalog.load(catalog_path)
    items = catalog.search(text, analysis, limit=limit)
    query_terms = _query_terms(analysis)
    return EvidenceBundle(
        items=tuple(items),
        insufficient=not items,
        query_terms=tuple(query_terms[:30]),
    )


def _validate_entry(entry: _CatalogEntry, index: int) -> None:
    if not re.fullmatch(r"E[1-9][0-9]*", entry.id):
        raise ValueError(f"evidence catalog items[{index}].id must match E1, E2, ...")
    parsed = urlparse(entry.source_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"evidence catalog items[{index}].source_url must be an HTTPS URL")
    if parsed.netloc not in {
        "www.law.go.kr",
        "law.go.kr",
        "www.gosi.kr",
        "gosi.kr",
        "gongmuwon.gosi.kr",
    }:
        raise ValueError(f"evidence catalog items[{index}].source_url must use an official domain")
    try:
        date.fromisoformat(entry.checked_at)
    except ValueError as exc:
        raise ValueError(
            f"evidence catalog items[{index}].checked_at must be YYYY-MM-DD"
        ) from exc
    if not entry.terms:
        raise ValueError(f"evidence catalog items[{index}].terms must not be empty")


def _required_string(value: Mapping[str, object], key: str, index: int | None = None) -> str:
    raw = value.get(key)
    path = f"evidence catalog items[{index}].{key}" if index is not None else key
    if type(raw) is not str or not raw.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return raw.strip()


def _string_tuple(value: object, key: str, index: int) -> tuple[str, ...]:
    if type(value) is not list or not value:
        raise ValueError(f"evidence catalog items[{index}].{key} must be a non-empty list")
    cleaned: list[str] = []
    for item_index, item in enumerate(value):
        if type(item) is not str or not item.strip():
            raise ValueError(
                f"evidence catalog items[{index}].{key}[{item_index}] must be a non-empty string"
            )
        if item.strip() not in cleaned:
            cleaned.append(item.strip())
    return tuple(cleaned)


def _analysis_value(
    analysis: AnalysisArtifact | Mapping[str, object] | None, field: str
) -> object:
    if analysis is None:
        return None
    if isinstance(analysis, Mapping):
        return analysis.get(field)
    return getattr(analysis, field, None)


def _analysis_categories(
    analysis: AnalysisArtifact | Mapping[str, object] | None,
) -> set[str]:
    values: list[object] = []
    primary = _analysis_value(analysis, "primary_category")
    if primary is not None:
        values.append(primary)
    secondary = _analysis_value(analysis, "secondary_categories")
    if isinstance(secondary, (list, tuple)):
        values.extend(secondary)
    return {_enum_string(value) for value in values if _enum_string(value)}


def _analysis_strings(
    analysis: AnalysisArtifact | Mapping[str, object] | None, field: str
) -> tuple[str, ...]:
    raw = _analysis_value(analysis, field)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(value).strip() for value in raw if str(value).strip())


def _query_terms(
    analysis: AnalysisArtifact | Mapping[str, object] | None,
) -> list[str]:
    out: list[str] = []
    for field in ("law_queries", "keywords", "issues"):
        for value in _analysis_strings(analysis, field):
            if value not in out:
                out.append(value)
    return out


def _enum_string(value: object) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value).strip() if enum_value is not None else ""


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _compact(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


def _same_or_contains(left: str, right: str) -> bool:
    left_compact = _compact(left)
    right_compact = _compact(right)
    return bool(left_compact and right_compact) and (
        left_compact in right_compact or right_compact in left_compact
    )


def _direct_term_score(compact_term: str) -> int:
    if len(compact_term) >= 8:
        return 8
    if len(compact_term) >= 4:
        return 6
    return 5
