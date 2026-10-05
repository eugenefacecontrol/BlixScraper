#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/blix ]; then
  echo 'Install once: uv sync --extra dev' >&2
  exit 1
fi
exec .venv/bin/blix serve --transport streamable-http
