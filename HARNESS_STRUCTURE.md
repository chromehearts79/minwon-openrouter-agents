# 최종 하네스 구조

## 1. 문서 목적

이 문서는 2026-07-10 작업 트리의 실제 코드를 기준으로 근거 기반 민원 답변 초안
하네스의 실행 구조, 데이터 계약, 가드레일, 상태 전이, 개선된 병목과 남은 제약을
설명한다. 과제 소개와 실행법은 [README.md](README.md), 작업 완료 현황은
[plan.md](plan.md)를 참고한다.

## 2. 설계 원칙

1. 모델은 산출물을 만들거나 검토하지만 자신의 결과를 최종 승인하지 못한다.
2. 에이전트 경계마다 immutable dataclass로 입력과 출력을 고정한다.
3. 모델의 JSON은 암묵적으로 보정하지 않고 strict contract로 검증한다.
4. 근거 부족, 민감 사안, 고난도, 검수 실패는 정상 완료와 구분한다.
5. `completed`일 때만 `final`을 허용한다.
6. 모든 실행은 UUID `run_id`로 이벤트와 출력 경로를 격리한다.
7. API 키가 없는 결정론적 `dry-run`을 기본 재현 경로로 제공한다.

## 3. 전체 실행 구조

```text
입력: XLSX 한 행 또는 웹에서 선택한 행
  │
  ▼
InputGuard / IntakeAgent
  ├─ 신청번호·제목·본문 타입, 필수값, 길이, 제어문자 검사
  ├─ UUID run_id
  └─ 원문과 개인정보 마스킹본 분리
  │
  ▼
AnalyzeAgent
  ├─ dry-run: 가중치 기반 복수 분류
  └─ real: OpenRouter JSON → AnalysisArtifact.from_dict()
  │
  ▼
EvidenceAgent
  └─ 마스킹 원문의 구체 용어를 로컬 카탈로그와 직접 대조
  │
  ▼
DraftAgent
  ├─ dry-run: 결정론적 템플릿
  └─ real: OpenRouter 본문 → DraftArtifact
  │
  ├───────────────────────────────┐
  ▼                               ▼
GroundingReviewAgent         QualityReviewAgent
코드 기반 citation 검사       dry-run 규칙 / real OpenRouter
  │                               │
  └────────── ThreadPoolExecutor ─┘
                  │
                  ▼
PolicyGateAgent
  ├─ completed → final=draft.text
  ├─ 수정 가능 → draft revision=1 후 두 Reviewer 재실행
  ├─ human_review_required → final=null
  └─ failed → final=null
                  │
                  ▼
RunResult + AgentEvent[]
  ├─ CLI JSON Lines
  ├─ 웹 NDJSON
  └─ outputs/<run_id>/result.json
```

분석 결과는 근거 검색의 랭킹 보조 정보이므로 두 단계는 의존 순서대로 실행한다.
서로 독립적인 Grounding Review와 Quality Review만 fan-out/fan-in으로 병렬 실행한다.
이 구조는 무제한 자율 협업보다 업무 단계와 승인 책임을 명확하게 하는 고정형
멀티에이전트 하네스다.

## 4. 코드 구성 매핑

