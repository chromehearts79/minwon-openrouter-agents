"""Deterministic input validation and basic PII masking guardrails."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .contracts import (
    ContractValidationError,
    IntakeArtifact,
    RunInput,
    new_run_id,
    validate_run_id,
)


DEFAULT_MAX_REQUEST_ID_CHARS = 100
DEFAULT_MAX_TITLE_CHARS = 300
DEFAULT_MAX_BODY_CHARS = 20_000


class InputValidationError(ContractValidationError):
    """Raised when a raw civil-petition input is unsafe or incomplete."""


# The order matters: a 13-digit identity number is masked before the more
# general phone expression gets a chance to inspect it.
_IDENTITY_NUMBER_RE = re.compile(
    r"(?<!\d)\d{6}\s*[- ]?\s*[1-8]\d{6}(?!\d)"
)
_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?82[-.\s]?)?(?:0?1[016789]|0\d{1,2})"
    r"[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"
)

_MASK_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("identity_number", _IDENTITY_NUMBER_RE, "[ID_NUMBER]"),
    ("email", _EMAIL_RE, "[EMAIL]"),
    ("phone", _PHONE_RE, "[PHONE]"),
)


@dataclass(frozen=True)
class MaskingResult:
    text: str
    identity_numbers: int = 0
    emails: int = 0
    phones: int = 0

    @property
    def total(self) -> int:
        return self.identity_numbers + self.emails + self.phones

    @property
    def masked(self) -> bool:
        return self.total > 0


@dataclass(frozen=True)
class InputGuard:
    """Configurable, deterministic guard for a single minwon input."""

    max_request_id_chars: int = DEFAULT_MAX_REQUEST_ID_CHARS
    max_title_chars: int = DEFAULT_MAX_TITLE_CHARS
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS

    def __post_init__(self) -> None:
        for name in (
            "max_request_id_chars",
            "max_title_chars",
            "max_body_chars",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    def validate(
        self,
        *,
        request_id: object,
        title: object,
        body: object,
        run_id: object | None = None,
    ) -> RunInput:
        return validate_input(
            request_id=request_id,
            title=title,
            body=body,
            run_id=run_id,
            max_request_id_chars=self.max_request_id_chars,
            max_title_chars=self.max_title_chars,
            max_body_chars=self.max_body_chars,
        )

    def prepare(
        self,
        *,
        request_id: object,
        title: object,
        body: object,
        run_id: object | None = None,
    ) -> IntakeArtifact:
        return prepare_intake(
            request_id=request_id,
            title=title,
            body=body,
            run_id=run_id,
            guard=self,
        )


def validate_input(
    *,
    request_id: object,
    title: object,
    body: object,
    run_id: object | None = None,
    max_request_id_chars: int = DEFAULT_MAX_REQUEST_ID_CHARS,
    max_title_chars: int = DEFAULT_MAX_TITLE_CHARS,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
) -> RunInput:
    """Validate and normalize one raw request without masking it.

    No lossy type conversion is attempted.  This prevents values such as
    integers and ``None`` from entering the agent pipeline as apparently valid
    text.
    """

    limits = {
        "max_request_id_chars": max_request_id_chars,
        "max_title_chars": max_title_chars,
        "max_body_chars": max_body_chars,
    }
    for name, value in limits.items():
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")

    validated_run_id = new_run_id() if run_id is None else _validate_run_id(run_id)
    clean_request_id = _validate_text(
        request_id,
        field="request_id",
        max_chars=max_request_id_chars,
        allow_multiline=False,
    )
    clean_title = _validate_text(
        title,
        field="title",
        max_chars=max_title_chars,
        allow_multiline=False,
    )
    clean_body = _validate_text(
        body,
        field="body",
        max_chars=max_body_chars,
        allow_multiline=True,
    )
    return RunInput(
        run_id=validated_run_id,
        request_id=clean_request_id,
        title=clean_title,
        body=clean_body,
    )


def prepare_intake(
    *,
    request_id: object,
    title: object,
    body: object,
    run_id: object | None = None,
    guard: InputGuard | None = None,
) -> IntakeArtifact:
    """Validate raw input and return both original and model-safe text."""

    selected_guard = guard or InputGuard()
    raw = selected_guard.validate(
        request_id=request_id,
        title=title,
        body=body,
        run_id=run_id,
    )
    title_result = mask_pii_with_report(raw.title)
    body_result = mask_pii_with_report(raw.body)
    return IntakeArtifact(
        run_id=raw.run_id,
        request_id=raw.request_id,
        original_title=raw.title,
        original_body=raw.body,
        masked_title=title_result.text,
        masked_body=body_result.text,
        pii_masked=title_result.masked or body_result.masked,
    )


def mask_pii(text: str) -> str:
    """Mask basic Korean identity-number, email, and phone patterns."""

    return mask_pii_with_report(text).text


def mask_pii_with_report(text: str) -> MaskingResult:
    if type(text) is not str:
        raise TypeError("text must be a string")
    masked = text
    counts: dict[str, int] = {}
    for name, pattern, replacement in _MASK_RULES:
        masked, count = pattern.subn(replacement, masked)
        counts[name] = count
    return MaskingResult(
        text=masked,
        identity_numbers=counts["identity_number"],
        emails=counts["email"],
        phones=counts["phone"],
    )


def contains_pii(text: str) -> bool:
    """Return whether text still contains a supported basic PII pattern."""

    if type(text) is not str:
        raise TypeError("text must be a string")
    return any(pattern.search(text) is not None for _, pattern, _ in _MASK_RULES)


def _validate_run_id(value: object) -> str:
    try:
        return validate_run_id(value)
    except ContractValidationError as exc:
        raise InputValidationError(str(exc)) from exc


def _validate_text(
    value: object,
    *,
    field: str,
    max_chars: int,
    allow_multiline: bool,
) -> str:
    if type(value) is not str:
        raise InputValidationError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise InputValidationError(f"{field} is required")
    if len(normalized) > max_chars:
        raise InputValidationError(
            f"{field} must contain at most {max_chars} characters"
        )
    for character in normalized:
        codepoint = ord(character)
        if codepoint == 0 or (codepoint < 32 and character not in "\t\n\r"):
            raise InputValidationError(f"{field} contains a control character")
    if not allow_multiline and any(character in normalized for character in "\n\r"):
        raise InputValidationError(f"{field} must be a single line")
    return normalized
