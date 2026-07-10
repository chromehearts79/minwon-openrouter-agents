# 근거 기반 민원 답변 초안 생성·검증 하네스

민원 XLSX 한 행을 입력받아 쟁점을 분석하고, 근거 후보가 연결된 답변 초안을 만든
뒤, 두 개의 독립 검수와 결정론적 정책 게이트를 통과한 경우에만 담당자용 최종
초안으로 승격하는 멀티에이전트 하네스다.

이 프로젝트의 목적은 답변을 자동 발송하는 것이 아니라, 반복적인 민원 초안 작성
업무를 지원하면서 근거 부족·민감 사안·검수 실패가 자동 완료되는 것을 코드로
차단하는 데 있다. Python 표준 라이브러리만 사용하므로 별도 패키지 설치 없이
API 키가 필요 없는 `dry-run`으로 전체 흐름을 재현할 수 있다.

## 과제 주제와 구성 목적

- 주제: 근거 기반 민원 답변 초안 생성·검증
- 입력: `민원신청번호`, `제목`, `본문` 열이 있는 XLSX의 한 행
- 처리: 입력 검증·마스킹, 복수 유형 분석, 원문 기반 근거 검색, 초안 작성
- 검증: 인용 ID 검증과 품질 검수를 병렬 실행한 뒤 코드 정책으로 판정
- 출력: UUID별 `result.json`, NDJSON 이벤트, 웹 결과 화면
- 안전 원칙: `completed` 상태에서만 `final`을 제공하고 그 외 상태에서는
  `final=null`을 강제

## 전체 구조

```text
XLSX 행 / 웹에서 선택한 민원
              │
              ▼
InputGuard ── 필수값·길이·제어문자 검사, 기본 개인정보 마스킹
              │
              ▼
AnalyzeAgent ─ 주·부 유형, 쟁점, 난이도, 민감도 분석
              │
              ▼
EvidenceAgent ─ 마스킹 원문을 로컬 근거 카탈로그와 직접 대조
              │
              ▼
DraftAgent ── 허용된 Evidence ID만 인용해 담당자용 초안 작성
              │
       ┌──────┴──────┐
       ▼             ▼
GroundingReview   QualityReview
코드 인용 검증     말투·쟁점·단정·개인정보 검수
       └──────┬──────┘
              ▼
PolicyGate ── LLM이 아닌 결정론적 Python 정책
       ┌──────┼──────────────────┐
       ▼      ▼                  ▼
 completed   1회 재작성      human_review_required / failed
 final 있음   재검수              final=null
       └──────────────┬───────────┘
                      ▼
        outputs/<run_id>/result.json + 이벤트 + 웹 UI
```

파이프라인과 Producer–Reviewer 패턴을 결합했다. 두 Reviewer는
`ThreadPoolExecutor`로 동시에 실행되며, 수정 가능한 검수 실패만 최대 한 번
재작성한다. 중앙의 정책 게이트는 모델이 아니므로 같은 산출물에는 항상 같은 결정을
내린다.

## 구성 요소

| 구성 요소 | 역할 | 산출물 |
|---|---|---|
| `InputGuard` / `IntakeAgent` | 필수값·타입·길이 검사, 전화번호·이메일·식별번호 형식 마스킹 | `IntakeArtifact` |
| `AnalyzeAgent` | 주·부 유형, 소관, 난이도, 민감도, 쟁점과 검색어 생성 | `AnalysisArtifact` |
| `EvidenceAgent` | 원문에 실제로 나타난 구체 용어로 최대 3개 근거 후보 검색 | `EvidenceBundle` |
| `DraftAgent` | 제공된 근거 ID를 문장에 표시한 답변 초안 생성 | `DraftArtifact` |
| `GroundingReviewAgent` | `[E1]` 형식의 인용이 검색 결과에 존재하는지 코드로 검사 | `GroundingReview` |
| `QualityReviewAgent` | 말투, 쟁점 대응, 근거 범위, 과도한 단정, 개인정보 검사 | `QualityReview` |
| `PolicyGateAgent` | 모든 hard gate를 종합하고 상태·재작성 여부 결정 | `GateDecision` |
| `AgentPipeline` | 단계 순서, 병렬 검수, 1회 재작성, 오류 종결과 이벤트 관리 | `RunResult` |

