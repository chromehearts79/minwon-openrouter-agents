"""Deterministic policy gate for the complaint-answer harness.

The LLM reviewers produce observations.  This module alone decides whether a
draft may become a final answer.  Keeping that decision in ordinary Python
makes the same inputs produce the same route and prevents a reviewer from
silently promoting its own output.

The gate deliberately uses a small amount of duck typing.  Production callers
should pass the frozen artifacts from :mod:`minwon_agents.contracts`, while
tests and adapters may pass mappings or compatible objects during migration.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

try:
    from .contracts import GateDecision, GroundingReview, RunStatus
except ImportError:  # pragma: no cover - only used while contracts are bootstrapped
    class RunStatus(str, Enum):
        RUNNING = "running"
        COMPLETED = "completed"
        HUMAN_REVIEW_REQUIRED = "human_review_required"
        FAILED = "failed"

    @dataclass(frozen=True)
    class GroundingReview:
        passed: bool
        reasons: tuple[str, ...]

    @dataclass(frozen=True)
    class GateDecision:
        status: RunStatus
        passed: bool
        reasons: tuple[str, ...]
        allow_revision: bool = False


QUALITY_SCORE_THRESHOLD = 80
MAX_REVISIONS = 1

_CITATION_RE = re.compile(r"\[([Ee][A-Za-z0-9_-]*)\]")
_MISSING = object()


def validate_citations(draft_text: str, evidence_ids: Iterable[str]) -> GroundingReview:
    """Check that a non-empty draft cites only evidence supplied to the run.

    At least one ``[E...]`` citation is required.  The result contains stable
    machine-readable reason codes so the CLI, web UI, and tests can present the
    same explanation.
    """

    text = draft_text.strip() if isinstance(draft_text, str) else ""
    known_ids = {
        str(evidence_id).strip().upper()
        for evidence_id in evidence_ids
        if isinstance(evidence_id, str) and evidence_id.strip()
    }
    referenced_ids = tuple(
        dict.fromkeys(match.upper() for match in _CITATION_RE.findall(text))
    )

    reasons: list[str] = []
    if not text:
        reasons.append("EMPTY_DRAFT")
    if not known_ids:
        reasons.append("INSUFFICIENT_EVIDENCE")
    if text and not referenced_ids:
        reasons.append("MISSING_CITATION")

    for evidence_id in referenced_ids:
        if evidence_id not in known_ids:
            reasons.append(f"INVALID_CITATION:{evidence_id}")

    return GroundingReview(passed=not reasons, reasons=tuple(reasons))


def decide(
    analysis: object,
    evidence: object,
    draft: object,
    grounding: object,
    quality: object,
    revision_count: int = 0,
) -> GateDecision:
    """Return the deterministic route for one set of harness artifacts.

    Decision order:

    * malformed or missing required artifacts -> ``failed``;
    * sensitive, high-difficulty, or evidence-poor cases -> human review;
    * a recoverable review/citation failure -> one revision while ``running``;
    * the same failure after one revision -> human review;
    * only a fully passing set -> ``completed``.

    A ``running`` decision with ``allow_revision=True`` is not a publishable
    result.  It is an internal instruction to rewrite and re-run both reviews.
    """

    malformed: list[str] = []

    if (
        not isinstance(revision_count, int)
        or isinstance(revision_count, bool)
        or not 0 <= revision_count <= MAX_REVISIONS
    ):
        malformed.append("INVALID_REVISION_COUNT")
        revision_count = 0

    draft_revision = _read(draft, "revision", default=0)
    if (
        not isinstance(draft_revision, int)
        or isinstance(draft_revision, bool)
        or not 0 <= draft_revision <= MAX_REVISIONS
    ):
        malformed.append("INVALID_DRAFT_REVISION")
        draft_revision = 0
    effective_revision = max(revision_count, draft_revision)

    sensitive = _read(analysis, "sensitive")
    difficulty = _read(analysis, "difficulty")
    if not isinstance(sensitive, bool):
        malformed.append("INVALID_ANALYSIS_SENSITIVE")
    if difficulty is _MISSING or not _scalar_text(difficulty):
        malformed.append("INVALID_ANALYSIS_DIFFICULTY")

    draft_text = _draft_text(draft)
    if not draft_text:
        malformed.append("EMPTY_DRAFT")

    grounding_passed = _read(grounding, "passed")
    if not isinstance(grounding_passed, bool):
        malformed.append("INVALID_GROUNDING_REVIEW")

    quality_passed = _read(quality, "passed")
    quality_score = _read(quality, "score")
    if not isinstance(quality_passed, bool):
        malformed.append("INVALID_QUALITY_REVIEW")
    if (
        not isinstance(quality_score, int)
        or isinstance(quality_score, bool)
        or not 0 <= quality_score <= 100
    ):
        malformed.append("INVALID_QUALITY_SCORE")

    checks = _read(quality, "checks", default=())
    if not isinstance(checks, (tuple, list)):
        malformed.append("INVALID_QUALITY_CHECKS")
        checks = ()
    elif not checks:
        malformed.append("MISSING_QUALITY_CHECKS")
    check_failures: list[str] = []
    for index, check in enumerate(checks):
        check_passed = _read(check, "passed")
        if not isinstance(check_passed, bool):
            malformed.append(f"INVALID_QUALITY_CHECK:{index}")
            continue
        if not check_passed:
            criterion = _scalar_text(_read(check, "criterion", default="unknown")) or "unknown"
            check_failures.append(f"QUALITY_CHECK_FAILED:{criterion}")

    items, evidence_shape_error = _evidence_items(evidence)
    if evidence_shape_error:
        malformed.append(evidence_shape_error)
    evidence_ids, missing_id = _evidence_ids(items)
    if missing_id:
        malformed.append("MISSING_EVIDENCE_ID")

    insufficient = _read(evidence, "insufficient", default=not items)
    if not isinstance(insufficient, bool):
        malformed.append("INVALID_EVIDENCE_SUFFICIENCY")
        insufficient = True

    if malformed:
        return _decision(
            "failed",
            passed=False,
            reasons=_unique(malformed),
            allow_revision=False,
        )

    manual_reasons: list[str] = []
    if sensitive:
        manual_reasons.append("SENSITIVE_CASE")
    if _is_high_difficulty(difficulty):
        manual_reasons.append("HIGH_DIFFICULTY")
    if insufficient or not evidence_ids:
        manual_reasons.append("INSUFFICIENT_EVIDENCE")

    # A supplied reviewer result is never trusted as the only citation check.
    automatic_grounding = validate_citations(draft_text, evidence_ids)
    review_reasons: list[str] = []
    if grounding_passed is False:
        review_reasons.append("GROUNDING_REVIEW_FAILED")
        review_reasons.extend(_review_reasons(grounding))
    if not automatic_grounding.passed:
        review_reasons.extend(automatic_grounding.reasons)

    declared_citations = _read(draft, "citations", default=_MISSING)
    if declared_citations is not _MISSING:
        if not isinstance(declared_citations, (tuple, list)) or any(
            not isinstance(value, str) for value in declared_citations
        ):
            return _decision(
                "failed",
                passed=False,
                reasons=("INVALID_DRAFT_CITATIONS",),
                allow_revision=False,
            )
        declared = {value.strip().upper() for value in declared_citations if value.strip()}
        embedded = {match.upper() for match in _CITATION_RE.findall(draft_text)}
        if declared != embedded:
            review_reasons.append("CITATION_METADATA_MISMATCH")

    if quality_passed is False:
        review_reasons.append("QUALITY_REVIEW_FAILED")
        review_reasons.extend(_review_reasons(quality))
    if quality_score < QUALITY_SCORE_THRESHOLD:
        review_reasons.append(
            f"QUALITY_SCORE_BELOW_THRESHOLD:{quality_score}<{QUALITY_SCORE_THRESHOLD}"
        )
    review_reasons.extend(check_failures)

    # Manual-review conditions are not repairable by another model call.
    if manual_reasons:
        return _decision(
            "human_review_required",
            passed=False,
            reasons=_unique(manual_reasons + review_reasons),
            allow_revision=False,
        )

    if review_reasons:
        if effective_revision < MAX_REVISIONS:
            return _decision(
                "running",
                passed=False,
                reasons=_unique(["REVISION_REQUIRED", *review_reasons]),
                allow_revision=True,
            )
        return _decision(
            "human_review_required",
            passed=False,
            reasons=_unique(["MAX_REVISIONS_REACHED", *review_reasons]),
            allow_revision=False,
        )

    return _decision("completed", passed=True, reasons=(), allow_revision=False)


def final_for(decision: GateDecision, draft: object) -> str | None:
    """Return final text only for an internally consistent completed decision."""

    status = _enum_value(_read(decision, "status", default=""))
    passed = _read(decision, "passed", default=False)
    if status != "completed" or passed is not True:
        return None
    return _draft_text(draft) or None


class PolicyGate:
    """Object-oriented facade for orchestration code that models stages as nodes."""

    def decide(
        self,
        analysis: object,
        evidence: object,
        draft: object,
        grounding: object,
        quality: object,
        revision_count: int = 0,
    ) -> GateDecision:
        return decide(
            analysis,
            evidence,
            draft,
            grounding,
            quality,
            revision_count=revision_count,
        )

    @staticmethod
    def final_for(decision: GateDecision, draft: object) -> str | None:
        return final_for(decision, draft)


def _read(value: object, key: str, *, default: object = _MISSING) -> object:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _draft_text(draft: object) -> str:
    if isinstance(draft, str):
        return draft.strip()
    for key in ("text", "draft", "content"):
        value = _read(draft, key)
        if isinstance(value, str):
            return value.strip()
    return ""


def _evidence_items(evidence: object) -> tuple[list[object], str | None]:
    if isinstance(evidence, (tuple, list)):
        return list(evidence), None
    for key in ("items", "evidence", "evidences"):
        value = _read(evidence, key)
        if value is not _MISSING:
            if isinstance(value, (tuple, list)):
                return list(value), None
            return [], "INVALID_EVIDENCE_ITEMS"
    if evidence is None:
        return [], None
    return [], "INVALID_EVIDENCE_BUNDLE"


def _evidence_ids(items: list[object]) -> tuple[tuple[str, ...], bool]:
    ids: list[str] = []
    missing = False
    for item in items:
        value = _read(item, "id")
        if value is _MISSING:
            value = _read(item, "evidence_id")
        if not isinstance(value, str) or not value.strip():
            missing = True
            continue
        ids.append(value.strip().upper())
    return tuple(dict.fromkeys(ids)), missing


def _review_reasons(review: object) -> list[str]:
    values = _read(review, "reasons", default=())
    if not isinstance(values, (tuple, list)):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _is_high_difficulty(value: object) -> bool:
    text = _enum_value(value).strip()
    name = getattr(value, "name", "")
    return text in {"상", "high", "HIGH"} or str(name).upper() == "HIGH"


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _scalar_text(value: object) -> str:
    if value is _MISSING or value is None:
        return ""
    return _enum_value(value).strip()


def _decision(
    status_value: str,
    *,
    passed: bool,
    reasons: tuple[str, ...],
    allow_revision: bool,
) -> GateDecision:
    try:
        status = RunStatus(status_value)
    except (TypeError, ValueError):  # support enums whose member names are canonical
        status = getattr(RunStatus, status_value.upper())
    return GateDecision(
        status=status,
        passed=passed,
        reasons=reasons,
        allow_revision=allow_revision,
    )


def _unique(reasons: Iterable[str]) -> tuple[str, ...]:
    # GateDecision's strict contract allows at most 30 reasons.  Keep the first
    # occurrence so policy-level route codes are retained ahead of reviewer
    # detail when an unusually verbose review reaches the boundary.
    return tuple(dict.fromkeys(reason for reason in reasons if reason))[:30]


__all__ = [
    "MAX_REVISIONS",
    "QUALITY_SCORE_THRESHOLD",
    "PolicyGate",
    "decide",
    "final_for",
    "validate_citations",
]
