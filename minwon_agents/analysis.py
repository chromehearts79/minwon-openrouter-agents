from __future__ import annotations

"""Deterministic analysis used by the API-key-free harness path.

The module deliberately returns a plain dictionary from ``heuristic_analyze``.
That keeps the heuristic easy to test and also gives the strict contract layer a
single, explicit boundary at ``analyze``.
"""

import re
from dataclasses import dataclass
from typing import Iterable

from .contracts import AnalysisArtifact


CATEGORIES = (
    "인사",
    "복무",
    "보수·수당",
    "여비",
    "채용·시험",
    "시스템",
    "정책의견",
    "기타",
)


@dataclass(frozen=True)
class _Signal:
    term: str
    weight: int
    issue: str


_CATEGORY_SIGNALS: dict[str, tuple[_Signal, ...]] = {
    "인사": (
        _Signal("승진소요최저연수", 12, "승진소요최저연수"),
        _Signal("공무원 임용규칙", 11, "임용규칙 적용"),
        _Signal("공무원임용령", 11, "임용령 적용"),
        _Signal("계획인사교류", 10, "인사교류"),
        _Signal("근무성적평정", 9, "근무성적평정"),
        _Signal("의원면직", 9, "의원면직 후 임용"),
        _Signal("재임용", 8, "재임용 조건"),
        _Signal("인사교류", 8, "인사교류"),
        _Signal("승진", 6, "승진 기준"),
        _Signal("전보", 6, "전보 기준"),
        _Signal("호봉", 5, "호봉·경력 산정"),
        _Signal("경력산정", 6, "경력 산정"),
        _Signal("임용", 3, "임용 기준"),
    ),
    "복무": (
        _Signal("당직휴무", 11, "당직휴무 사용"),
        _Signal("육아휴직", 10, "육아휴직"),
        _Signal("질병휴직", 10, "질병휴직"),
        _Signal("동반휴직", 10, "동반휴직"),
        _Signal("유연근무", 9, "유연근무"),
        _Signal("당직근무", 8, "당직근무"),
        _Signal("병가", 8, "병가 사용"),
        _Signal("휴직", 6, "휴직 기준"),
        _Signal("휴가", 6, "휴가 기준"),
        _Signal("근무시간", 5, "근무시간"),
        _Signal("퇴근시간", 5, "퇴근시간"),
        _Signal("복무", 4, "복무 기준"),
    ),
    "보수·수당": (
        _Signal("공무원수당 등에 관한 규정", 13, "수당 규정 적용"),
        _Signal("시간외근무수당", 12, "시간외근무수당"),
        _Signal("초과근무수당", 12, "초과근무수당"),
        _Signal("정근수당가산금", 12, "정근수당·가산금"),
        _Signal("대우공무원수당", 11, "대우공무원수당"),
        _Signal("명예퇴직수당", 11, "명예퇴직수당"),
        _Signal("정근수당", 10, "정근수당"),
        _Signal("성과상여금", 8, "성과상여금"),
        _Signal("급여과다지급", 9, "급여 과다지급·환수"),
        _Signal("소급지급", 8, "수당 소급 지급"),
        _Signal("초과근무", 7, "초과근무수당"),
        _Signal("시간외근무", 7, "시간외근무수당"),
        _Signal("수당", 6, "수당 지급 기준"),
        _Signal("보수", 5, "보수 기준"),
        _Signal("급여", 5, "급여 기준"),
    ),
    "여비": (
        _Signal("공무원 여비 규정", 13, "여비 규정 적용"),
        _Signal("관내여비", 12, "관내여비"),
        _Signal("관외여비", 12, "관외여비"),
        _Signal("출장여비", 11, "출장여비"),
        _Signal("자가차량", 10, "자가차량 출장비"),
        _Signal("항공권", 9, "항공운임"),
        _Signal("마일리지", 9, "공무항공마일리지"),
        _Signal("유가", 8, "자가차량 유류비"),
        _Signal("운임", 7, "출장 운임"),
        _Signal("일비", 7, "출장 일비"),
        _Signal("숙박비", 7, "출장 숙박비"),
        _Signal("여비", 7, "여비 지급 기준"),
        _Signal("출장", 3, "출장 여비"),
    ),
    "채용·시험": (
        _Signal("공개경쟁채용시험", 12, "공개경쟁채용시험"),
        _Signal("지방인재채용목표제", 12, "지방인재채용목표제"),
        _Signal("공무원임용시험령", 11, "임용시험 기준"),
        _Signal("한국사 자격", 10, "한국사 자격 인정"),
        _Signal("토익", 9, "어학성적 인정"),
        _Signal("응시자격", 9, "응시자격"),
        _Signal("원서접수", 8, "원서접수"),
        _Signal("가산점", 7, "시험 가산점"),
        _Signal("응시", 6, "시험 응시"),
        _Signal("시험", 5, "시험 기준"),
        _Signal("채용", 4, "채용 기준"),
    ),
    "시스템": (
        _Signal("사이버국가고시센터", 15, "사이버국가고시센터 이용"),
        _Signal("임시 비밀번호", 12, "비밀번호 메일 미수신"),
        _Signal("비밀번호 찾기", 12, "비밀번호 찾기"),
        _Signal("비밀번호", 10, "비밀번호 찾기"),
        _Signal("로그인", 9, "로그인 오류"),
        _Signal("이메일로 오지 않", 9, "이메일 미수신"),
        _Signal("인증서", 7, "인증 문제"),
        _Signal("사이트 오류", 7, "사이트 오류"),
    ),
    "정책의견": (
        _Signal("지명철회", 15, "지명·임명에 대한 의견"),
        _Signal("장관 후보자", 14, "장관 후보자 임명 의견"),
        _Signal("장관후보자", 14, "장관 후보자 임명 의견"),
        _Signal("인사청문회", 12, "공직 후보자 검증 의견"),
        _Signal("임명하지 말", 11, "임명에 대한 의견"),
        _Signal("촉구", 8, "정책·인사 의견"),
        _Signal("국민을 모독", 10, "공직 임명 의견"),
        _Signal("의견을 피력", 8, "정책·인사 의견"),
    ),
}


