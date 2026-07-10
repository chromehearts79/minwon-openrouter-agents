#!/usr/bin/env python3
from __future__ import annotations

"""Deterministic evaluation runner for the civil-petition harness.

The script intentionally uses only the Python standard library.  It evaluates
semantic expectations with allowed sets/subset checks and separately enforces
release invariants, especially that a non-completed run never exposes ``final``.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minwon_agents.contracts import RunStatus  # noqa: E402
from minwon_agents.run import run_minwon  # noqa: E402
from minwon_agents.xlsx_reader import Minwon, load_minwons  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="민원 멀티에이전트 하네스의 결정론적 회귀 평가"
    )
    parser.add_argument(
        "--xlsx",
        default=str(ROOT / "data" / "minwon_sample.xlsx"),
        help="평가할 민원 XLSX 경로",
    )
    parser.add_argument(
        "--cases",
        default=str(ROOT / "eval" / "minwon_core_cases.json"),
        help="대표 평가 케이스 JSON 경로",
    )
    parser.add_argument(
        "--output",
        help="JSON 보고서 저장 경로(생략하면 표준 출력만 사용)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="대표 행뿐 아니라 XLSX의 모든 행을 실행하고 불변조건을 검사",
    )
    return parser.parse_args(argv)


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if type(raw) is not dict or type(raw.get("cases")) is not list:
        raise ValueError("cases file must be an object containing a cases list")

    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_rows: set[int] = set()
    for index, item in enumerate(raw["cases"]):
        if type(item) is not dict:
            raise ValueError(f"cases[{index}] must be an object")
        case_id = item.get("id")
        row = item.get("row")
        expected = item.get("expected", {})
        if type(case_id) is not str or not case_id.strip():
            raise ValueError(f"cases[{index}].id must be a non-empty string")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        if type(row) is not int or isinstance(row, bool) or row < 1:
            raise ValueError(f"cases[{index}].row must be a positive integer")
        if row in seen_rows:
            raise ValueError(f"duplicate case row: {row}")
        if type(expected) is not dict:
            raise ValueError(f"cases[{index}].expected must be an object")
        seen_ids.add(case_id)
        seen_rows.add(row)
        cases.append(item)
    if not cases:
        raise ValueError("cases list must not be empty")
    return cases


def evaluate(
    minwons: list[Minwon],
    cases: list[dict[str, Any]],
    *,
    evaluate_all: bool = False,
    xlsx_path: str = "",
    cases_path: str = "",
) -> dict[str, Any]:
    by_row = {int(case["row"]): case for case in cases}
    selected: list[dict[str, Any]]
    if evaluate_all:
        selected = [
            by_row.get(
                row,
                {
                    "id": f"row-{row:03d}",
                    "row": row,
                    "purpose": "전체 데이터 불변조건 검사",
                    "expected": {},
                },
            )
            for row in range(1, len(minwons) + 1)
        ]
    else:
        selected = cases

    metric_counts = {
        "category": [0, 0],
        "secondary": [0, 0],
        "status": [0, 0],
        "evidence": [0, 0],
        "sensitive": [0, 0],
    }
    results: list[dict[str, Any]] = []
    all_expectation_failures: list[dict[str, Any]] = []
    invariant_violations: list[dict[str, Any]] = []
    final_exposure_violations: list[dict[str, Any]] = []

    for case in selected:
        row = int(case["row"])
        if row > len(minwons):
            raise ValueError(
                f"case {case['id']} references row {row}, but XLSX has {len(minwons)} rows"
            )
        context = run_minwon(minwons[row - 1], dry_run=True)
        expected = case.get("expected", {})
        evaluated = _evaluate_case(case, context, expected, metric_counts)
        results.append(evaluated)
        for failure in evaluated["expectation_failures"]:
            all_expectation_failures.append(
                {"case_id": case["id"], "row": row, "failure": failure}
            )

        for violation in _invariant_violations(context):
            item = {"case_id": case["id"], "row": row, "violation": violation}
            invariant_violations.append(item)
            if violation.startswith("FINAL_EXPOSURE"):
                final_exposure_violations.append(item)

    metrics = {
        name: _metric(total=counts[0], passed=counts[1])
        for name, counts in metric_counts.items()
    }
    passed = not all_expectation_failures and not invariant_violations
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run",
        "scope": "all" if evaluate_all else "core",
        "inputs": {
            "xlsx": xlsx_path,
            "cases": cases_path,
            "xlsx_rows": len(minwons),
            "evaluated_rows": len(selected),
        },
        "summary": {
            "passed": passed,
            "case_count": len(selected),
            "expectation_failure_count": len(all_expectation_failures),
            "invariant_violation_count": len(invariant_violations),
            "final_exposure_violation_count": len(final_exposure_violations),
            "metrics": metrics,
        },
        "expectation_failures": all_expectation_failures,
        "invariant_violations": invariant_violations,
        "results": results,
    }


def _evaluate_case(
    case: dict[str, Any],
    context: Any,
    expected: dict[str, Any],
    metric_counts: dict[str, list[int]],
) -> dict[str, Any]:
    analysis = context.analysis
    evidence = context.evidence
    primary = analysis.primary_category.value if analysis else None
    secondary = [value.value for value in analysis.secondary_categories] if analysis else []
    categories = {primary, *secondary} if primary else set(secondary)
    evidence_ids = [item.id for item in evidence.items] if evidence else []
    status = context.status.value

    checks: dict[str, bool | None] = {
        "category": None,
        "secondary": None,
        "status": None,
        "evidence": None,
        "sensitive": None,
    }
    failures: list[str] = []

    category_parts: list[bool] = []
    allowed_primary = _string_set(expected.get("allowed_primary_categories"))
    if allowed_primary:
        category_parts.append(primary in allowed_primary)
    required_categories = _string_set(expected.get("required_categories"))
    if required_categories:
        category_parts.append(required_categories <= categories)
    if category_parts:
        checks["category"] = all(category_parts)
        _record_metric(metric_counts, "category", checks["category"])
        if not checks["category"]:
            failures.append("CATEGORY_EXPECTATION_FAILED")

    secondary_any_of = _string_set(expected.get("secondary_any_of"))
    if secondary_any_of:
        checks["secondary"] = bool(secondary_any_of.intersection(secondary))
        _record_metric(metric_counts, "secondary", checks["secondary"])
        if not checks["secondary"]:
            failures.append("SECONDARY_EXPECTATION_FAILED")

    allowed_statuses = _string_set(expected.get("allowed_statuses"))
    if allowed_statuses:
        checks["status"] = status in allowed_statuses
        _record_metric(metric_counts, "status", checks["status"])
        if not checks["status"]:
            failures.append("STATUS_EXPECTATION_FAILED")

    evidence_any_of = _string_set(expected.get("evidence_any_of"))
    evidence_policy = expected.get("evidence_policy")
    if evidence_any_of:
        checks["evidence"] = bool(evidence_any_of.intersection(evidence_ids))
    elif evidence_policy == "required":
        checks["evidence"] = bool(evidence_ids)
    elif evidence_policy == "absent":
        checks["evidence"] = not evidence_ids
    elif evidence_policy not in (None, "optional"):
        raise ValueError(
            f"case {case['id']} has invalid evidence_policy: {evidence_policy!r}"
        )
    if checks["evidence"] is not None:
        _record_metric(metric_counts, "evidence", checks["evidence"])
        if not checks["evidence"]:
            failures.append("EVIDENCE_EXPECTATION_FAILED")

    if "expected_sensitive" in expected:
        expected_sensitive = expected["expected_sensitive"]
        if type(expected_sensitive) is not bool:
            raise ValueError(f"case {case['id']}.expected_sensitive must be boolean")
        checks["sensitive"] = bool(analysis) and analysis.sensitive is expected_sensitive
        _record_metric(metric_counts, "sensitive", checks["sensitive"])
        if not checks["sensitive"]:
            failures.append("SENSITIVE_EXPECTATION_FAILED")

    return {
        "id": case["id"],
        "row": int(case["row"]),
        "request_id": context.minwon.request_id,
        "purpose": case.get("purpose", ""),
        "actual": {
            "primary_category": primary,
            "secondary_categories": secondary,
            "sensitive": analysis.sensitive if analysis else None,
            "status": status,
            "evidence_ids": evidence_ids,
            "final_available": context.final is not None,
            "revision_count": context.revision_count,
        },
        "checks": checks,
        "expectation_failures": failures,
    }


def _invariant_violations(context: Any) -> list[str]:
    status = context.status
    decision = context.decision
    violations: list[str] = []
    completed = status is RunStatus.COMPLETED

    if completed and not context.final:
        violations.append("FINAL_EXPOSURE:COMPLETED_WITHOUT_FINAL")
    if not completed and context.final is not None:
        violations.append("FINAL_EXPOSURE:NON_COMPLETED_WITH_FINAL")
    if decision is None:
        violations.append("MISSING_GATE_DECISION")
    else:
        if decision.status is not status:
            violations.append("STATUS_DECISION_MISMATCH")
        if completed and decision.passed is not True:
            violations.append("COMPLETED_WITH_REJECTED_DECISION")

    if completed:
        if context.evidence is None or context.evidence.insufficient or not context.evidence.items:
            violations.append("COMPLETED_WITHOUT_EVIDENCE")
        if context.grounding_review is None or not context.grounding_review.passed:
            violations.append("COMPLETED_WITHOUT_GROUNDING_PASS")
        if context.quality_review is None or not context.quality_review.passed:
            violations.append("COMPLETED_WITHOUT_QUALITY_PASS")
    return violations


def _record_metric(
    metric_counts: dict[str, list[int]], name: str, passed: bool | None
) -> None:
    if passed is None:
        return
    metric_counts[name][0] += 1
    metric_counts[name][1] += int(passed)


def _metric(*, total: int, passed: int) -> dict[str, int | float | None]:
    return {
        "checked": total,
        "passed": passed,
        "accuracy": round(passed / total, 4) if total else None,
    }


def _string_set(value: object) -> set[str]:
    if value is None:
        return set()
    if type(value) is not list or any(type(item) is not str for item in value):
        raise ValueError("expectation set fields must be lists of strings")
    return {item.strip() for item in value if item.strip()}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        minwons = load_minwons(args.xlsx)
        if not minwons:
            raise ValueError("XLSX contains no complaint rows")
        cases = load_cases(args.cases)
        report = evaluate(
            minwons,
            cases,
            evaluate_all=args.all,
            xlsx_path=str(Path(args.xlsx)),
            cases_path=str(Path(args.cases)),
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0 if report["summary"]["passed"] else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {"type": "evaluation_error", "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