실제 모델 모드에서는 Analyze, Draft, Quality Review에 OpenRouter를 사용한다.
입력 가드, 근거 검색, 인용 검증, 최종 상태 결정은 항상 로컬 Python 코드가
수행한다.

## 정책 게이트 상태

| 상태 | 의미 | `final` |
|---|---|---|
| `running` | 내부 실행 또는 1회 재작성 진행 중인 비종결 상태 | `null` |
| `completed` | 근거·품질·민감도·난이도 hard gate를 모두 통과 | 문자열 |
| `human_review_required` | 민감/고난도/근거 부족 또는 재검수 실패로 담당자 판단 필요 | `null` |
| `failed` | 입력·스키마·필수 단계 실행 오류 | `null` |

주요 차단 사유는 `SENSITIVE_CASE`, `HIGH_DIFFICULTY`,
`INSUFFICIENT_EVIDENCE`, `INVALID_CITATION:E99`,
`QUALITY_REVIEW_FAILED`, `MAX_REVISIONS_REACHED`처럼 결과 JSON에 남는다.
검수 실패가 수정 가능하면 최초 판정은 `running + allow_revision=true`이며, 한 번
재작성하고도 통과하지 못하면 `human_review_required`로 종료한다.

## 빠른 시작

### 요구 환경

- Python 3.10 이상
- 추가 Python 패키지 없음
- `dry-run`에는 네트워크와 API 키가 필요 없음

저장소 루트에서 다음 명령을 실행한다.

```bash
python3 -m unittest discover -s tests -v
python3 -m minwon_agents.run \
  --xlsx data/minwon_sample.xlsx \
  --row 17 \
  --dry-run
```

실행 이벤트가 한 줄 JSON으로 출력되고 결과는 자동으로 다음 위치에 저장된다.

```text
outputs/<UUID>/result.json
```

민원 목록만 확인하려면 다음 명령을 사용한다.

```bash
python3 -m minwon_agents.run \
  --xlsx data/minwon_sample.xlsx \
  --list \
  --limit 10
```

## 웹 UI

```bash
python3 -m minwon_agents.web --host 127.0.0.1 --port 8765
```

브라우저에서 <http://127.0.0.1:8765/>을 연다. 민원을 선택하고 `dry-run`이
체크된 상태에서 실행하면 입력 → 분석 → 검색 → 작성 → 두 검수 → 정책 게이트의
진행 상황과 결과를 확인할 수 있다. CLI와 웹은 같은 `run_minwon()`과 상태 계약을
사용한다.

웹 응답은 `application/x-ndjson` 단계 이벤트를 스트리밍한다. 모델이 생성하는
토큰 자체를 스트리밍하는 구조는 아니다.

## 실제 OpenRouter 실행

실제 모델 호출에는 비용이 발생할 수 있고 마스킹되지 않은 민감 정보가 남아 있을 수
있다. 샘플 데이터 또는 사용 권한이 있는 데이터만 사용하고, 먼저 `dry-run`으로
경로를 확인한다.

CLI는 `.env.local`을 자동으로 읽지 않으므로 현재 셸에 키를 설정한다.

```bash
export OPENROUTER_API_KEY='<본인의 키>'
python3 -m minwon_agents.run \
  --xlsx data/minwon_sample.xlsx \
  --row 17
```

단계별 모델은 선택적으로 바꿀 수 있다.

```bash
export OR_CLASSIFY_MODEL='mistralai/mistral-small-3.2-24b-instruct'
export OR_DRAFT_MODEL='google/gemini-2.5-flash'
export OR_REVIEW_MODEL='google/gemini-2.5-flash-lite'
```

웹은 기본적으로 실제 호출을 거부한다. 신뢰할 수 있는 로컬 환경에서만
`.env.example`을 복사하고 명시적으로 허용한다.

