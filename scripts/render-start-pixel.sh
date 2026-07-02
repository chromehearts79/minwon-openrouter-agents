#!/usr/bin/env bash
set -euo pipefail

cd pixel-agents-runtime
node dist/cli.js --external-only --host 0.0.0.0 --port "${PORT:-3100}"
