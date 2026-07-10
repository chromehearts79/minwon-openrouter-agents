# 과제 수행 계획 및 완료 현황

## 1. 목표

과제 주제는 **근거 기반 민원 답변 초안 생성·검증 하네스**다. 민원 한 건을
입력받아 분석·근거 검색·초안 생성을 수행하고, 독립 검수와 코드 기반 정책
게이트를 통과한 경우에만 담당자용 최종 초안을 제공한다.

핵심 완료 조건은 다음과 같다.

- 입력 → 처리 → 검증 → 출력 흐름이 코드와 README에서 동일하게 보일 것
- 각 에이전트가 명확한 artifact를 반환할 것
- 잘못된 모델 출력과 citation을 성공으로 보정하지 않을 것
- 민감·고난도·근거 부족·검수 실패에서 `final`을 차단할 것
- API 키 없이 전체 흐름과 평가를 재현할 수 있을 것
- GitHub 저장소 주소 하나로 제출할 수 있을 것

## 2. 범위

### 구현 범위

- XLSX 입력과 웹 행 선택
- 입력 타입·필수값·길이 검증과 기본 개인정보 마스킹
- 주·부 유형, 쟁점, 난이도, 민감도 분석
- 원문 직접 매칭 기반 로컬 근거 검색
- Evidence ID를 사용하는 답변 초안
- 코드 기반 Grounding Review와 독립 Quality Review
- 결정론적 Policy Gate와 최대 1회 재작성
- `completed`, `human_review_required`, `failed` 종결 상태
- UUID별 JSON 결과, CLI 이벤트, 웹 NDJSON
- 표준 라이브러리 `unittest`와 40건 dry-run 평가

### 비목적

- 법률 자문 또는 행정기관의 최종 유권해석
- 사람의 결재 없는 자동 발송
- 공식 법령 API 실시간 연동
- 조직 인증, 권한, 작업 큐, 데이터베이스, 대규모 운영 모니터링
- 무제한 자율 에이전트 통신 또는 계층형 위임

## 3. 선택한 하네스 패턴

| 패턴 | 적용 | 상태 |
|---|---|---|
| Pipeline | Intake → Analyze → Retrieve → Draft → Review → Gate → Output | 완료 |
| Fan-out/Fan-in | Grounding Review와 Quality Review 병렬 실행·합류 | 완료 |
| Producer–Reviewer | 초안 생성 후 검수, 수정 가능한 실패에 최대 1회 재작성 | 완료 |

분석 결과가 근거 검색의 랭킹 보조 정보이므로 Analyze와 Retrieve는 순차 실행한다.
병렬성은 실제로 독립적인 두 Reviewer에만 적용했다.

## 4. 목표 상태 계약

```text
RunInput
  → IntakeArtifact
  → AnalysisArtifact
  → EvidenceBundle
  → DraftArtifact
  → GroundingReview + QualityReview
  → GateDecision
  → RunResult
```

| 상태 | 조건 | final |
|---|---|---|
| `completed` | 모든 hard gate 통과 | 문자열 |
| `human_review_required` | 민감/고난도/근거 부족/재검수 실패 | `null` |
| `failed` | 입력, schema, 필수 단계 오류 | `null` |

`running`은 실행과 1회 재작성에만 사용하는 내부 비종결 상태다.

## 5. 단계별 완료 현황

### Phase 0 — 주제와 기준 고정

- [x] 주제를 근거 기반 민원 초안 하네스로 확정
- [x] 자동 회신이 아닌 담당자용 초안으로 범위 제한
- [x] 기존 구조와 실패 지점을 문서화
- [x] 대표 회귀 행 3, 4, 12, 17, 20, 25, 39 선정

### Phase 1 — strict 계약과 입력 가드

- [x] `RunStatus`, `Category`, `Difficulty` Enum
- [x] 모든 단계의 frozen dataclass
- [x] strict `from_dict()`와 JSON-safe `to_dict()`
- [x] 문자열 boolean, 임의 enum, 필드 누락·추가 거부
- [x] UUID `run_id` 생성과 검증
- [x] 신청번호·제목·본문 필수값, 타입, 길이, 제어문자 검사
- [x] 식별번호형 숫자, 이메일, 전화번호 기본 마스킹
- [x] 원문과 모델 입력용 마스킹본 분리
- [x] UUID 결과 디렉터리와 원자적 저장

