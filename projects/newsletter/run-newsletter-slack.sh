#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f ./.env ]; then
  echo "Missing ./.env in $SCRIPT_DIR" >&2
  exit 1
fi

set -a
. ./.env
set +a

mkdir -p output
python3 generate-newsletter-slack.py
