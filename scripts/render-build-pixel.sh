#!/usr/bin/env bash
set -euo pipefail

rm -rf pixel-agents-runtime
git clone --depth 1 https://github.com/pixel-agents-hq/pixel-agents.git pixel-agents-runtime
cd pixel-agents-runtime
git apply ../pixel-agents-openrouter.patch
npm install
npm run build