```bash
cp .env.example .env.local
# .env.local에 실제 키를 넣고 ALLOW_REAL_RUNS=1로 변경
python3 -m minwon_agents.web --host 127.0.0.1 --port 8765
```

현재 웹 서버에는 사용자 인증과 호출량 제한이 없으므로 공개 서버에서 실제 호출을
허용하면 안 된다.

## 실행 예시

### 정상 완료: 17번 복합 민원

```text
analyze     보수·수당 + 여비, difficulty=중, sensitive=false
retrieve    E5 공무원수당 등에 관한 규정, E6 공무원 여비 규정
draft       [E5], [E6] 인용
grounding   passed=true
quality     passed=true, score=100
gate        status=completed
final       담당자용 초안 문자열
```

결과 JSON의 핵심 부분은 다음과 같다. `run_id`는 실행마다 달라진다.

```json
{
  "status": "completed",
  "analysis": {
    "primary_category": "보수·수당",
    "secondary_categories": ["여비"],
    "difficulty": "중",
    "sensitive": false
  },
  "decision": {
    "status": "completed",
    "passed": true,
    "reasons": [],
    "allow_revision": false
  },
  "final": "담당자 검토용 초안 전문"
}
```

### 안전 중단: 25번 민감 민원

```text
analyze     difficulty=상, sensitive=true
grounding   passed=true
quality     passed=true, score=100
gate        status=human_review_required
reason      SENSITIVE_CASE, HIGH_DIFFICULTY
final       null
```

두 Reviewer가 통과해도 민감·고난도 정책이 우선하므로 자동 최종화되지 않는다.

축약된 제출용 결과는
[`completed_result.example.json`](examples/completed_result.example.json)과
[`human_review_result.example.json`](examples/human_review_result.example.json)에서
바로 비교할 수 있다.

## 테스트와 평가

전체 단위·회귀·통합 테스트:

```bash
python3 -m unittest discover -s tests -v
```

테스트는 다음을 포함한다.

- strict Enum/boolean/필수 필드와 `final` 상태 불변식
- 개인정보 패턴 마스킹과 입력 길이 제한
- 3·4·12·17·20·25·39번 대표 민원의 분류·근거 회귀
- 존재하지 않는 인용, 빈 검수, 낮은 점수, 재작성 상한
- 정상 완료, 사람 검토, 실패 경로
- 전체 40건 `dry-run` smoke 평가

2026-07-10 최종 검증에서 표준 라이브러리 `unittest` **81개가 모두
통과**했다. 평가 결과는
[`evaluation_report.json`](examples/evaluation_report.json)에 저장되어 있다.

```bash
python3 scripts/evaluate.py \
  --xlsx data/minwon_sample.xlsx \
  --cases eval/minwon_core_cases.json \
  --output examples/evaluation_report.json \
  --all
```

평가 결과:

| 항목 | 결과 |
|---|---:|
| 대표 category | 10/10 |
| 대표 secondary category | 2/2 |
| 대표 status | 10/10 |
| 대표 evidence | 8/8 |
| 대표 sensitive | 2/2 |
| 전체 샘플 실행 | 40/40 |
| 기대값 실패 | 0 |
| 상태 불변식 위반 | 0 |
| 비완료 결과의 final 노출 | 0 |

평가 기대값 미충족 시 종료 코드는 1, 입력·평가 파일 오류는 2다.

실제 모델 품질은 모델 버전과 시점에 따라 달라지므로 자동 테스트는 네트워크 없는
`dry-run`을 기준으로 하며, 실모델 평가는 별도 canary로 수행해야 한다.

## 결과 파일

`result.json`에는 다음 정보가 함께 기록된다.

- 스키마 버전, UTC 생성 시각, `dry-run`/`openrouter` 모드
- UUID `run_id`와 최종 상태
- 원문·마스킹 입력, 분석, 근거, 초안, 두 검수, 게이트 판정
- 단계별 모델 사용량과 오류
- `run_id`, 시각이 포함된 전체 단계 이벤트