| 파일 | 구현 책임 | 주요 타입·함수 |
|---|---|---|
| `minwon_agents/xlsx_reader.py` | 표준 라이브러리로 XLSX 첫 시트 읽기 | `Minwon`, `load_minwons()` |
| `minwon_agents/contracts.py` | strict Enum, frozen artifact, JSON 변환, 상태 불변식 | `RunStatus`, `AnalysisArtifact`, `RunResult` |
| `minwon_agents/guardrails.py` | 입력 검증과 기본 개인정보 패턴 마스킹 | `InputGuard`, `prepare_intake()` |
| `minwon_agents/analysis.py` | 가중치 기반 복수 분류 dry-run 로직 | `heuristic_analyze()`, `analyze()` |
| `minwon_agents/retrieval.py` | 카탈로그 검증, 원문 직접 매칭과 랭킹 | `EvidenceCatalog`, `retrieve_evidence()` |
| `data/evidence_catalog.json` | 공식 도메인 URL·확인일을 포함한 교육용 근거 후보 | `E1` 형식의 항목 |
| `minwon_agents/agents.py` | 7개 역할과 dry-run/실모델 경계 | `AgentSuite`, `build_agents()` |
| `minwon_agents/policy.py` | citation 검사, 상태와 재작성 판정 | `validate_citations()`, `PolicyGate` |
| `minwon_agents/pipeline.py` | 순서, 병렬 검수, 1회 재작성, 오류 종결 | `AgentPipeline` |
| `minwon_agents/events.py` | `run_id`·UTC 시각이 포함된 단계 이벤트 | `AgentEvent` |
| `minwon_agents/openrouter.py` | 표준 라이브러리 HTTP 모델 호출 | `OpenRouterClient` |
| `minwon_agents/run.py` | CLI, 공통 실행 함수, UUID 결과 저장 | `run_minwon()`, `save_result()` |
| `minwon_agents/web.py` | ThreadingHTTPServer 기반 UI·NDJSON API | `WebState`, `create_handler()` |
| `scripts/evaluate.py` | 대표 사례와 전체 40건 dry-run 평가 | 평가 CLI |

## 5. 단계별 에이전트

| 단계 | 입력 | 실행 | 출력 | 실패 행동 |
|---|---|---|---|---|
| Intake | `Minwon`, `run_id` | 필수값·길이·제어문자 검사, 마스킹 | `IntakeArtifact` | `failed` |
| Analyze | 마스킹 제목·본문 | 주·부 유형, 난이도, 민감도, 쟁점 분석 | `AnalysisArtifact` | schema 오류면 `failed` |
| Retrieve | 마스킹 원문, 분석 | 원문 직접 일치가 있는 카탈로그 항목만 최대 3개 선택 | `EvidenceBundle` | 0개면 `insufficient=true` |
| Draft | 마스킹 원문, 분석, 근거 | 허용 Evidence ID를 포함한 초안 | `DraftArtifact` | 빈 본문·계약 오류면 `failed` |
| Grounding Review | 초안, 근거 | 본문 citation 존재·허용 ID·metadata 일치 검사 | `GroundingReview` | 수정 경로 또는 사람 검토 |
| Quality Review | 원문, 분석, 근거, 초안 | 말투·쟁점·근거·단정·개인정보 검토 | `QualityReview` | 수정 경로 또는 사람 검토 |
| Policy Gate | 모든 단계 artifact | 결정론적 상태·재작성 여부 판정 | `GateDecision` | `final` 차단 |

실제 모델 모드의 LLM 경계는 Analyze, Draft, Quality Review 세 곳이다. 최초 검수에
실패해 한 번 재작성하면 Draft와 Quality Review가 한 차례씩 더 호출되므로 한 실행의
모델 호출 상한은 5회다. Evidence 검색, Grounding Review, Policy Gate는 모델을
사용하지 않는다.

## 6. 데이터 계약

```text
Minwon
  → IntakeArtifact
  → AnalysisArtifact
  → EvidenceBundle<EvidenceItem>
  → DraftArtifact
  → GroundingReview + QualityReview
  → GateDecision
  → RunResult
```

`contracts.py`의 artifact는 frozen dataclass다. `from_dict()`는 외부 모델 JSON에
대해 다음을 엄격하게 검사한다.

- 누락되거나 예상하지 않은 필드
- 문자열 `"false"` 같은 boolean 위장 값
- 허용 목록 밖의 category·difficulty·status
- JSON 배열이 아닌 값과 목록 개수 상한
- 빈 문자열, 잘못된 URL·날짜·Evidence ID
- 실패했는데 사유가 없는 Review 또는 Gate
- `completed`가 아닌데 `final`이 존재하는 결과

`AgentContext`는 실행 중 artifact를 보관하는 가변 컨테이너지만, 각 단계가 저장하는
값 자체는 immutable이다. 에이전트가 다른 단계의 artifact 내부를 임의로 수정할 수
없다.

