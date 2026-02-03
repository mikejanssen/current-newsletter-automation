#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="./.env"
if [ ! -f "$ENV_FILE" ] && [ -f "../newsletter/.env" ]; then
  ENV_FILE="../newsletter/.env"
fi

set -a
. "$ENV_FILE"
set +a

mkdir -p output
OUT_FILE="output/monthly-pageviews-$(date +%Y-%m-%d).txt"

python3 monthly-pageview-update.py --post-slack --output-file "$OUT_FILE"
