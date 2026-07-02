#!/usr/bin/env bash
set -euo pipefail

python3 -m minwon_agents.web --host 0.0.0.0 --port "${PORT:-8765}"