구현: `contracts.py`, `guardrails.py`, `run.py`

### Phase 2 — 분석과 근거 검색

- [x] first-match를 가중치 기반 복수 분류로 교체
- [x] 주 분류와 부 분류 분리
- [x] 범죄·징계·의료·정책의견 민감도 신호
- [x] 로컬 근거를 JSON 카탈로그로 분리
- [x] Evidence ID, excerpt, 공식 URL, 확인일, 일치어, 점수
- [x] 분류값이 아니라 마스킹 원문의 직접 일치를 검색 hard gate로 사용
- [x] 일반어 단독 일치와 무관 근거 제외
- [x] 최대 3개와 근거 부족 표시

회귀 기대값:

| 행 | 검증 목적 | 기대 결과 | 검증 현황 |
|---:|---|---|---|
| 3 | 비밀번호 문의 | 시스템 | 완료 |
| 4 | 승진 민원 | 무관 시험 근거 제외 | 완료 |
| 12 | 수당 조문 문의 | 보수·수당 | 완료 |
| 17 | 복합 쟁점 | 보수·수당 + 여비 | 완료 |
| 20 | 후보자 관련 의견 | 정책의견 | 완료 |
| 25 | 피의사건 | 민감·고난도 사람 검토 | 완료 |
| 39 | 정근수당 | 보수·수당 | 완료 |

구현: `analysis.py`, `retrieval.py`, `data/evidence_catalog.json`

### Phase 3 — 초안, 독립 검수, 정책 게이트

- [x] Draft가 제공된 Evidence ID만 인용
- [x] 본문 citation과 Evidence ID 집합 코드 검증
- [x] Quality Reviewer에 원문, 분석, 근거, 초안 제공
- [x] QualityReview strict schema와 비어 있지 않은 checks
- [x] 80점 기준, 개별 check, 최상위 passed를 모두 판정
- [x] 민감·난이도 상·근거 부족 정책 연결
- [x] 수정 가능한 실패에 최대 1회 재작성
- [x] 재검수 실패를 사람 검토로 종결
- [x] 존재하지 않는 `[E99]`와 citation metadata 불일치 차단
- [x] `completed`일 때만 `final` 허용

구현: `agents.py`, `policy.py`, `pipeline.py`

### Phase 4 — 출력과 웹

- [x] 이벤트에 UUID `run_id`와 UTC 시각
- [x] 결과 JSON에 모든 artifact, 결정 사유, 이벤트, 사용량 저장
- [x] CLI와 웹이 같은 `run_minwon()`·상태 계약 사용
- [x] 웹에 7단계 카드, 근거, 초안, 게이트 사유, 최종 답변 표시
- [x] `completed`와 사람 검토 상태를 다른 문구로 표시
- [x] 웹 실제 모델 호출을 기본 비활성화
- [x] 요청 타입·본문 크기·행 범위 검사

구현: `events.py`, `run.py`, `web.py`

### Phase 5 — 테스트와 평가

- [x] 계약, 입력 가드, 분석, 검색, 정책 단위 테스트
- [x] 정상 완료, 사람 검토, 실행 실패 통합 테스트
- [x] 잘못된 JSON, 빈 초안, 검수 실패, 잘못된 citation 장애 테스트
- [x] 대표 사례 fixture
- [x] 전체 40건 dry-run smoke 평가
- [x] 평가 JSON 결과 저장
- [ ] 실제 모델 canary 평가

실제 모델 canary는 API 비용과 비결정성을 이유로 자동 제출 검증에서 제외한다.
기본 합격 기준은 다음과 같다.

- 전체 40건 파이프라인 실행: 40/40
- 핵심 10건 기대 경로: 10/10
- invalid model output의 정상 수용: 0건
- 검수 실패 후 `final` 노출: 0건
- completed 결과의 잘못된 Evidence ID: 0건
- 민감 필수 사례의 사람 검토 전환: 100%

