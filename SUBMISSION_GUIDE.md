# 과제 제출 가이드

## 1. 최종 제출물

최종 제출물은 압축 파일이나 로컬 실행 주소가 아니라 다음 **공개 GitHub 저장소
주소**다.

<https://github.com/chromehearts79/minwon-openrouter-agents>

저장소에는 구현 코드, 샘플 데이터, 테스트·평가, 결과 예시와 다음 문서가 함께 있어야
한다.

- `README.md`: 주제, 목적, 구조, 사용법, 실행·결과 예시
- `HARNESS_STRUCTURE.md`: 코드 기준 상세 구조와 가드레일
- `plan.md`: 구현 계획과 완료·잔여 작업
- `SUBMISSION_GUIDE.md`: 제출 전 확인 절차
- `DEPLOY_RENDER.md`: 선택적 웹 배포 절차

## 2. 제출 전 로컬 검증

저장소 루트에서 순서대로 실행한다.

```bash
python3 -m compileall -q minwon_agents scripts tests
python3 -m unittest discover -s tests -v
python3 -m minwon_agents.run \
  --xlsx data/minwon_sample.xlsx \
  --row 17 \
  --dry-run \
  --output-dir /tmp/minwon-submission-check
python3 scripts/evaluate.py \
  --xlsx data/minwon_sample.xlsx \
  --cases eval/minwon_core_cases.json \
  --output examples/evaluation_report.json \
  --all
```

확인할 결과:

- 테스트가 모두 통과한다.
- 17번 실행이 `completed`이며 `final`이 있다.
- 25번 평가가 `human_review_required`이며 `final=null`이다.
- 평가 보고서가 핵심 회귀와 40건 smoke 결과를 기록한다.
- 결과 JSON 경로가 `outputs/<UUID>/result.json` 형식이다.

웹도 확인하려면 다음 명령을 실행하고 <http://127.0.0.1:8765/>을 연다.

```bash
python3 -m minwon_agents.web --host 127.0.0.1 --port 8765
```

## 3. 비밀정보와 불필요 파일 검사

실제 API 키와 로컬 결과는 commit하지 않는다. `.gitignore`가 다음 항목을 제외한다.

- `.env`, `.env.local`
- `outputs/`
- `__pycache__/`, `*.pyc`
- `.DS_Store`, coverage 산출물

추적 대상에 포함되지 않았는지 확인한다.

```bash
git status --short
git ls-files '.env' '.env.local' 'outputs/*' '*.pyc'
git diff --check
```

`.env.example`에는 예시 값만 두고 실제 키를 넣지 않는다. 샘플 XLSX와 결과 예시에
실제 민원인의 개인정보가 없어야 한다.

## 4. GitHub 업로드

압축을 풀었거나 최종 제출 폴더를 복사해 `.git`이 없는 경우, 새 공개 GitHub
저장소를 만든 뒤 다음 순서로 초기화하고 업로드한다.

```bash
git init
git add .
git status --short
git commit -m "Complete evidence-grounded minwon harness"
git branch -M main
git remote add origin https://github.com/<아이디>/minwon-openrouter-agents.git
git push -u origin main
```

이미 Git 저장소로 작업 중이면 현재 원격 주소를 먼저 확인한다.

```bash
git remote -v
git branch --show-current
```

원격이 이미 올바르면 변경을 검토하고 push한다.

```bash
git add .
git status --short
git commit -m "Complete evidence-grounded minwon harness"
git push -u origin main
```

기존 `origin`이 다른 주소라면 새 remote를 중복 추가하지 말고 확인 후 주소를
교체한다.

```bash
git remote set-url origin https://github.com/<아이디>/minwon-openrouter-agents.git
git push -u origin main
```

## 5. GitHub 화면에서 확인

push 후 로그아웃 창 또는 시크릿 창에서 저장소 URL을 열어 다음을 확인한다.

- [ ] 저장소가 public으로 열린다.
- [ ] 첫 화면에서 README가 정상 렌더링된다.
- [ ] README에 과제 주제와 구성 목적이 있다.
- [ ] 입력 → 처리 → 검증 → 출력 구조가 보인다.
- [ ] dry-run, 웹, 실제 모델 실행법이 있다.
- [ ] 정상 완료와 사람 검토 결과 예시가 있다.
- [ ] `data/`, `eval/`, `examples/`, `tests/`가 보인다.
- [ ] `.env.local`, 실제 키, `outputs/`, 캐시 파일이 없다.
- [ ] 저장소 주소를 clone한 뒤 README 명령으로 재현할 수 있다.

가능하면 별도의 빈 디렉터리에서 clean clone 검증을 수행한다.

```bash
cd /tmp
git clone https://github.com/chromehearts79/minwon-openrouter-agents.git minwon-final-check
cd minwon-final-check
python3 -m unittest discover -s tests -v
python3 -m minwon_agents.run \
  --xlsx data/minwon_sample.xlsx \
  --row 17 \
  --dry-run
```

## 6. 평가자용 1분 재현 경로

평가자는 추가 패키지나 API 키 없이 다음 두 명령으로 핵심을 확인할 수 있다.

```bash
python3 -m unittest discover -s tests -v
python3 -m minwon_agents.run \
  --xlsx data/minwon_sample.xlsx \
  --row 17 \
  --dry-run
```

25번 행으로 바꾸면 민감·고난도 민원이 자동 완료되지 않고 사람 검토로 전환되는
안전 중단 경로를 볼 수 있다.

## 7. 웹 배포 여부

과제의 최종 제출물은 GitHub 저장소 주소이므로 웹 배포는 선택 사항이다. 로컬 주소
`127.0.0.1`은 서버를 실행한 컴퓨터에서만 열리며 제출 URL이 될 수 없다.

공개 웹 데모가 필요하면 [DEPLOY_RENDER.md](DEPLOY_RENDER.md)에 따라 `dry-run`
전용으로 배포한다. 현재 웹 API에는 인증과 호출량 제한이 없으므로 공개 환경에서
실제 모델 호출을 허용하지 않는다.

## 8. 최종 제출 문구 예시

```text
근거 기반 민원 답변 초안 생성·검증 멀티에이전트 하네스입니다.
입력 검증, 분석, 근거 검색, 초안 생성, 병렬 검수, 결정론적 정책 게이트의
입력 → 처리 → 검증 → 출력 흐름을 구현했습니다.

GitHub: https://github.com/chromehearts79/minwon-openrouter-agents
```
