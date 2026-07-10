from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .events import AgentEvent
from .run import run_minwon, save_result
from .xlsx_reader import Minwon, load_minwons


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XLSX = str(PROJECT_ROOT / "data" / "minwon_sample.xlsx")


class WebState:
    def __init__(self, xlsx: str) -> None:
        self.xlsx = xlsx
        self.minwons = load_minwons(xlsx)
        self.allow_real_runs = os.getenv("ALLOW_REAL_RUNS", "0") == "1"


def create_handler(state: WebState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MinwonHarnessWeb/2.0"

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"[web] {self.address_string()} - {fmt % args}")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                return self._send_html(INDEX_HTML)
            if parsed.path == "/api/minwons":
                try:
                    params = parse_qs(parsed.query)
                    limit = min(120, max(1, int(params.get("limit", ["80"])[0])))
                except (TypeError, ValueError):
                    return self._send_json({"error": "limit must be an integer"}, status=400)
                return self._send_json(
                    {
                        "xlsx": state.xlsx,
                        "count": len(state.minwons),
                        "has_api_key": _has_api_key(),
                        "allow_real_runs": state.allow_real_runs,
                        "items": [_minwon_summary(i, item) for i, item in enumerate(state.minwons[:limit], 1)],
                    }
                )
            self._send_json({"error": "not found"}, status=404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/run":
                return self._send_json({"error": "not found"}, status=404)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._send_json({"error": "invalid content length"}, status=400)
            if length < 2 or length > 65_536:
                return self._send_json({"error": "request body size is invalid"}, status=400)
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._send_json({"error": "body must be valid JSON"}, status=400)
            if type(payload) is not dict:
                return self._send_json({"error": "body must be a JSON object"}, status=400)
            row = payload.get("row", 1)
            dry_run = payload.get("dry_run", True)
            if type(row) is not int or type(dry_run) is not bool:
                return self._send_json({"error": "row must be int and dry_run must be bool"}, status=400)
            if not dry_run and not state.allow_real_runs:
                return self._send_json(
                    {"error": "real model calls are disabled; set ALLOW_REAL_RUNS=1 on a trusted local server"},
                    status=403,
                )
            self._run_pipeline(row=row, dry_run=dry_run)

        def _run_pipeline(self, *, row: int, dry_run: bool) -> None:
            if row < 1 or row > len(state.minwons):
                return self._send_json(
                    {"error": f"row must be between 1 and {len(state.minwons)}"},
                    status=400,
                )
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            selected = state.minwons[row - 1]
            events: list[dict[str, object]] = []

            def write(obj: dict) -> None:
                self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()

            def emit(event: AgentEvent) -> None:
                data = event.to_dict()
                events.append(data)
                write(data)

            try:
                context = run_minwon(selected, dry_run=dry_run, emit=emit)
                output = save_result(
                    "outputs",
                    context,
                    events,
                    row=row,
                    dry_run=dry_run,
                )
                write(
                    {
                        "type": "result",
                        "run_id": context.run_id,
                        "status": context.status.value,
                        "path": str(output),
                        "final": context.final,
                        "decision": context.decision.to_dict() if context.decision else None,
                    }
                )
            except Exception as exc:
                write({"type": "error", "message": " ".join(str(exc).split())[:500]})

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: dict, *, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _minwon_summary(index: int, item: Minwon) -> dict[str, str | int]:
    return {
        "row": index,
        "request_id": item.request_id,
        "title": item.title,
        "body": item.body,
        "body_preview": item.body.replace("\n", " ")[:180],
    }


def _has_api_key() -> bool:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    return bool(key and key != "sk-or-...")


def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)
    state = WebState(args.xlsx)
    handler = create_handler(state)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Minwon Agents web running at http://{args.host}:{args.port}")
    print(f"Loaded {len(state.minwons)} minwon rows from {Path(args.xlsx)}")
    print("Use dry-run mode without OPENROUTER_API_KEY, or set the key for real model calls.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping server")
    finally:
        server.server_close()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="민원 답변 초안 하네스 웹 UI")
    parser.add_argument("--xlsx", default=DEFAULT_XLSX)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--env-file", default=".env.local")
    return parser.parse_args()