2026-07-10 최종 확인 결과:

- unittest 81/81 통과
- core category 10/10, secondary 2/2, status 10/10
- core evidence 8/8, sensitive 2/2
- 전체 40건 실행 실패 0건
- 기대값 실패, 상태 불변식 위반, 비완료 `final` 노출 각각 0건

구현: `tests/`, `eval/`, `scripts/evaluate.py`, `examples/`

### Phase 6 — 제출 문서와 GitHub

- [x] README에 주제, 목적, 전체 구조와 에이전트 작성
- [x] dry-run, 웹, 실제 OpenRouter 사용법 작성
- [x] 정상 완료와 안전 중단 결과 예시 작성
- [x] 실제 코드 기준 HARNESS_STRUCTURE 개정
- [x] 제출·선택적 배포 가이드 개정
- [x] 격리된 clean copy에서 README 명령 재실행
- [x] 비밀키·개인정보·불필요 산출물 최종 검사
- [ ] 최종 변경 commit
- [ ] GitHub `main` push
- [ ] GitHub 화면에서 README·파일·실행 명령 확인
- [ ] 공개 저장소 주소 제출

## 6. 테스트 전략

### 단위 테스트

- strict enum, boolean, 배열, 필수 필드, frozen artifact
- 개인정보 패턴, 입력 길이, UUID
- 복수 분류와 민감도
- 근거 eligibility, 랭킹, 공식 URL metadata
- citation 검사와 Policy Gate 상태
- `completed` 전용 `final` 불변식

### 통합·회귀 테스트

- 정상 완료
- 민감·고난도 사람 검토
- 근거 부족
- 검수 실패 후 재작성 1회
- 재작성 후에도 실패
- pipeline 예외의 failed 종결
- 샘플 40건 smoke 실행

### 실모델 평가 원칙

- 기본 테스트와 CI에서는 네트워크 호출 금지
- 대표 2~5건만 수동 canary
- 실행 전 호출 수와 비용 상한 결정
- 모델명, 입력, 프롬프트 버전과 응답을 별도 기록

## 7. 완료 정의

- [x] 입력 → 처리 → 검증 → 출력 흐름이 코드에서 실행됨
- [x] 단계별 artifact와 실패 행동이 명확함
- [x] 모델 schema 오류가 성공으로 처리되지 않음
- [x] 민감·검수 실패에서 final이 생성되지 않음
- [x] completed 결과의 citation이 Evidence ID 범위 안에 있음
- [x] API 키 없이 quickstart 실행 가능
- [x] 결과 파일 충돌이 UUID 경로로 해소됨
- [x] README 필수 항목과 결과 예시 포함
- [ ] GitHub 원격 저장소의 최종 HEAD에서 재현 확인

## 8. 남은 제출 작업

기능 구현은 완료되었다. 남은 작업은 코드 변경이 아니라 제출 고정 단계다.

1. 전체 테스트와 평가 명령을 마지막으로 실행한다.
2. 문서 명령과 실제 CLI `--help`가 일치하는지 확인한다.
3. 저장소에서 비밀키와 로컬 결과가 추적되지 않는지 검사한다.
4. 변경 내용을 `main`에 commit하고 원격 저장소로 push한다.
5. GitHub에서 README 렌더링과 공개 접근을 확인한다.
6. 저장소 URL을 최종 제출한다.

예정 제출 주소:

<https://github.com/chromehearts79/minwon-openrouter-agents>

## 9. 향후 개선

과제 제출 범위 밖에서 운영 수준으로 발전시키려면 다음 순서가 적절하다.

1. 공식 법령 API의 실시간 버전·조문 조회
2. 전체 개인정보 탐지와 원문 암호화·보존 기간 정책
3. 웹 인증, 권한, rate limit, 비용 한도
4. 작업 큐, 동시성 제한, 취소, retry/backoff, 전체 deadline
5. 실모델 response replay와 정기 품질 평가
6. 사람이 승인·수정한 결과를 반영하는 감사 로그
