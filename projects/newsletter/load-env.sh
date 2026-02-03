#!/usr/bin/env bash
set -euo pipefail

# Loads variables from ./.env into the current shell
if [ ! -f ./.env ]; then
  echo "Missing ./.env. Copy .env.example to .env and fill in values." >&2
  exit 1
fi

set -a
. ./.env
set +a

echo "Loaded environment from ./.env"