_LAW_QUERIES: dict[str, tuple[str, ...]] = {
    "인사": ("국가공무원법", "공무원임용령", "공무원 임용규칙"),
    "복무": ("국가공무원 복무규정", "국가공무원 복무·징계 관련 예규"),
    "보수·수당": ("공무원수당 등에 관한 규정",),
    "여비": ("공무원 여비 규정",),
    "채용·시험": ("공무원임용시험령",),
    "시스템": ("국가공무원 채용시스템 안내",),
    "정책의견": (),
    "기타": (),
}


_SENSITIVE_SIGNALS = (
    # Medical and health information
    "진단서", "진단시", "진단을", "질병", "임신", "조기진통", "병가", "재해신청", "의료",
    # Criminal and investigative matters
    "피의자", "피의사건", "범죄사건", "수사개시", "수사 개시", "절도", "불송치", "고발",
    # Disciplinary and misconduct matters
    "징계절차", "징계 절차", "징계처분", "징계 처분", "징계의결", "징계 사유",
    "비위", "품위유지", "인사감사", "감사조사", "감사 처분",
)


def heuristic_analyze(title: str, body: str) -> dict[str, object]:
    """Analyze a complaint without an LLM using weighted, multi-label rules.

    Specific phrases have more weight than broad single words.  Policy-opinion
    signals are handled explicitly so an analogy containing a word such as
    ``채용`` does not turn a political opinion into a recruitment question.
    """

    text = _normalize(f"{title}\n{body}")
    scores: dict[str, int] = {category: 0 for category in CATEGORIES if category != "기타"}
    issues_by_category: dict[str, list[tuple[int, str]]] = {}
    matched_terms: list[tuple[int, str]] = []

    for category, signals in _CATEGORY_SIGNALS.items():
        seen_issues: set[str] = set()
        category_issues: list[tuple[int, str]] = []
        for signal in signals:
            if _normalize(signal.term) not in text:
                continue
            scores[category] += signal.weight
            matched_terms.append((signal.weight, signal.term))
            if signal.issue not in seen_issues:
                seen_issues.add(signal.issue)
                category_issues.append((signal.weight, signal.issue))
        issues_by_category[category] = category_issues

    # An explicit policy-opinion phrase is more informative than incidental
    # personnel/recruitment vocabulary used in an analogy.
    if scores["정책의견"] >= 10:
        scores["정책의견"] += 12

    ranked = sorted(scores.items(), key=lambda item: (-item[1], CATEGORIES.index(item[0])))
    primary, primary_score = ranked[0]
    if primary_score < 5:
        primary = "기타"
        secondary: list[str] = []
    else:
        # A secondary label must have its own substantial evidence.  Requiring
        # both an absolute and relative score avoids retaining weak generic hits.
        threshold = max(7, int(primary_score * 0.35))
        secondary = [
            category
            for category, score in ranked[1:]
            if score >= threshold and category != "정책의견"
        ][:2]

    sensitive = primary == "정책의견" or _has_sensitive_signal(text)

    ordered_categories = [primary, *secondary]
    issues: list[str] = []
    for category in ordered_categories:
        candidates = sorted(issues_by_category.get(category, ()), reverse=True)
        for _, issue in candidates:
            if issue not in issues:
                issues.append(issue)
            if len(issues) == 3:
                break
        if len(issues) == 3:
            break
    if not issues:
        issues = ["민원 요지 확인", "소관 및 근거 확인"]

    law_queries: list[str] = []
    for category in ordered_categories:
        for query in _LAW_QUERIES[category]:
            if query not in law_queries:
                law_queries.append(query)

    keywords = _ordered_keywords(matched_terms, text)
    difficulty = _difficulty(
        text=text,
        category_count=len(ordered_categories),
        issue_count=len(issues),
        sensitive=sensitive,
    )
    department = _department(primary)
    return {
        "primary_category": primary,
        "secondary_categories": secondary,
        "department": department,
        "difficulty": difficulty,
        "sensitive": sensitive,
        "issues": issues,
        "law_queries": law_queries[:4],
        "keywords": keywords,
    }