## 7. 결정론적 상태 전이

```text
running
  ├─ 모든 hard gate 통과 ───────────────────────→ completed
  ├─ 수정 가능한 review/citation 실패, revision=0 → running + allow_revision
  │                                                  │
  │                                      재작성·재검수 1회
  │                                                  │
  │                   ├─ 통과 ──────────────────────→ completed
  │                   └─ 미통과 ────────────────────→ human_review_required
  ├─ 민감 / 난이도 상 / 근거 부족 ────────────────→ human_review_required
  └─ 입력 / schema / 필수 단계 오류 ──────────────→ failed
```

정책 우선순위는 다음과 같다.

1. 필수 artifact나 타입이 손상되면 `failed`
2. 민감, 난이도 상, 근거 부족은 재작성으로 해소할 수 없으므로 즉시 사람 검토
3. citation·품질 실패는 한 번만 재작성
4. 재작성 후 실패하면 사람 검토
5. 모든 조건을 통과한 경우에만 `completed`

`PolicyGate.final_for()`와 `RunResult.__post_init__()`가 이 규칙을 이중으로
강제한다. 상태와 `passed`를 임의로 불일치시키거나 비완료 상태에 `final`을 넣으면
계약 오류가 발생한다.

## 8. 가드레일

### 8.1 입력 가드

- 신청번호 100자, 제목 300자, 본문 20,000자 상한
- 빈 문자열, 잘못된 타입, 허용하지 않는 제어문자 거부
- 제목과 신청번호는 단일 행만 허용
- 주민등록번호형 식별번호, 이메일, 국내 전화번호 기본 패턴 마스킹
- 원문과 모델 입력용 마스킹본을 별도 필드에 보존

### 8.2 모델 출력 가드

- JSON 파싱 실패를 휴리스틱 성공으로 숨기지 않고 단계 오류로 처리
- `AnalysisArtifact.from_dict()`와 `QualityReview.from_dict()` strict 검증
- Draft 빈 문자열 거부, revision은 0 또는 1만 허용
- 단계별 출력 토큰 상한과 90초 HTTP timeout

### 8.3 근거 가드

- 카탈로그 시작 시 ID, HTTPS 공식 도메인, 날짜, 중복을 검증
- 분류 metadata만으로 근거를 선택할 수 없음
- 마스킹 원문에 나타난 구체 term이 있어야 검색 후보가 됨
- `공무원`, `규정`, `관련` 같은 일반어 단독 일치 제외
- 최대 3개, ID·excerpt·URL·checked_at·matched_terms·score 제공
- 근거 0개이면 `insufficient=true`로 자동 완료 차단
- 초안 citation이 실제 Evidence ID 집합 안에 있는지 별도 검사

### 8.4 품질·출력 가드

- Reviewer가 민원 원문, 분석, 근거, 초안을 함께 확인
- 점수 80 미만, 최상위 실패, 개별 check 실패를 모두 차단
- 민감 또는 난이도 상은 Reviewer 통과와 무관하게 사람 검토
- 재작성은 최대 1회
- UUID 결과 디렉터리와 임시 파일 교체 저장
- 예외도 `GateDecision(status=failed)`와 오류 이벤트로 종결

### 8.5 웹 실행 가드

- 요청 JSON 타입, 본문 65,536바이트, 행 범위를 검사
- 웹 실제 모델 호출은 `ALLOW_REAL_RUNS=1`일 때만 허용
- 기본 설정은 `dry-run`
- 클라이언트에 전달하는 오류 문자열을 500자로 제한

## 9. 이벤트와 출력

이벤트 단계는 `intake`, `analyze`, `retrieve`, `draft`, `grounding`, `quality`,
`gate`다. 각 이벤트에는 다음이 포함된다.

- `type`: `stage` 또는 `done`
- `status`: `running`, `done`, `error` 또는 최종 상태
- `stage`, `message`, 단계별 `data`
- UUID `run_id`
- UTC ISO 8601 `timestamp`