def load_env_file(path: str | Path) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


INDEX_HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>민원답변 멀티에이전트</title>
  <style>
    :root {
      --frame: #000000;
      --canvas: #ffffff;
      --ink: #000000;
      --red: #e91d2a;
      --yellow: #fcc20f;
      --purple: #6a26a4;
      --link: #0000ee;
      --olive: #8e8a25;
      --sage: #b3bd95;
      --salmon: #d77a7a;
      --peach: #e6915d;
      --lime: #c0d4a7;
      --sky: #9ab6c8;
      --steel: #a5b8c0;
      --periwinkle: #8c9ae0;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 8px;
      background: var(--frame);
      color: var(--ink);
      font-family: "Times New Roman", Times, serif;
      font-size: 14px;
      line-height: 1.4;
      letter-spacing: 0;
    }
    .global-nav {
      background: var(--frame); color: var(--canvas);
      display: flex; align-items: center; position: sticky; top: 8px; z-index: 40;
      max-width: 1280px; margin: 0 auto; border: 8px solid var(--frame); border-bottom: 0;
      font-family: Helvetica, Arial, sans-serif; font-size: 12px; line-height: 1; letter-spacing: 0;
    }
    .global-inner {
      width: 100%; margin: 0; padding: 10px 12px;
      display: flex; align-items: center; justify-content: space-between; gap: 24px;
    }
    .global-brand {
      font-family: "Arial Black", Helvetica, Arial, sans-serif;
      font-size: 18px; font-weight: 900; line-height: 1; text-transform: uppercase;
    }
    .global-links { display: flex; align-items: center; gap: 12px; color: var(--canvas); }
    .global-links span { border: 1px solid var(--canvas); padding: 4px 8px; font-weight: 700; text-transform: uppercase; }
    .global-links span:first-child { color: var(--red); border-color: var(--red); }
    .global-links span:last-child { background: var(--yellow); color: var(--ink); border-color: var(--frame); }
    .app {
      display: grid; grid-template-columns: 320px minmax(0, 1fr);
      max-width: 1280px; min-height: calc(100vh - 80px); margin: 0 auto;
      background: var(--canvas); border: 8px solid var(--frame); border-top: 0;
    }
    aside {
      border-right: 3px solid var(--frame); background: var(--canvas);
      min-height: calc(100vh - 80px); position: sticky; top: 58px;
    }
    header { padding: 14px 12px; border-bottom: 3px solid var(--frame); background: var(--olive); }
    h1 {
      margin: 0; font-family: "Arial Black", Helvetica, Arial, sans-serif;
      font-size: 24px; font-weight: 900; line-height: 1.05; letter-spacing: 0;
    }
    .sub { margin-top: 8px; color: var(--ink); font-size: 13px; line-height: 1.35; }
    .toolbar { display: flex; gap: 8px; align-items: center; padding: 8px; border-bottom: 2px solid var(--frame); background: var(--steel); }
    input[type="search"] {
      width: 100%; height: 30px; border: 1px solid var(--frame); background: var(--canvas);
      border-radius: 0; padding: 4px 6px; font: 14px/1.4 "Times New Roman", Times, serif;
      outline: none; color: var(--ink); letter-spacing: 0;
    }
    input[type="search"]:focus { outline: 2px solid var(--yellow); outline-offset: 1px; }
    .list { height: calc(100vh - 206px); overflow: auto; padding: 8px; }
    .item {
      width: 100%; text-align: left; border: 1px solid var(--frame); border-radius: 0;
      background: var(--canvas); padding: 8px; margin: 0 0 6px; color: var(--ink); cursor: pointer;
    }
    .item:hover { background: var(--lime); }
    .item.active { background: var(--sage); border-width: 2px; }
    .item-title {
      font-family: Helvetica, Arial, sans-serif; font-size: 12px; font-weight: 700;
      line-height: 1.2; letter-spacing: 0;
    }
    .item-meta { margin-top: 5px; font-size: 12px; color: var(--ink); line-height: 1.32; }
    main { min-width: 0; }
    .top {
      display: flex; gap: 14px; align-items: center; justify-content: space-between;
      min-height: 48px; padding: 8px 10px; border-bottom: 3px solid var(--frame);
      background: var(--canvas); position: sticky; top: 58px; z-index: 30;
    }
    .status { font-size: 12px; color: var(--ink); overflow-wrap: anywhere; }
    .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    label {
      min-height: 28px; padding: 4px 8px; border: 1px solid var(--frame); border-radius: 0;
      background: var(--yellow); color: var(--ink); font: 700 12px/1 Helvetica, Arial, sans-serif;
      display: inline-flex; gap: 6px; align-items: center; text-transform: uppercase;
    }
    button.primary {
      border: 1px solid var(--frame); border-radius: 0; background: var(--frame); color: var(--canvas);
      min-height: 28px; padding: 6px 16px; font: 700 12px/1 Helvetica, Arial, sans-serif;
      cursor: pointer; text-transform: uppercase;
    }
    button.primary:hover:not(:disabled) { background: var(--red); color: var(--canvas); }
    button.primary:focus-visible { outline: 2px solid var(--yellow); outline-offset: 2px; }
    button.primary:disabled { opacity: .45; cursor: not-allowed; }
    .content { padding: 10px; display: grid; gap: 10px; max-width: none; }
    .section {
      border: 1px solid var(--frame); border-radius: 0; background: var(--canvas);
      overflow: hidden;
    }
    .section.dark { background: var(--canvas); color: var(--ink); }
    .section-head {
      display: flex; justify-content: space-between; align-items: center; gap: 12px;
      max-width: none; margin: 0; padding: 8px 12px;
      border-bottom: 1px solid var(--frame); background: var(--sage);
    }
    .section:nth-of-type(1) .section-head { background: var(--olive); }
    .section:nth-of-type(2) .section-head { background: var(--salmon); }
    .section:nth-of-type(3) .section-head { background: var(--sky); }
    .section:nth-of-type(4) .section-head { background: var(--peach); }
    .section:nth-of-type(5) .section-head { background: var(--lime); }
    .section-title {
      font-family: "Arial Black", Helvetica, Arial, sans-serif;
      font-size: 24px; font-weight: 900; line-height: 1; letter-spacing: 0; text-transform: uppercase;
    }
    .section-body { max-width: none; margin: 0; padding: 12px 14px; }
    .selected-title {
      margin: 0 0 10px; font-family: Helvetica, Arial, sans-serif;
      font-size: 18px; font-weight: 700; line-height: 1.2; letter-spacing: 0;
      overflow-wrap: anywhere;
    }
    .body-text {
      white-space: pre-wrap; color: var(--ink); font-size: 14px; line-height: 1.45;
      max-height: 220px; overflow: auto;
    }
    .stages {
      display: grid; grid-template-columns: repeat(7, minmax(100px, 1fr)); gap: 6px;
      background: var(--canvas); padding: 0; color: var(--ink);
    }
    .stage {
      border: 1px solid var(--frame); border-radius: 0; padding: 8px 10px; background: var(--steel);
      min-height: 74px; position: relative;
    }
    .stage:nth-child(1) { background: var(--sage); }
    .stage:nth-child(2) { background: var(--salmon); }
    .stage:nth-child(3) { background: var(--sky); }
    .stage:nth-child(4) { background: var(--peach); }
    .stage:nth-child(5) { background: var(--periwinkle); }
    .stage:nth-child(6) { background: var(--salmon); }
    .stage:nth-child(7) { background: var(--yellow); }
    .stage::before {
      content: ""; width: 8px; height: 8px; border: 1px solid var(--frame); border-radius: 999px; background: var(--canvas);
      position: absolute; top: 8px; right: 8px;
    }
    .stage-name { font-family: Helvetica, Arial, sans-serif; font-size: 13px; font-weight: 700; line-height: 1.2; padding-right: 18px; }
    .stage-msg { margin-top: 8px; color: var(--ink); font-size: 12px; line-height: 1.35; }
    .stage.running { background: var(--yellow); }
    .stage.running::before { background: var(--red); }
    .stage.done { background: var(--lime); }
    .stage.done::before { background: var(--frame); }
    .stage.error { background: var(--red); color: var(--canvas); }
    .stage.error .stage-msg { color: var(--canvas); }
    .stage.error::before { background: var(--yellow); }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .result-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
    .result-card {
      border: 1px solid var(--frame); border-radius: 0; padding: 10px 12px;
      background: var(--sage); min-height: 112px;
    }
    .result-card:nth-child(2) { background: var(--lime); }
    .result-card:nth-child(3) { background: var(--sky); }
    .result-label { font-family: Helvetica, Arial, sans-serif; font-size: 12px; font-weight: 700; color: var(--ink); line-height: 1.2; text-transform: uppercase; }
    .result-value { margin-top: 8px; font-family: Helvetica, Arial, sans-serif; font-size: 16px; font-weight: 700; line-height: 1.2; }
    .result-detail { margin-top: 8px; color: var(--ink); font-size: 13px; line-height: 1.4; }
    .evidence-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 8px; }
    .evidence-item {
      border: 1px solid var(--frame); border-radius: 0; background: var(--peach); padding: 10px 12px;
    }
    .evidence-item:nth-child(2n) { background: var(--sage); }
    .evidence-title { font-family: Helvetica, Arial, sans-serif; font-size: 13px; font-weight: 700; line-height: 1.2; }
    .evidence-summary { margin-top: 6px; color: var(--ink); font-size: 13px; line-height: 1.4; }
    .hidden { display: none; }
    pre {
      margin: 0; white-space: pre-wrap; word-break: break-word; color: var(--ink);
      font-size: 12px; line-height: 1.4; font-family: "Courier New", Courier, monospace;
    }
    .answer { white-space: pre-wrap; font-size: 14px; line-height: 1.5; color: var(--ink); }
    .dark .answer { color: var(--ink); }
    .log { max-height: 240px; overflow: auto; background: var(--frame); color: var(--canvas); padding: 8px; border-radius: 0; font-size: 12px; line-height: 1.4; }
    .pill {
      border: 1px solid var(--frame); border-radius: 0; padding: 4px 8px;
      color: var(--ink); background: var(--yellow); font: 700 12px/1 Helvetica, Arial, sans-serif;
      white-space: nowrap; text-transform: uppercase;
    }
    .dark .pill { color: var(--ink); background: var(--yellow); border-color: var(--frame); }
    .warn { color: var(--red); font-weight: 700; }
    @media (max-width: 920px) {
      .app { grid-template-columns: 1fr; }
      aside { min-height: auto; position: static; border-right: 0; border-bottom: 3px solid var(--frame); }
      .top { top: 58px; padding: 8px; flex-direction: column; align-items: stretch; }
      .controls { justify-content: flex-start; }
      .list { height: 260px; }
      .stages, .grid-2, .result-grid { grid-template-columns: 1fr; }
      .stages { gap: 6px; }
      .section-head { flex-wrap: wrap; }
      .section-title { font-size: 20px; }
      .selected-title { font-size: 16px; }
      .global-nav { position: static; border-width: 4px; }
      .global-inner { align-items: flex-start; flex-direction: column; gap: 8px; }
      .global-links { flex-wrap: wrap; gap: 6px; }
    }
  </style>
