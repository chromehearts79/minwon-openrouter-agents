# 과제 제출 안내

## 제출 URL

과제 안내의 "본인의 github에 배포하고 public 주소 제출"은 public GitHub 저장소
주소를 제출하라는 의미다.

예시:

```text
https://github.com/<본인아이디>/minwon-openrouter-agents
```

이 주소를 받은 평가자는 다음을 확인할 수 있다.

1. 멀티에이전트 파이프라인 구현 코드
2. OpenRouter 연동 방식
3. Pixel Agents를 Claude CLI 없이 외부 이벤트로 구동하도록 바꾼 patch
4. `data/minwon_sample.xlsx` 기준 dry-run/실제 API 실행 방법
5. `docs/minwon-real-pixel.png` 실행 화면 캡처

## API Key

실제 OpenRouter API key는 public GitHub에 올리지 않는다.

이 저장소에는 `.env.example`만 포함하고, 실제 실행자는 다음처럼 `.env.local`을
만들어 실행한다.

```bash
cp .env.example .env.local
# .env.local의 OPENROUTER_API_KEY 값을 실제 키로 교체
```

## 웹페이지 주소

현재 구현된 웹페이지는 로컬 서버 기반이다.

```text
http://127.0.0.1:8765/?v=real-pixel
```

이 주소는 내 컴퓨터에서 서버를 실행했을 때만 열린다. 다른 사람에게 이 주소만
보내면 열리지 않는다.

다른 사람이 확인하는 방법은 두 가지다.

1. GitHub 저장소를 받아서 README 순서대로 로컬 실행
2. 별도 서버/Vercel/Render 등에 배포한 뒤 배포 URL 공유

이 프로젝트는 Python backend와 별도 Pixel Agents server가 같이 필요하므로,
정적 GitHub Pages만으로는 실행할 수 없다.