UUID별 디렉터리를 사용하고 임시 파일에서 `os.replace()`로 교체하므로 같은 행을
동시에 실행해도 결과 경로가 충돌하지 않는다.

## 폴더 구조

```text
.
├── README.md                    # 과제 소개와 실행 방법
├── HARNESS_STRUCTURE.md         # 코드 기준 하네스 상세 구조
├── plan.md                      # 구현 계획과 완료 현황
├── SUBMISSION_GUIDE.md          # GitHub 제출 체크리스트
├── DEPLOY_RENDER.md             # 선택적 웹 배포 가이드
├── data/
│   ├── minwon_sample.xlsx       # 기관·담당자·연락처를 익명화한 교육용 샘플 40건
│   └── evidence_catalog.json    # 교육용 로컬 근거 카탈로그
├── eval/                        # 대표 평가 사례
├── examples/                    # 평가 및 결과 예시
├── minwon_agents/
│   ├── contracts.py             # strict frozen 데이터 계약
│   ├── guardrails.py            # 입력 검증·기본 개인정보 마스킹
│   ├── analysis.py              # 복수 분류 휴리스틱
│   ├── retrieval.py             # 원문 기반 근거 검색
│   ├── agents.py                # 역할별 에이전트
│   ├── policy.py                # 인용 검사·결정론적 게이트
│   ├── pipeline.py              # 오케스트레이션·병렬 검수·재작성
│   ├── run.py                   # CLI와 결과 저장
│   └── web.py                   # 표준 라이브러리 웹 UI
├── scripts/
│   └── evaluate.py              # 회귀 및 전체 샘플 평가
└── tests/                       # unittest 테스트
```

## 안전 한계

- 이 결과는 법률 자문, 행정기관의 최종 유권해석 또는 결재된 회신이 아니다.
- 로컬 카탈로그는 교육용 근거 후보 목록이다. 공식 URL과 확인일을 제공하지만 실제
  적용 전 최신 조문·시행일·기관 지침을 담당자가 다시 확인해야 한다.
- 기본 마스킹은 주민등록번호형 식별번호, 이메일, 전화번호 패턴만 지원한다. 이름,
  주소, 사건 서술 등 모든 개인정보를 탐지하지 않는다.
- 결과 JSON에는 업무 추적을 위해 원문 입력도 포함된다. 실제 개인정보를 처리할
  때는 접근 통제, 암호화, 보존·삭제 정책이 별도로 필요하다.
- 외부 모델 오류, 지연, 모델 편향을 제거하지 못하며 자동 발송 기능은 제공하지 않는다.
- 웹 API에는 인증, 사용자별 권한, rate limit이 없다. 공개 데모는 `dry-run`으로만
  운영해야 한다.

## GitHub 업로드와 제출

비밀키와 로컬 결과가 추적되지 않는지 확인한 뒤 업로드한다.

압축을 푼 제출용 폴더처럼 `.git`이 없는 사본은 먼저 저장소로 초기화한다.

```bash
git init
git add .
git commit -m "Complete evidence-grounded minwon harness"
git branch -M main
git remote add origin https://github.com/<아이디>/minwon-openrouter-agents.git
git push -u origin main
```

이미 Git 저장소로 작업 중이고 `origin`이 등록되어 있다면 초기화하지 말고 현재
연결을 확인한 뒤 변경만 올린다.

```bash
git status --short
git remote -v
git add .
git commit -m "Complete evidence-grounded minwon harness"
git push -u origin main
```

최종 제출물은 파일이나 로컬 주소가 아니라 공개 GitHub 저장소 주소다. 이 작업
트리의 연결 대상은 다음과 같다.

<https://github.com/chromehearts79/minwon-openrouter-agents>

제출 전 상세 점검은 [SUBMISSION_GUIDE.md](SUBMISSION_GUIDE.md), 선택적 웹 배포는
[DEPLOY_RENDER.md](DEPLOY_RENDER.md)를 참고한다.
