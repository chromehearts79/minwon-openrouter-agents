from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

from .events import AgentEvent, Usage, stage_done, stage_start
from .models import ModelConfig
from .openrouter import OpenRouterClient, parse_json_object
from .xlsx_reader import Minwon


EventSink = Callable[[AgentEvent], None]


class Agent(Protocol):
    name: str

    def run(self, context: "AgentContext", emit: EventSink) -> None:
        ...


@dataclass
class Classification:
    department: str
    category: str
    difficulty: str
    sensitive: bool
    issues: list[str]
    law_queries: list[str]
    keywords: list[str]


@dataclass
class Evidence:
    title: str
    source: str
    summary: str
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class AgentContext:
    minwon: Minwon
    models: ModelConfig
    classification: Classification | None = None
    evidence: list[Evidence] = field(default_factory=list)
    draft: str = ""
    final: str = ""
    usage: dict[str, Usage] = field(default_factory=dict)


class IntakeAgent:
    name = "IntakeAgent"

    def run(self, context: AgentContext, emit: EventSink) -> None:
        emit(stage_start("intake", "민원 원문을 읽는 중"))
        emit(
            stage_done(
                "intake",
                "민원 입력 준비 완료",
                {
                    "request_id": context.minwon.request_id,
                    "title": context.minwon.title,
                    "body_chars": len(context.minwon.body),
                },
            )
        )


class ClassifyAgent:
    name = "ClassifyAgent"

    SYSTEM = """너는 대한민국 중앙행정기관의 민원 분류 담당자다.
민원을 읽고 후속 검색/답변 작성 단계가 쓸 수 있게 구조화한다.
반드시 순수 JSON 객체만 출력한다.

스키마:
{
  "department": "추정 소관",
  "category": "인사|복무|보수·수당|여비|채용·시험|시스템|정책의견|기타 중 하나",
  "difficulty": "상|중|하",
  "sensitive": true 또는 false,
  "issues": ["핵심 쟁점 1~3개"],
  "law_queries": ["검색할 법령명 1~4개"],
  "keywords": ["조문/근거 검색 키워드 3~8개"]
}"""

    def __init__(self, llm: OpenRouterClient) -> None:
        self.llm = llm

    def run(self, context: AgentContext, emit: EventSink) -> None:
        emit(stage_start("classify", "민원 유형과 검색 키워드를 분류 중"))
        if self.llm.dry_run:
            data = _heuristic_classify(context.minwon.title, context.minwon.body)
        else:
            result = self.llm.chat_json(
                model=context.models.classify,
                system=self.SYSTEM,
                user=f"[제목]\n{context.minwon.title}\n\n[본문]\n{context.minwon.body}",
                max_tokens=800,
            )
            context.usage["classify"] = result.usage
            data = parse_json_object(result.text) or _heuristic_classify(
                context.minwon.title, context.minwon.body
            )

        classification = Classification(
            department=str(data.get("department") or "확인 필요"),
            category=str(data.get("category") or "기타"),
            difficulty=str(data.get("difficulty") or "중"),
            sensitive=bool(data.get("sensitive") or False),
            issues=_str_list(data.get("issues"), fallback=["핵심 쟁점 확인 필요"]),
            law_queries=_str_list(data.get("law_queries") or data.get("lawQueries"), fallback=[]),
            keywords=_str_list(data.get("keywords"), fallback=[]),
        )
        context.classification = classification
        emit(stage_done("classify", "분류 완료", asdict(classification)))


