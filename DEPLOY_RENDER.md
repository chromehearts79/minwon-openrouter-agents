# Render 웹 배포 가이드

GitHub 저장소 주소만으로 과제 제출 요건을 충족하므로 웹 배포는 선택 사항이다.
외부에서 화면을 확인해야 할 때만 이 절차를 사용한다.

이 프로젝트는 Python 백엔드가 필요하므로 정적 호스팅이 아니라 Render Web
Service로 배포한다. 추가 Python 패키지는 없으며 Python 표준 라이브러리만 사용한다.

## 1. 배포 전 확인

GitHub의 최종 `main`에서 다음 명령이 성공해야 한다.

```bash
python3 -m unittest discover -s tests -v
python3 -m minwon_agents.run \
  --xlsx data/minwon_sample.xlsx \
  --row 17 \
  --dry-run
```

저장소에는 실제 API 키, `.env.local`, `outputs/`를 올리지 않는다.

## 2. Render Web Service 생성

1. Render Dashboard에서 **New → Web Service**를 선택한다.
2. GitHub 저장소를 연결한다.
3. 다음 값을 입력한다.

```text
Name: minwon-openrouter-agents
Runtime: Python
Branch: main
Build Command: python3 -m unittest discover -s tests && python3 -m compileall -q minwon_agents scripts
Start Command: bash scripts/render-start-minwon.sh
Health Check Path: /
```

`scripts/render-start-minwon.sh`는 Render가 제공하는 `PORT`를 사용해 다음과 같이
서버를 시작한다.

```text
python3 -m minwon_agents.web --host 0.0.0.0 --port ${PORT:-8765}
```

## 3. 권장 환경변수

공개 데모는 `dry-run` 전용으로 운영한다.

```text
ALLOW_REAL_RUNS=0
```

이 설정에서는 웹 화면의 실제 호출 선택이 비활성화되고 API로 `dry_run=false`를
보내도 403을 반환한다. `OPENROUTER_API_KEY`는 설정하지 않아도 된다.

단계별 모델 환경변수도 `dry-run` 화면 확인에는 필요 없다.

## 4. 실제 모델 호출이 꼭 필요한 경우

현재 웹 API에는 로그인, 사용자 권한, rate limit, 동시 실행 수와 비용 한도가 없다.
따라서 인터넷에 공개된 서비스에서 실제 모델 호출을 허용하지 않는다.

접근이 통제된 별도 환경에서 위험을 이해하고 테스트할 때만 Secret 환경변수로
다음을 설정한다.

```text
OPENROUTER_API_KEY=<본인의 비밀 키>
ALLOW_REAL_RUNS=1
OR_CLASSIFY_MODEL=mistralai/mistral-small-3.2-24b-instruct
OR_DRAFT_MODEL=google/gemini-2.5-flash
OR_REVIEW_MODEL=google/gemini-2.5-flash-lite
```

실제 민원 데이터는 기본 마스킹 범위를 넘어서는 개인정보를 포함할 수 있다. 외부
모델 전송 전에 별도 개인정보 검토와 적법한 처리 권한이 필요하다.

## 5. 배포 확인

Render가 발급한 URL에서 다음을 확인한다.

1. `/`가 HTTP 200으로 열리는가
2. `/api/minwons?limit=3`에서 샘플 목록이 로드되는가
3. 웹에서 17번 `dry-run`이 `completed`로 끝나는가
4. 25번 `dry-run`이 `human_review_required`이고 최종 답변을 노출하지 않는가
5. 근거 카드, 두 검수 단계, 정책 게이트 사유가 표시되는가
6. `dry-run` 체크를 해제할 수 없고 실제 실행 요청이 거부되는가

예상 URL 형식:

```text
https://minwon-openrouter-agents.onrender.com
```

## 6. 운영상 주의

- 무료 인스턴스는 유휴 후 절전되어 첫 요청이 늦을 수 있다.
- 결과는 `outputs/<run_id>/result.json`에 저장되지만 Render 기본 파일시스템은
  영속 저장소가 아니다. 재배포·재시작 후 보존을 기대하지 않는다.
- `/api/minwons`는 샘플 본문을 반환한다. 실제 개인정보 데이터로 교체하지 않는다.
- 웹 요청은 서버 스레드에서 동기 실행되며 작업 큐와 동시성 제한이 없다.
- 이 서비스는 교육용 업무 보조 데모이며 자동 민원 회신 서비스가 아니다.

## 7. 제출 주소와 웹 주소의 차이

- 과제 제출: 공개 GitHub 저장소 주소
- 선택적 데모: Render가 발급한 웹 URL
- 로컬 확인: `http://127.0.0.1:8765/`

최종 과제 제출에는 다음 GitHub 주소를 사용한다.

<https://github.com/chromehearts79/minwon-openrouter-agents>
