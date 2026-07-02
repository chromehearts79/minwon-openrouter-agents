from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .agents import AgentContext, build_agents
from .events import AgentEvent
from .models import load_model_config
from .openrouter import OpenRouterClient
from .pipeline import AgentPipeline
from .xlsx_reader import load_minwons


def main() -> int:
    args = parse_args()
    minwons = load_minwons(args.xlsx)
    if not minwons:
        raise SystemExit("No minwon rows found")
    if args.list:
        for i, item in enumerate(minwons[: args.limit], start=1):
            print(f"{i:04d} {item.request_id} {item.title}")
        return 0

    if args.row < 1 or args.row > len(minwons):
        raise SystemExit(f"--row must be between 1 and {len(minwons)}")

    selected = minwons[args.row - 1]
    models = load_model_config()
    llm = OpenRouterClient(dry_run=args.dry_run)
    context = AgentContext(minwon=selected, models=models)
    events: list[dict] = []

    def emit(event: AgentEvent) -> None:
        payload = event.to_dict()
        events.append(payload)
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    pipeline = AgentPipeline(build_agents(llm))
    pipeline.run(context, emit)

    output_path = save_result(args.output_dir, args.row, context, events)
    print(json.dumps({"type": "result_saved", "path": str(output_path)}, ensure_ascii=False))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OpenRouter multi-agent minwon pipeline")
    parser.add_argument("--xlsx", required=True, help="Path to source minwon xlsx")
    parser.add_argument("--row", type=int, default=1, help="1-based data row to process, excluding header")
    parser.add_argument("--dry-run", action="store_true", help="Run without OpenRouter API calls")
    parser.add_argument("--list", action="store_true", help="List sample minwon rows and exit")
    parser.add_argument("--limit", type=int, default=20, help="List limit")
    parser.add_argument("--output-dir", default="outputs", help="Directory for JSON result files")
    return parser.parse_args()


def save_result(
    output_dir: str | Path, row: int, context: AgentContext, events: list[dict]
) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / f"minwon_row_{row:04d}_{stamp}.json"
    payload = {
        "minwon": asdict(context.minwon),
        "models": asdict(context.models),
        "classification": asdict(context.classification) if context.classification else None,
        "evidence": [asdict(e) for e in context.evidence],
        "draft": context.draft,
        "final": context.final,
        "usage": {stage: asdict(usage) for stage, usage in context.usage.items()},
        "events": events,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())