class RetrieveAgent:
    name = "RetrieveAgent"

    LOCAL_KNOWLEDGE = [
        Evidence(
            title="국가공무원법",
            source="local-rule",
            summary="국가공무원의 임용, 휴직, 복무, 신분 보장 등 기본 사항의 상위 법률이다.",
        ),
        Evidence(
            title="공무원임용령",
            source="local-rule",
            summary="채용, 승진, 전보, 경력 산정 등 국가공무원 임용 절차를 구체화한다.",
        ),
        Evidence(
            title="공무원 임용규칙",
            source="local-rule",
            summary="승진소요최저연수, 경력환산 등 임용령 운영에 필요한 세부 기준을 둔다.",
        ),
        Evidence(
            title="국가공무원 복무규정",
            source="local-rule",
            summary="근무시간, 유연근무, 출장, 휴가 등 복무 기준을 규정한다.",
        ),
        Evidence(
            title="공무원수당 등에 관한 규정",
            source="local-rule",
            summary="초과근무수당, 정근수당, 명예퇴직수당 등 각종 수당의 지급 기준을 규정한다.",
        ),
        Evidence(
            title="공무원 여비 규정",
            source="local-rule",
            summary="출장 시 운임, 일비, 식비, 숙박비, 자가용 사용 시 지급 기준을 규정한다.",
        ),
        Evidence(
            title="공무원임용시험령",
            source="local-rule",
            summary="국가공무원 공개경쟁채용시험, 자격요건, 가산점, 시험 절차 등을 규정한다.",
        ),
        Evidence(
            title="사이버국가고시센터 안내",
            source="local-rule",
            summary="원서접수, 비밀번호, 인증서, 시험성적 등록 등 시스템 이용 문의는 사이트 고객지원 확인이 필요하다.",
        ),
    ]

    def run(self, context: AgentContext, emit: EventSink) -> None:
        emit(stage_start("retrieve", "관련 법령과 근거 후보를 검색 중"))
        cls = context.classification
        keywords = cls.keywords if cls else []
        law_queries = cls.law_queries if cls else []
        scored: list[tuple[int, Evidence]] = []

        for item in self.LOCAL_KNOWLEDGE:
            score, matched = _evidence_score(item, law_queries, keywords, context.minwon)
            if score > 0:
                scored.append(
                    (
                        score,
                        Evidence(
                            title=item.title,
                            source=item.source,
                            summary=item.summary,
                            matched_keywords=matched,
                        ),
                    )
                )

        scored.sort(key=lambda pair: pair[0], reverse=True)
        picked = [item for _, item in scored]

        if not picked:
            picked = [
                Evidence(
                    title="소관 부서 확인 필요",
                    source="fallback",
                    summary="현재 로컬 근거 후보로는 직접 관련 법령을 특정하기 어렵다. 답변에서는 단정적 법령 인용을 피하고 소관 부서 확인을 안내한다.",
                    matched_keywords=[],
                )
            ]

        context.evidence = picked[:4]
        emit(stage_done("retrieve", "근거 후보 검색 완료", {"evidence": [asdict(e) for e in context.evidence]}))


class DraftAgent:
    name = "DraftAgent"

    SYSTEM = """너는 중앙행정기관 민원 답변 초안을 작성하는 주무관이다.
검색된 근거 후보만 사용하고, 근거가 부족하면 단정하지 말고 확인 절차를 안내한다.
정중하고 명확한 공직 답변체로 작성한다."""

    def __init__(self, llm: OpenRouterClient) -> None:
        self.llm = llm

    def run(self, context: AgentContext, emit: EventSink) -> None:
        emit(stage_start("draft", "민원 답변 초안을 작성 중"))
        if self.llm.dry_run:
            context.draft = _dry_draft(context)
        else:
            result = self.llm.chat_text(
                model=context.models.draft,
                system=self.SYSTEM,
                user=_draft_prompt(context),
                max_tokens=1800,
                temperature=0.35,
            )
            context.usage["draft"] = result.usage
            context.draft = result.text.strip()
        emit(stage_done("draft", "초안 작성 완료", {"chars": len(context.draft), "draft": context.draft}))


class ReviewAgent:
    name = "ReviewAgent"

    SYSTEM = """너는 민원 답변 최종 검수 담당자다.
초안의 말투, 근거 인용, 환각 가능성, 누락 여부를 검토하고 최종본을 만든다.
반드시 JSON 객체만 출력한다.

스키마:
{
  "checks": [{"criterion": "말투|근거|환각|누락", "pass": true, "comment": "한 줄"}],
  "score": 0~100,
  "final": "최종 답변 전문"
}"""

    def __init__(self, llm: OpenRouterClient) -> None:
        self.llm = llm

    def run(self, context: AgentContext, emit: EventSink) -> None:
        emit(stage_start("review", "초안의 근거와 표현을 검수 중"))
        if self.llm.dry_run:
            review = {
                "checks": [
                    {"criterion": "말투", "pass": True, "comment": "공직 답변체를 사용함"},
                    {"criterion": "근거", "pass": True, "comment": "로컬 근거 후보 범위 내에서 작성됨"},
                    {"criterion": "환각", "pass": True, "comment": "구체 조문 번호 단정 없음"},
                    {"criterion": "누락", "pass": True, "comment": "핵심 문의에 대한 확인 절차 안내 포함"},
                ],
                "score": 82,
                "final": context.draft,
            }
        else:
            result = self.llm.chat_json(
                model=context.models.review,
                system=self.SYSTEM,
                user=_review_prompt(context),
                max_tokens=2000,
            )
            context.usage["review"] = result.usage
            review = parse_json_object(result.text) or {"checks": [], "score": 0, "final": context.draft}
        context.final = str(review.get("final") or context.draft)
        emit(stage_done("review", "검수 완료", {"review": review, "final": context.final}))


def build_agents(llm: OpenRouterClient) -> list[Agent]:
    return [IntakeAgent(), ClassifyAgent(llm), RetrieveAgent(), DraftAgent(llm), ReviewAgent(llm)]


