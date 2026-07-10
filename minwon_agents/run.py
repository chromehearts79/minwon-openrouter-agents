from __future__ import annotations

"""CLI entry point and result persistence for the harness."""

import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .agents import AgentContext, build_agents
from .contracts import RunStatus
from .events import AgentEvent
from .models import ModelConfig, load_model_config
from .openrouter import OpenRouterClient
from .pipeline import AgentPipeline
from .xlsx_reader import Minwon, load_minwons


def main() -> int:
    args = parse_args()
    minwons = load_minwons(args.xlsx)
    if not minwons:
        raise SystemExit("민원 데이터가 없습니다.")
    if args.list:
        for index, item in enumerate(minwons[: args.limit], start=1):
            print(f"{index:04d} {item.request_id} {item.title}")
        return 0

    if args.row < 1 or args.row > len(minwons):
        raise SystemExit(f"--row 값은 1~{len(minwons)} 범위여야 합니다.")

    selected = minwons[args.row - 1]
    events: list[dict[str, object]] = []

    def emit(event: AgentEvent) -> None:
        payload = event.to_dict()
        events.append(payload)
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    try:
        context = run_minwon(selected, dry_run=args.dry_run, emit=emit)
    except RuntimeError as exc:
        # Configuration errors (most commonly a missing API key) happen before
        # the pipeline can create stage artifacts.
        print(json.dumps({"type": "configuration_error", "message": str(exc)}, ensure_ascii=False))
        return 2

    output_path = save_result(
        args.output_dir,
        context,
        events,
        row=args.row,
        dry_run=args.dry_run,
    )
    print(
        json.dumps(
            {
                "type": "result_saved",
                "run_id": context.run_id,
                "status": context.status.value,
                "path": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 1 if context.status is RunStatus.FAILED else 0


def run_minwon(
    minwon: Minwon,
    *,
    dry_run: bool = True,
    emit: Callable[[AgentEvent], None] | None = None,
    models: ModelConfig | None = None,
) -> AgentContext:
    """Execute one complaint through the same harness used by CLI and web."""

    context = AgentContext(minwon=minwon, models=models or load_model_config())
    sink = emit or (lambda event: None)
    llm = OpenRouterClient(dry_run=dry_run)
    return AgentPipeline(build_agents(llm)).run(context, sink)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="근거 기반 민원 답변 초안 생성·검증 멀티에이전트 하네스"
    )
    parser.add_argument("--xlsx", required=True, help="민원 XLSX 경로")
    parser.add_argument("--row", type=int, default=1, help="헤더를 제외한 1-based 데이터 행")
    parser.add_argument("--dry-run", action="store_true", help="API 호출 없이 결정론적 데모 실행")
    parser.add_argument("--list", action="store_true", help="민원 목록만 표시")
    parser.add_argument("--limit", type=int, default=20, help="목록 표시 최대 건수")
    parser.add_argument("--output-dir", default="outputs", help="결과 JSON 루트 디렉터리")
    return parser.parse_args()


def save_result(
    output_dir: str | Path,
    context: AgentContext,
    events: list[dict[str, object]],
    *,
    row: int | None = None,
    dry_run: bool,
) -> Path:
    """Atomically persist one run below a UUID-named directory."""

    root = Path(output_dir)
    run_dir = root / context.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    path = run_dir / "result.json"
    temporary = run_dir / ".result.json.tmp"
    result = context.to_result()
    payload = {
        "schema_version": "2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run" if dry_run else "openrouter",
        "source": {"row": row},
        "models": asdict(context.models),
        "result": result.to_dict(),
        "usage": {stage: asdict(usage) for stage, usage in context.usage.items()},
        "errors": list(context.errors),
        "events": events,
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


if __name__ == "__main__":
    raise SystemExit(main())
