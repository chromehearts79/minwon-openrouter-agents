# Render 배포 가이드

이 프로젝트는 서버가 두 개 필요하다.

1. Pixel Agents webview/server
2. Minwon OpenRouter Agents Python web server

따라서 Render에서 Web Service를 두 개 만든다. GitHub Pages 같은 정적 호스팅으로는
실행할 수 없다.

## 1. GitHub에 public repo 업로드

이 저장소를 public GitHub repo로 업로드한다.

예시:

```text
https://github.com/<본인아이디>/minwon-openrouter-agents
```

## 2. Pixel Agents Web Service 생성

Render Dashboard에서 **New > Web Service**를 선택하고 위 GitHub repo를 연결한다.

설정값:

```text
Name: pixel-agents-minwon
Runtime/Language: Node
Build Command: bash scripts/render-build-pixel.sh
Start Command: bash scripts/render-start-pixel.sh
```

배포가 끝나면 다음과 같은 URL이 생긴다.

```text
https://pixel-agents-minwon.onrender.com
```

## 3. Minwon Web Service 생성

같은 GitHub repo로 Web Service를 하나 더 만든다.

설정값:

```text
Name: minwon-openrouter-agents
Runtime/Language: Python
Build Command: python3 -m py_compile minwon_agents/web.py minwon_agents/pixel_adapter.py minwon_agents/openrouter.py minwon_agents/run.py
Start Command: bash scripts/render-start-minwon.sh
```

Environment Variables:

```text
OPENROUTER_API_KEY=<본인 OpenRouter API key>
PIXEL_AGENTS_URL=https://pixel-agents-minwon.onrender.com
PIXEL_AGENTS_PUBLIC_URL=https://pixel-agents-minwon.onrender.com
```

`OPENROUTER_API_KEY`를 넣지 않아도 dry-run은 가능하지만, 실제 OpenRouter 모델
호출 결과를 보여주려면 Render 환경변수에 키를 넣어야 한다.

## 4. 제출할 웹 URL

Minwon Web Service 배포가 끝나면 다음 같은 URL이 생긴다.

```text
https://minwon-openrouter-agents.onrender.com
```

이 주소가 다른 사람이 브라우저에서 직접 열 수 있는 웹페이지 주소다.

## 주의

무료 인스턴스는 한동안 접속이 없으면 잠들 수 있다. 처음 접속할 때 Pixel Agents
iframe이 늦게 뜨면 30초 정도 기다린 뒤 새로고침하면 된다.