</head>
<body>
  <nav class="global-nav">
    <div class="global-inner">
      <div class="global-brand">MINWON ANSWER SYSTEM</div>
      <div class="global-links">
        <span>OpenRouter Ready</span>
        <span>Agent Line</span>
        <span>Run Queue</span>
      </div>
    </div>
  </nav>
  <div class="app">
    <aside>
      <header>
        <h1>민원답변 멀티에이전트</h1>
        <div class="sub">입력 → 분석 → 근거 검색 → 작성 → 병렬 검증 → 정책 게이트 흐름을 실행합니다.</div>
      </header>
      <div class="toolbar"><input id="search" type="search" placeholder="민원 제목 검색" /></div>
      <div id="list" class="list"></div>
    </aside>
    <main>
      <div class="top">
        <div>
          <div class="status" id="source">데이터 로딩 중</div>
          <div class="status" id="key-status"></div>
        </div>
        <div class="controls">
          <label><input id="dry-run" type="checkbox" checked /> dry-run</label>
          <button id="run" class="primary" disabled>에이전트 실행</button>
        </div>
      </div>
      <div class="content">
        <section class="section">
          <div class="section-head">
            <div class="section-title">선택한 민원</div>
            <span class="pill" id="selected-row">미선택</span>
          </div>
          <div class="section-body">
            <h2 id="selected-title" class="selected-title">왼쪽에서 민원을 선택하세요</h2>
            <div id="selected-body" class="body-text"></div>
          </div>
        </section>

        <div class="stages" id="stages"></div>

        <section class="section">
          <div class="section-head">
            <div class="section-title">확인 결과</div>
            <span class="pill" id="summary-status">실행 전</span>
          </div>
          <div class="section-body">
            <div class="result-grid">
              <div class="result-card">
                <div class="result-label">민원 유형</div>
                <div class="result-value" id="category">-</div>
                <div class="result-detail" id="department">-</div>
              </div>
              <div class="result-card">
                <div class="result-label">핵심 쟁점</div>
                <div class="result-value" id="difficulty">-</div>
                <div class="result-detail" id="issues">-</div>
              </div>
              <div class="result-card">
                <div class="result-label">검색 키워드</div>
                <div class="result-value" id="laws">-</div>
                <div class="result-detail" id="keywords">-</div>
              </div>
            </div>
          </div>
        </section>

        <section class="section">
          <div class="section-head"><div class="section-title">근거 후보</div></div>
          <div class="section-body"><div id="evidence" class="evidence-list">실행 후 관련 근거 후보가 표시됩니다.</div></div>
        </section>

        <section class="section">
          <div class="section-head"><div class="section-title">초안</div></div>
          <div class="section-body"><div id="draft" class="answer">-</div></div>
        </section>

        <section class="section">
          <div class="section-head"><div class="section-title">정책 게이트</div><span class="pill" id="gate-status">판정 전</span></div>
          <div class="section-body"><div id="gate-reasons" class="answer">검증 완료 후 공개 여부가 표시됩니다.</div></div>
        </section>

        <section class="section">
          <div class="section-head"><div class="section-title">승격된 최종 답변</div><span class="pill" id="result-path">저장 전</span></div>
          <div class="section-body"><div id="final" class="answer">-</div></div>
        </section>

        <section class="section" id="event-section">
          <div class="section-head"><div class="section-title">이벤트 로그</div></div>
          <div class="section-body"><div id="log" class="log"></div></div>
        </section>
      </div>
    </main>
  </div>

  <script>
    const stages = [
      ["intake", "입력"],
      ["analyze", "분석"],
      ["retrieve", "검색"],
      ["draft", "작성"],
      ["grounding", "근거검증"],
      ["quality", "품질검수"],
      ["gate", "정책게이트"],
    ];
    const state = { items: [], filtered: [], selected: null, running: false };

    const el = (id) => document.getElementById(id);
    const list = el("list");
    const search = el("search");
    const runBtn = el("run");
    const dryRun = el("dry-run");

    function initStages() {
      el("stages").innerHTML = stages.map(([key, label]) => `
        <div class="stage" id="stage-${key}">
          <div class="stage-name">${label} Agent</div>
          <div class="stage-msg" id="stage-msg-${key}">대기 중</div>
        </div>`).join("");
    }

    function setStage(stage, status, message) {
      const box = el(`stage-${stage}`);
      const msg = el(`stage-msg-${stage}`);
      if (!box || !msg) return;
      box.className = `stage ${status || ""}`;
      msg.textContent = message || (status === "done" ? "완료" : "진행 중");
    }

    function resetRunView() {
      initStages();
      el("summary-status").textContent = "실행 중";
      el("category").textContent = "-";
      el("department").textContent = "-";
      el("difficulty").textContent = "-";
      el("issues").textContent = "-";
      el("laws").textContent = "-";
      el("keywords").textContent = "-";
      el("evidence").textContent = "근거 후보를 찾는 중입니다.";
      el("draft").textContent = "-";
      el("final").textContent = "-";
      el("gate-status").textContent = "판정 전";
      el("gate-reasons").textContent = "검증 완료 후 공개 여부가 표시됩니다.";
      el("result-path").textContent = "저장 전";
      el("log").textContent = "";
    }

    function log(obj) {
      el("log").textContent += JSON.stringify(obj, null, 0) + "\n";
      el("log").scrollTop = el("log").scrollHeight;
    }

    function renderList() {
      list.innerHTML = state.filtered.map(item => `
        <button class="item ${state.selected && state.selected.row === item.row ? "active" : ""}" data-row="${item.row}">
          <div class="item-title">${item.row}. ${escapeHtml(item.title)}</div>
          <div class="item-meta">${escapeHtml(item.request_id)} · ${escapeHtml(item.body_preview)}</div>
        </button>`).join("");
      list.querySelectorAll(".item").forEach(btn => {
        btn.addEventListener("click", () => selectRow(Number(btn.dataset.row)));
      });
    }

    function selectRow(row) {
      state.selected = state.items.find(item => item.row === row);
      el("selected-row").textContent = state.selected ? `row ${state.selected.row}` : "미선택";
      el("selected-title").textContent = state.selected ? state.selected.title : "왼쪽에서 민원을 선택하세요";
      el("selected-body").textContent = state.selected ? state.selected.body : "";
      runBtn.disabled = !state.selected || state.running;
      renderList();
    }

    function escapeHtml(text) {
      return String(text || "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }

    async function loadMinwons() {
      const res = await fetch("/api/minwons?limit=120");
      const data = await res.json();
      state.items = data.items;
      state.filtered = data.items;
      el("source").textContent = `${data.count}건 로드 · ${data.xlsx}`;
      el("key-status").innerHTML = data.has_api_key
        ? (data.allow_real_runs
          ? "OpenRouter API 키 감지됨 · 이 로컬 서버는 실제 호출 허용 상태."
          : "OpenRouter API 키 감지됨 · 안전을 위해 웹 실제 호출은 비활성화됨.")
        : "<span class='warn'>OpenRouter API 키 없음</span> · dry-run으로 화면/흐름 확인 가능.";
      dryRun.checked = true;
      dryRun.disabled = !data.allow_real_runs;
      renderList();
      if (state.items[0]) selectRow(state.items[0].row);
    }

    search.addEventListener("input", () => {
      const q = search.value.trim();
      state.filtered = q ? state.items.filter(item => item.title.includes(q) || item.body.includes(q)) : state.items;
      renderList();
    });

    runBtn.addEventListener("click", async () => {
      if (!state.selected || state.running) return;
      state.running = true;
      runBtn.disabled = true;
      resetRunView();
      try {
        const res = await fetch("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ row: state.selected.row, dry_run: dryRun.checked }),
        });
        if (!res.ok) {
          const error = await res.json();
          throw new Error(error.error || `HTTP ${res.status}`);
        }
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const lines = buf.split("\n");
          buf = lines.pop() || "";
          for (const line of lines) {
            if (!line.trim()) continue;
            handleEvent(JSON.parse(line));
          }
        }
      } catch (err) {
        log({ type: "client_error", message: String(err) });
      } finally {
        state.running = false;
        runBtn.disabled = !state.selected;
      }
    });

    function handleEvent(event) {
      log(event);
      if (event.type === "stage" && event.stage) {
        setStage(event.stage, event.status, event.message);
        if (event.status === "done" && event.stage === "analyze") {
          renderClassification(event.data || {});
        }
        if (event.status === "done" && event.stage === "retrieve") {
          renderEvidence(event.data.items || []);
        }
        if (event.status === "done" && event.stage === "draft") {
          el("draft").textContent = event.data.text || "";
        }
        if (event.status === "done" && event.stage === "quality") {
          el("summary-status").textContent = `품질검수 ${event.data.passed ? "통과" : "미통과"} · ${event.data.score}점`;
        }
        if (event.status === "done" && event.stage === "gate") {
          renderGate(event.data || {});
        }
      }
      if (event.type === "done") {
        el("summary-status").textContent = statusLabel(event.status);
      }
      if (event.type === "result") {
        el("result-path").textContent = event.path;
        el("final").textContent = event.final || "자동 공개가 차단되었습니다. 저장된 초안을 담당자가 검토해야 합니다.";
      }
      if (event.type === "error") {
        el("summary-status").textContent = "오류";
        el("final").textContent = event.message;
      }
    }

    function renderClassification(data) {
      el("summary-status").textContent = "분류 완료";
      const secondary = Array.isArray(data.secondary_categories) && data.secondary_categories.length
        ? ` · 부 분류 ${data.secondary_categories.join(", ")}` : "";
      el("category").textContent = (data.primary_category || "-") + secondary;
      el("department").textContent = data.department || "소관 확인 필요";
      el("difficulty").textContent = data.difficulty
        ? `난이도 ${data.difficulty}${data.sensitive ? " · 민감" : ""}` : "-";
      el("issues").textContent = Array.isArray(data.issues) && data.issues.length ? data.issues.join(" · ") : "-";
      el("laws").textContent = Array.isArray(data.law_queries) && data.law_queries.length
        ? data.law_queries.slice(0, 2).join(", ") + (data.law_queries.length > 2 ? " 외" : "")
        : "법령 후보 없음";
      el("keywords").textContent = Array.isArray(data.keywords) && data.keywords.length ? data.keywords.join(", ") : "-";
    }

    function renderEvidence(items) {
      if (!items.length) {
        el("evidence").textContent = "직접 관련 근거 후보를 찾지 못했습니다.";
        return;
      }
      el("evidence").innerHTML = items.map(item => `
        <div class="evidence-item">
          <div class="evidence-title">[${escapeHtml(item.id || "-")}] ${escapeHtml(item.title || "근거 후보")}</div>
          <div class="evidence-summary">${escapeHtml(item.excerpt || "")}</div>
          <div class="evidence-summary">확인일 ${escapeHtml(item.checked_at || "-")} · <a href="${escapeHtml(item.source_url || "#")}" target="_blank" rel="noreferrer">공식 원문 후보</a></div>
        </div>
      `).join("");
    }

    function renderGate(data) {
      el("gate-status").textContent = statusLabel(data.status);
      const reasons = Array.isArray(data.reasons) && data.reasons.length
        ? data.reasons.join(" · ") : "모든 자동 검증을 통과했습니다.";
      el("gate-reasons").textContent = reasons;
      if (data.status !== "completed") {
        el("final").textContent = "자동 공개가 차단되었습니다. 초안과 차단 사유를 담당자가 확인해야 합니다.";
      }
    }

    function statusLabel(status) {
      return ({
        completed: "검증 통과",
        human_review_required: "사람 검토 필요",
        failed: "실행 실패",
        running: "실행 중",
      })[status] || status || "상태 미확인";
    }

    initStages();
    loadMinwons();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