CLI는 이벤트를 JSON Lines로 출력하고 웹은 같은 이벤트를 NDJSON으로 flush한다.
`save_result()`는 `outputs/<run_id>/result.json`에 다음을 저장한다.

- `schema_version`, 생성 시각, 실행 모드, 원본 행 번호, 모델 설정
- 전체 `RunResult`
- 단계별 토큰·비용 사용량
- 오류와 전체 이벤트

## 10. 기존 병목에 대한 개선

| 기존 현상 | 최종 구현 |
|---|---|
| 첫 키워드가 전체 분류를 결정 | 구체 문구에 가중치를 둔 다중 점수, 주·부 유형 보존 |
| 분류 결과만으로 무관 근거가 연쇄 선택 | 원문 직접 일치를 eligibility hard gate로 사용 |
| 근거가 제목·한 문장뿐이고 추적 불가 | ID, 인용 구간, 공식 URL, 확인일, 일치어, 점수 제공 |
| 검수 실패여도 초안이 최종본이 됨 | 독립 Review + Policy Gate + `RunResult` 불변식 |
| 민감도·난이도가 표시만 됨 | 사람 검토 라우팅에 직접 연결 |
| 잘못된 JSON을 암묵적 타입 변환 | strict parser가 문자열 boolean과 임의 enum을 거부 |
| 검수 단계가 직렬 | 서로 독립인 두 Reviewer를 병렬 실행 |
| 결과 파일명이 충돌 | UUID 디렉터리로 실행별 격리 |
| 오류 시 부분 상태를 잃음 | failed 결정·오류·완료 이벤트를 결과에 보존 |

## 11. 남아 있는 병목과 한계

1. Analyze → Retrieve → Draft는 데이터 의존성이 있어 순차 실행된다.
2. OpenRouter 호출은 동기식이고 요청당 timeout은 90초다. retry, backoff, 전체
   deadline, 취소와 checkpoint는 없다.
3. 웹 요청마다 파이프라인을 실행하므로 동시 실제 호출 수와 비용을 제한하는 작업
   큐가 없다.
4. 로컬 카탈로그는 공식 원문을 실시간 조회하지 않는다. URL·확인일은 추적 단서이며
   최신 법령 적용을 보장하지 않는다.
5. 개인정보 마스킹은 세 가지 기본 패턴만 지원하고, 결과 JSON에는 원문도 보존한다.
6. 웹 민원 목록 API는 본문을 반환하며 인증, 권한, rate limit이 없다.
7. 실제 모델 결과는 비결정적이다. 자동 회귀 테스트는 로컬 `dry-run`을 중심으로 한다.
8. 웹은 단계 이벤트를 스트리밍하지만 모델 토큰 스트리밍은 하지 않는다.
9. 결과 저장소는 로컬 파일시스템이며 장기 보존, 검색, 암호화, 보존 기간 정책이 없다.

따라서 현재 결과물은 교육용·업무 보조용 하네스이며, 실제 민원 시스템의 자동 회신
또는 법률 판단에 사용해서는 안 된다.

## 12. 검증 명령

```bash
python3 -m unittest discover -s tests -v
python3 -m minwon_agents.run \
  --xlsx data/minwon_sample.xlsx \
  --row 17 \
  --dry-run
python3 -m minwon_agents.run \
  --xlsx data/minwon_sample.xlsx \
  --row 25 \
  --dry-run
python3 scripts/evaluate.py \
  --xlsx data/minwon_sample.xlsx \
  --cases eval/minwon_core_cases.json \
  --output examples/evaluation_report.json \
  --all
```

17번은 복합 유형과 정상 완료, 25번은 민감·고난도 사람 검토 경로를 보여준다.

2026-07-10 검증 결과 unittest 81개, 대표 10건 평가, 전체 40건 smoke 실행이
모두 통과했다. 대표 기대값 실패, 상태 불변식 위반, 비완료 결과의 `final` 노출은
각각 0건이며 상세 결과는
[`examples/evaluation_report.json`](examples/evaluation_report.json)에 있다.