def _heuristic_classify(title: str, body: str) -> dict[str, object]:
    text = f"{title}\n{body}"
    rules = [
        ("인사", "공무원임용령", ["승진소요최저연수", "승진", "임용", "전보", "경력", "의원면직", "재임용"]),
        ("여비", "공무원 여비 규정", ["출장", "자가차량", "유가", "여비", "운임"]),
        ("보수·수당", "공무원수당 등에 관한 규정", ["수당", "초과근무", "시간외", "정근", "명예퇴직"]),
        ("복무", "국가공무원 복무규정", ["육아휴직", "휴직", "유연근무", "출장", "복무", "휴가"]),
        ("채용·시험", "공무원임용시험령", ["7급", "시험", "한국사", "국가고시", "채용", "응시"]),
        ("시스템", "사이버국가고시센터 안내", ["비밀번호", "이메일", "로그인", "사이버국가고시센터"]),
    ]
    for category, law, terms in rules:
        matched = [term for term in terms if term in text]
        if matched:
            law_queries = [law]
            if category == "인사":
                law_queries.append("공무원 임용규칙")
            if category in {"인사", "복무"}:
                law_queries.insert(0, "국가공무원법")
            return {
                "department": "인사혁신처 또는 관계 소관 부서",
                "category": category,
                "difficulty": "중",
                "sensitive": _is_sensitive(text),
                "issues": [f"{category} 관련 기준 확인"],
                "law_queries": law_queries,
                "keywords": matched[:8],
            }
    return {
        "department": "소관 부서 확인 필요",
        "category": "기타",
        "difficulty": "중",
        "sensitive": _is_sensitive(text),
        "issues": ["민원 요지 확인", "소관 및 근거 확인"],
        "law_queries": [],
        "keywords": _keywords(text),
    }


def _is_sensitive(text: str) -> bool:
    signals = ["모독", "탄핵", "부당", "항의", "고발", "불법", "왜 "]
    return any(signal in text for signal in signals)


def _keywords(text: str) -> list[str]:
    tokens = re.findall(r"[가-힣A-Za-z0-9]{2,}", text)
    stop = {"안녕하십니까", "문의드립니다", "관련하여", "궁금합니다", "수고하세요"}
    out: list[str] = []
    for token in tokens:
        if token in stop or token in out:
            continue
        out.append(token)
        if len(out) >= 8:
            break
    return out


def _evidence_score(
    item: Evidence, law_queries: list[str], keywords: list[str], minwon: Minwon
) -> tuple[int, list[str]]:
    haystack = f"{item.title} {item.summary}"
    matched: list[str] = []
    score = 0
    for query in law_queries:
        if query and (query in item.title or item.title in query):
            score += 5
            matched.append(query)
    for keyword in keywords:
        if keyword and keyword in haystack:
            score += 1
            matched.append(keyword)
    return score, matched


def _str_list(value: object, *, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        return cleaned or fallback
    return fallback


def _draft_prompt(context: AgentContext) -> str:
    cls = context.classification
    evidence = "\n".join(
        f"- {e.title}: {e.summary} (source={e.source}, matched={', '.join(e.matched_keywords)})"
        for e in context.evidence
    )
    return f"""[민원]
신청번호: {context.minwon.request_id}
제목: {context.minwon.title}
본문:
{context.minwon.body}

[분류]
{json.dumps(asdict(cls) if cls else {}, ensure_ascii=False)}

[근거 후보]
{evidence}

위 정보를 바탕으로 민원 답변 초안을 작성하라."""


def _review_prompt(context: AgentContext) -> str:
    evidence = "\n".join(f"- {e.title}: {e.summary}" for e in context.evidence)
    return f"""[민원 제목]
{context.minwon.title}

[근거 후보]
{evidence}

[초안]
{context.draft}

초안을 검수하고 JSON으로 최종본을 작성하라."""


def _dry_draft(context: AgentContext) -> str:
    cls = context.classification
    evidence_titles = ", ".join(e.title for e in context.evidence)
    issues = ", ".join(cls.issues if cls else [])
    return f"""안녕하십니까. 귀하께서 문의하신 사항에 대해 안내드립니다.

귀하의 민원은 '{context.minwon.title}' 건으로, 주요 쟁점은 {issues or '민원 요지 확인'}으로 파악됩니다.

현재 확인 가능한 근거 후보는 {evidence_titles or '소관 부서 확인 필요'}입니다. 다만 본 답변은 자동 생성 초안이므로, 실제 회신 전에는 해당 법령의 최신 조문과 소관 부서의 유권해석을 반드시 확인해야 합니다.

문의하신 사항은 관련 규정과 사실관계에 따라 처리 가능 여부가 달라질 수 있으므로, 민원 본문에 적힌 구체적 기간, 대상, 신청 경위 등을 기준으로 담당 부서에서 최종 판단하는 것이 적절합니다.

추가 자료가 필요한 경우 신청번호 {context.minwon.request_id}를 기준으로 보완 요청 또는 유선 안내를 병행하시기 바랍니다. 감사합니다."""
