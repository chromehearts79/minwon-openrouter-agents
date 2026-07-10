"""Strict, JSON-friendly contracts used by the minwon agent harness.

The LLM boundary must not rely on Python's permissive coercions.  In
particular, values such as ``"false"`` and arbitrary enum strings are rejected
instead of being silently converted.  Every artifact is immutable and exposes
``from_dict``/``to_dict`` for an explicit JSON boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from enum import Enum
import re
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import UUID, uuid4


class ContractValidationError(ValueError):
    """Raised when untrusted data does not conform to a contract."""


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    FAILED = "failed"


class Category(str, Enum):
    PERSONNEL = "인사"
    SERVICE = "복무"
    PAY = "보수·수당"
    TRAVEL = "여비"
    RECRUITMENT = "채용·시험"
    SYSTEM = "시스템"
    POLICY_OPINION = "정책의견"
    OTHER = "기타"


class Difficulty(str, Enum):
    HIGH = "상"
    MEDIUM = "중"
    LOW = "하"


def new_run_id() -> str:
    """Return a collision-resistant UUID suitable for filenames and events."""

    return str(uuid4())


def validate_run_id(value: object, *, path: str = "run_id") -> str:
    value = _strict_string(value, path=path, max_length=36)
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ContractValidationError(f"{path} must be a valid UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise ContractValidationError(f"{path} must use canonical lowercase UUID form")
    return value


@dataclass(frozen=True)
class RunInput:
    run_id: str
    request_id: str
    title: str
    body: str

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        _strict_string(self.request_id, path="request_id")
        _strict_string(self.title, path="title")
        _strict_string(self.body, path="body")

    @classmethod
    def from_dict(cls, value: object) -> "RunInput":
        data = _strict_object(
            value,
            required={"run_id", "request_id", "title", "body"},
            path="RunInput",
        )
        return cls(
            run_id=validate_run_id(data["run_id"]),
            request_id=_strict_string(data["request_id"], path="RunInput.request_id"),
            title=_strict_string(data["title"], path="RunInput.title"),
            body=_strict_string(data["body"], path="RunInput.body"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)


@dataclass(frozen=True)
class IntakeArtifact:
    """Validated input with raw and model-safe text kept explicitly separate."""

    run_id: str
    request_id: str
    original_title: str
    original_body: str
    masked_title: str
    masked_body: str
    pii_masked: bool

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        for name in (
            "request_id",
            "original_title",
            "original_body",
            "masked_title",
            "masked_body",
        ):
            _strict_string(getattr(self, name), path=name)
        _strict_bool(self.pii_masked, path="pii_masked")

    @classmethod
    def from_dict(cls, value: object) -> "IntakeArtifact":
        required = {
            "run_id",
            "request_id",
            "original_title",
            "original_body",
            "masked_title",
            "masked_body",
            "pii_masked",
        }
        data = _strict_object(value, required=required, path="IntakeArtifact")
        return cls(
            run_id=validate_run_id(data["run_id"]),
            request_id=_strict_string(data["request_id"], path="IntakeArtifact.request_id"),
            original_title=_strict_string(
                data["original_title"], path="IntakeArtifact.original_title"
            ),
            original_body=_strict_string(
                data["original_body"], path="IntakeArtifact.original_body"
            ),
            masked_title=_strict_string(
                data["masked_title"], path="IntakeArtifact.masked_title"
            ),
            masked_body=_strict_string(
                data["masked_body"], path="IntakeArtifact.masked_body"
            ),
            pii_masked=_strict_bool(data["pii_masked"], path="IntakeArtifact.pii_masked"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)


@dataclass(frozen=True)
class AnalysisArtifact:
    primary_category: Category
    secondary_categories: tuple[Category, ...]
    department: str
    difficulty: Difficulty
    sensitive: bool
    issues: tuple[str, ...]
    law_queries: tuple[str, ...]
    keywords: tuple[str, ...]

    def __post_init__(self) -> None:
        _strict_instance(self.primary_category, Category, "primary_category")
        _strict_enum_tuple(
            self.secondary_categories,
            Category,
            path="secondary_categories",
            max_items=3,
        )
        if self.primary_category in self.secondary_categories:
            raise ContractValidationError(
                "secondary_categories must not contain primary_category"
            )
        _ensure_unique(self.secondary_categories, path="secondary_categories")
        _strict_string(self.department, path="department", max_length=200)
        _strict_instance(self.difficulty, Difficulty, "difficulty")
        _strict_bool(self.sensitive, path="sensitive")
        _strict_string_tuple(self.issues, path="issues", min_items=1, max_items=5)
        _strict_string_tuple(self.law_queries, path="law_queries", max_items=4)
        _strict_string_tuple(self.keywords, path="keywords", max_items=8)

    @classmethod
    def from_dict(cls, value: object) -> "AnalysisArtifact":
        required = {
            "primary_category",
            "secondary_categories",
            "department",
            "difficulty",
            "sensitive",
            "issues",
            "law_queries",
            "keywords",
        }
        data = _strict_object(value, required=required, path="AnalysisArtifact")
        primary = _strict_enum(
            data["primary_category"], Category, path="AnalysisArtifact.primary_category"
        )
        secondary = _enum_list(
            data["secondary_categories"],
            Category,
            path="AnalysisArtifact.secondary_categories",
            max_items=3,
        )
        return cls(
            primary_category=primary,
            secondary_categories=secondary,
            department=_strict_string(
                data["department"], path="AnalysisArtifact.department", max_length=200
            ),
            difficulty=_strict_enum(
                data["difficulty"], Difficulty, path="AnalysisArtifact.difficulty"
            ),
            sensitive=_strict_bool(data["sensitive"], path="AnalysisArtifact.sensitive"),
            issues=_string_list(
                data["issues"], path="AnalysisArtifact.issues", min_items=1, max_items=5
            ),
            law_queries=_string_list(
                data["law_queries"], path="AnalysisArtifact.law_queries", max_items=4
            ),
            keywords=_string_list(
                data["keywords"], path="AnalysisArtifact.keywords", max_items=8
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)


_EVIDENCE_ID = re.compile(r"E[1-9][0-9]*\Z")


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    title: str
    source: str
    excerpt: str
    source_url: str
    checked_at: str
    matched_terms: tuple[str, ...]
    score: int

    def __post_init__(self) -> None:
        evidence_id = _strict_string(self.id, path="id", max_length=16)
        if not _EVIDENCE_ID.fullmatch(evidence_id):
            raise ContractValidationError("id must match E1, E2, ...")
        _strict_string(self.title, path="title", max_length=300)
        _strict_string(self.source, path="source", max_length=100)
        _strict_string(self.excerpt, path="excerpt", max_length=4_000)
        _strict_http_url(self.source_url, path="source_url")
        _strict_date(self.checked_at, path="checked_at")
        _strict_string_tuple(self.matched_terms, path="matched_terms", max_items=20)
        score = _strict_int(self.score, path="score")
        if score < 0:
            raise ContractValidationError("score must be zero or greater")

    @classmethod
    def from_dict(cls, value: object) -> "EvidenceItem":
        required = {
            "id",
            "title",
            "source",
            "excerpt",
            "source_url",
            "checked_at",
            "matched_terms",
            "score",
        }
        data = _strict_object(value, required=required, path="EvidenceItem")
        return cls(
            id=_strict_string(data["id"], path="EvidenceItem.id", max_length=16),
            title=_strict_string(data["title"], path="EvidenceItem.title", max_length=300),
            source=_strict_string(data["source"], path="EvidenceItem.source", max_length=100),
            excerpt=_strict_string(
                data["excerpt"], path="EvidenceItem.excerpt", max_length=4_000
            ),
            source_url=_strict_http_url(data["source_url"], path="EvidenceItem.source_url"),
            checked_at=_strict_date(data["checked_at"], path="EvidenceItem.checked_at"),
            matched_terms=_string_list(
                data["matched_terms"], path="EvidenceItem.matched_terms", max_items=20
            ),
            score=_strict_int(data["score"], path="EvidenceItem.score"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)


# Short compatibility name for callers that prefer the domain noun.
Evidence = EvidenceItem


@dataclass(frozen=True)
class EvidenceBundle:
    items: tuple[EvidenceItem, ...]
    insufficient: bool
    query_terms: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple:
            raise ContractValidationError("items must be a tuple")
        if len(self.items) > 3:
            raise ContractValidationError("items must contain at most 3 entries")
        for index, item in enumerate(self.items):
            _strict_instance(item, EvidenceItem, f"items[{index}]")
        _ensure_unique((item.id for item in self.items), path="items.id")
        _strict_bool(self.insufficient, path="insufficient")
        if not self.items and not self.insufficient:
            raise ContractValidationError("an empty evidence bundle must be insufficient")
        _strict_string_tuple(self.query_terms, path="query_terms", max_items=30)

    @classmethod
    def from_dict(cls, value: object) -> "EvidenceBundle":
        data = _strict_object(
            value,
            required={"items", "insufficient", "query_terms"},
            path="EvidenceBundle",
        )
        raw_items = _strict_list(data["items"], path="EvidenceBundle.items", max_items=3)
        return cls(
            items=tuple(EvidenceItem.from_dict(item) for item in raw_items),
            insufficient=_strict_bool(
                data["insufficient"], path="EvidenceBundle.insufficient"
            ),
            query_terms=_string_list(
                data["query_terms"], path="EvidenceBundle.query_terms", max_items=30
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)


@dataclass(frozen=True)
class DraftArtifact:
    text: str
    citations: tuple[str, ...]
    revision: int = 0

    def __post_init__(self) -> None:
        _strict_string(self.text, path="text", max_length=20_000)
        citations = _strict_string_tuple(self.citations, path="citations", max_items=20)
        _ensure_unique(citations, path="citations")
        for citation in citations:
            if not _EVIDENCE_ID.fullmatch(citation):
                raise ContractValidationError("citations entries must match E1, E2, ...")
        revision = _strict_int(self.revision, path="revision")
        if revision not in (0, 1):
            raise ContractValidationError("revision must be 0 or 1")

    @classmethod
    def from_dict(cls, value: object) -> "DraftArtifact":
        data = _strict_object(
            value,
            required={"text", "citations", "revision"},
            path="DraftArtifact",
        )
        return cls(
            text=_strict_string(data["text"], path="DraftArtifact.text", max_length=20_000),
            citations=_string_list(
                data["citations"], path="DraftArtifact.citations", max_items=20
            ),
            revision=_strict_int(data["revision"], path="DraftArtifact.revision"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)


@dataclass(frozen=True)
class GroundingReview:
    passed: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _strict_bool(self.passed, path="passed")
        _strict_string_tuple(self.reasons, path="reasons", max_items=20)
        if not self.passed and not self.reasons:
            raise ContractValidationError("a failed grounding review requires reasons")

    @classmethod
    def from_dict(cls, value: object) -> "GroundingReview":
        data = _strict_object(
            value, required={"passed", "reasons"}, path="GroundingReview"
        )
        return cls(
            passed=_strict_bool(data["passed"], path="GroundingReview.passed"),
            reasons=_string_list(
                data["reasons"], path="GroundingReview.reasons", max_items=20
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)


@dataclass(frozen=True)
class QualityCheck:
    criterion: str
    passed: bool
    comment: str

    def __post_init__(self) -> None:
        _strict_string(self.criterion, path="criterion", max_length=100)
        _strict_bool(self.passed, path="passed")
        _strict_string(self.comment, path="comment", max_length=1_000)

    @classmethod
    def from_dict(cls, value: object) -> "QualityCheck":
        data = _strict_object(
            value,
            required={"criterion", "passed", "comment"},
            path="QualityCheck",
        )
        return cls(
            criterion=_strict_string(
                data["criterion"], path="QualityCheck.criterion", max_length=100
            ),
            passed=_strict_bool(data["passed"], path="QualityCheck.passed"),
            comment=_strict_string(
                data["comment"], path="QualityCheck.comment", max_length=1_000
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)


@dataclass(frozen=True)
class QualityReview:
    passed: bool
    score: int
    checks: tuple[QualityCheck, ...]
    reasons: tuple[str, ...]
    suggested_final: str | None = None

    def __post_init__(self) -> None:
        _strict_bool(self.passed, path="passed")
        score = _strict_int(self.score, path="score")
        if not 0 <= score <= 100:
            raise ContractValidationError("score must be between 0 and 100")
        if type(self.checks) is not tuple or not self.checks:
            raise ContractValidationError("checks must be a non-empty tuple")
        for index, check in enumerate(self.checks):
            _strict_instance(check, QualityCheck, f"checks[{index}]")
        _strict_string_tuple(self.reasons, path="reasons", max_items=20)
        if self.passed and any(not check.passed for check in self.checks):
            raise ContractValidationError("passed cannot be true when a quality check failed")
        if not self.passed and not self.reasons:
            raise ContractValidationError("a failed quality review requires reasons")
        if self.suggested_final is not None:
            _strict_string(
                self.suggested_final, path="suggested_final", max_length=20_000
            )

    @classmethod
    def from_dict(cls, value: object) -> "QualityReview":
        data = _strict_object(
            value,
            required={"passed", "score", "checks", "reasons", "suggested_final"},
            path="QualityReview",
        )
        raw_checks = _strict_list(
            data["checks"], path="QualityReview.checks", min_items=1, max_items=20
        )
        raw_suggestion = data["suggested_final"]
        suggestion = None
        if raw_suggestion is not None:
            suggestion = _strict_string(
                raw_suggestion, path="QualityReview.suggested_final", max_length=20_000
            )
        return cls(
            passed=_strict_bool(data["passed"], path="QualityReview.passed"),
            score=_strict_int(data["score"], path="QualityReview.score"),
            checks=tuple(QualityCheck.from_dict(check) for check in raw_checks),
            reasons=_string_list(
                data["reasons"], path="QualityReview.reasons", max_items=20
            ),
            suggested_final=suggestion,
        )

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)


@dataclass(frozen=True)
class GateDecision:
    status: RunStatus
    passed: bool
    reasons: tuple[str, ...]
    allow_revision: bool = False

    def __post_init__(self) -> None:
        _strict_instance(self.status, RunStatus, "status")
        _strict_bool(self.passed, path="passed")
        _strict_string_tuple(self.reasons, path="reasons", max_items=30)
        _strict_bool(self.allow_revision, path="allow_revision")
        if self.passed != (self.status is RunStatus.COMPLETED):
            raise ContractValidationError(
                "passed must be true exactly when status is completed"
            )
        if not self.passed and not self.reasons:
            raise ContractValidationError("a blocked gate decision requires reasons")

    @classmethod
    def from_dict(cls, value: object) -> "GateDecision":
        data = _strict_object(
            value,
            required={"status", "passed", "reasons", "allow_revision"},
            path="GateDecision",
        )
        return cls(
            status=_strict_enum(data["status"], RunStatus, path="GateDecision.status"),
            passed=_strict_bool(data["passed"], path="GateDecision.passed"),
            reasons=_string_list(
                data["reasons"], path="GateDecision.reasons", max_items=30
            ),
            allow_revision=_strict_bool(
                data["allow_revision"], path="GateDecision.allow_revision"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus
    intake: IntakeArtifact | None
    analysis: AnalysisArtifact | None
    evidence: EvidenceBundle | None
    draft: DraftArtifact | None
    grounding_review: GroundingReview | None
    quality_review: QualityReview | None
    decision: GateDecision
    final: str | None

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        _strict_instance(self.status, RunStatus, "status")
        for name, expected in (
            ("intake", IntakeArtifact),
            ("analysis", AnalysisArtifact),
            ("evidence", EvidenceBundle),
            ("draft", DraftArtifact),
            ("grounding_review", GroundingReview),
            ("quality_review", QualityReview),
        ):
            value = getattr(self, name)
            if value is not None:
                _strict_instance(value, expected, name)
        _strict_instance(self.decision, GateDecision, "decision")
        if self.status is not self.decision.status:
            raise ContractValidationError("status must match decision.status")
        if self.status is RunStatus.COMPLETED:
            if self.final is None:
                raise ContractValidationError("completed results require final")
            _strict_string(self.final, path="final", max_length=20_000)
            required = (
                self.intake,
                self.analysis,
                self.evidence,
                self.draft,
                self.grounding_review,
                self.quality_review,
            )
            if any(item is None for item in required):
                raise ContractValidationError(
                    "completed results require all stage artifacts"
                )
        elif self.final is not None:
            raise ContractValidationError(
                "final must be null unless status is completed"
            )

    @classmethod
    def from_dict(cls, value: object) -> "RunResult":
        required = {
            "run_id",
            "status",
            "intake",
            "analysis",
            "evidence",
            "draft",
            "grounding_review",
            "quality_review",
            "decision",
            "final",
        }
        data = _strict_object(value, required=required, path="RunResult")
        final = data["final"]
        if final is not None:
            final = _strict_string(final, path="RunResult.final", max_length=20_000)
        return cls(
            run_id=validate_run_id(data["run_id"], path="RunResult.run_id"),
            status=_strict_enum(data["status"], RunStatus, path="RunResult.status"),
            intake=_optional_contract(data["intake"], IntakeArtifact),
            analysis=_optional_contract(data["analysis"], AnalysisArtifact),
            evidence=_optional_contract(data["evidence"], EvidenceBundle),
            draft=_optional_contract(data["draft"], DraftArtifact),
            grounding_review=_optional_contract(
                data["grounding_review"], GroundingReview
            ),
            quality_review=_optional_contract(data["quality_review"], QualityReview),
            decision=GateDecision.from_dict(data["decision"]),
            final=final,
        )

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)


def to_jsonable(value: object) -> Any:
    """Recursively turn contract values into JSON-compatible Python values."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def _dataclass_to_dict(value: object) -> dict[str, Any]:
    converted = to_jsonable(value)
    if not isinstance(converted, dict):  # pragma: no cover - internal invariant
        raise TypeError("contract did not serialize to an object")
    return converted