def analyze(title: str, body: str) -> AnalysisArtifact:
    """Return a strict immutable analysis artifact for the harness pipeline."""

    return AnalysisArtifact.from_dict(heuristic_analyze(title, body))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _ordered_keywords(weighted_terms: Iterable[tuple[int, str]], text: str) -> list[str]:
    out: list[str] = []
    for _, term in sorted(weighted_terms, key=lambda item: (-item[0], -len(item[1]), item[1])):
        term = term.strip()
        if term and term not in out:
            out.append(term)
        if len(out) == 8:
            return out

    # The fallback is used only when no domain signal exists.  Common request
    # prose is excluded because it is not a useful retrieval query.
    stop = {
        "안녕하십니까", "문의드립니다", "관련하여", "궁금합니다", "수고하세요",
        "공무원", "관련", "문의", "규정", "경우", "처리", "기준",
    }
    for token in re.findall(r"[가-힣A-Za-z0-9]{2,}", text):
        if token in stop or token in out:
            continue
        out.append(token)
        if len(out) == 8:
            break
    return out


def _difficulty(*, text: str, category_count: int, issue_count: int, sensitive: bool) -> str:
    high_signals = ("조문", "해석", "소멸시효", "피의사건", "범죄사건", "징계절차")
    if sensitive or category_count >= 3 or sum(signal in text for signal in high_signals) >= 2:
        return "상"
    if category_count >= 2 or issue_count >= 2 or len(text) >= 900:
        return "중"
    return "하"


def _has_sensitive_signal(text: str) -> bool:
    for signal in _SENSITIVE_SIGNALS:
        normalized = _normalize(signal)
        if normalized not in text:
            continue
        # A statement that explicitly says there was no misconduct/discipline
        # should not be routed as sensitive based on that negated word alone.
        if normalized == "비위" and re.search(
            rf"{re.escape(normalized)}.{{0,16}}(?:전혀\s*)?없", text
        ):
            continue
        return True
    return False


def _department(category: str) -> str:
    if category == "시스템":
        return "사이버국가고시센터 고객지원"
    if category == "정책의견":
        return "정책 소관 부서 확인 필요"
    if category == "기타":
        return "소관 부서 확인 필요"
    return "인사혁신처 또는 관계 소관 부서"