def _optional_contract(value: object, contract: type[Any]) -> Any:
    if value is None:
        return None
    return contract.from_dict(value)


def _strict_object(
    value: object,
    *,
    required: set[str],
    path: str,
    optional: set[str] | None = None,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{path} must be a JSON object")
    optional = optional or set()
    actual = set(value)
    non_string_keys = [key for key in actual if type(key) is not str]
    if non_string_keys:
        raise ContractValidationError(f"{path} keys must be strings")
    missing = sorted(required - actual)
    if missing:
        raise ContractValidationError(f"{path} is missing fields: {', '.join(missing)}")
    unexpected = sorted(actual - required - optional)
    if unexpected:
        raise ContractValidationError(
            f"{path} has unexpected fields: {', '.join(unexpected)}"
        )
    return value


def _strict_string(
    value: object,
    *,
    path: str,
    max_length: int | None = None,
) -> str:
    if type(value) is not str:
        raise ContractValidationError(f"{path} must be a string")
    if not value.strip():
        raise ContractValidationError(f"{path} must not be blank")
    if max_length is not None and len(value) > max_length:
        raise ContractValidationError(
            f"{path} must contain at most {max_length} characters"
        )
    return value


def _strict_bool(value: object, *, path: str) -> bool:
    if type(value) is not bool:
        raise ContractValidationError(f"{path} must be a JSON boolean")
    return value


def _strict_int(value: object, *, path: str) -> int:
    if type(value) is not int:
        raise ContractValidationError(f"{path} must be an integer")
    return value


def _strict_list(
    value: object,
    *,
    path: str,
    min_items: int = 0,
    max_items: int | None = None,
) -> list[object]:
    if type(value) is not list:
        raise ContractValidationError(f"{path} must be a JSON array")
    if len(value) < min_items:
        raise ContractValidationError(
            f"{path} must contain at least {min_items} entries"
        )
    if max_items is not None and len(value) > max_items:
        raise ContractValidationError(
            f"{path} must contain at most {max_items} entries"
        )
    return value


def _strict_enum(
    value: object, enum_type: type[Enum], *, path: str
) -> Any:
    if type(value) is not str:
        raise ContractValidationError(f"{path} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(str(member.value) for member in enum_type)
        raise ContractValidationError(
            f"{path} must be one of: {allowed}"
        ) from exc


def _string_list(
    value: object,
    *,
    path: str,
    min_items: int = 0,
    max_items: int | None = None,
) -> tuple[str, ...]:
    raw = _strict_list(
        value, path=path, min_items=min_items, max_items=max_items
    )
    result = tuple(
        _strict_string(item, path=f"{path}[{index}]")
        for index, item in enumerate(raw)
    )
    _ensure_unique(result, path=path)
    return result


def _enum_list(
    value: object,
    enum_type: type[Enum],
    *,
    path: str,
    min_items: int = 0,
    max_items: int | None = None,
) -> tuple[Any, ...]:
    raw = _strict_list(
        value, path=path, min_items=min_items, max_items=max_items
    )
    result = tuple(
        _strict_enum(item, enum_type, path=f"{path}[{index}]")
        for index, item in enumerate(raw)
    )
    _ensure_unique(result, path=path)
    return result


def _strict_string_tuple(
    value: object,
    *,
    path: str,
    min_items: int = 0,
    max_items: int | None = None,
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ContractValidationError(f"{path} must be a tuple")
    if len(value) < min_items:
        raise ContractValidationError(
            f"{path} must contain at least {min_items} entries"
        )
    if max_items is not None and len(value) > max_items:
        raise ContractValidationError(
            f"{path} must contain at most {max_items} entries"
        )
    for index, item in enumerate(value):
        _strict_string(item, path=f"{path}[{index}]")
    _ensure_unique(value, path=path)
    return value


def _strict_enum_tuple(
    value: object,
    enum_type: type[Enum],
    *,
    path: str,
    min_items: int = 0,
    max_items: int | None = None,
) -> tuple[Any, ...]:
    if type(value) is not tuple:
        raise ContractValidationError(f"{path} must be a tuple")
    if len(value) < min_items:
        raise ContractValidationError(
            f"{path} must contain at least {min_items} entries"
        )
    if max_items is not None and len(value) > max_items:
        raise ContractValidationError(
            f"{path} must contain at most {max_items} entries"
        )
    for index, item in enumerate(value):
        _strict_instance(item, enum_type, f"{path}[{index}]")
    return value


def _strict_instance(value: object, expected: type[Any], path: str) -> None:
    if type(value) is not expected:
        raise ContractValidationError(
            f"{path} must be {expected.__name__}, not {type(value).__name__}"
        )


def _ensure_unique(values: object, *, path: str) -> None:
    sequence = tuple(values)  # type: ignore[arg-type]
    if len(sequence) != len(set(sequence)):
        raise ContractValidationError(f"{path} must not contain duplicates")


def _strict_http_url(value: object, *, path: str) -> str:
    value = _strict_string(value, path=path, max_length=2_000)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ContractValidationError(f"{path} must be an absolute HTTP(S) URL")
    return value


def _strict_date(value: object, *, path: str) -> str:
    value = _strict_string(value, path=path, max_length=10)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ContractValidationError(f"{path} must be an ISO date (YYYY-MM-DD)") from exc
    if parsed.isoformat() != value:
        raise ContractValidationError(f"{path} must be an ISO date (YYYY-MM-DD)")
    return value
