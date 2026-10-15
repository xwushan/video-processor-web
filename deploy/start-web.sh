#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${VIDEO_PROCESSOR_ENV_FILE:-/data/video-processor-data/video-processor.env}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

export VIDEO_PROCESSOR_ROOT="${VIDEO_PROCESSOR_ROOT:-/data/video-processor-data}"
APP_DIR="${VIDEO_PROCESSOR_APP_DIR:-$(dirname -- "$SCRIPT_DIR")}"
PYTHON_BIN="${VIDEO_PROCESSOR_PYTHON:-$APP_DIR/.venv/bin/python}"
APP_HOST="${VIDEO_PROCESSOR_BIND_IP:-0.0.0.0}"
APP_PORT="${VIDEO_PROCESSOR_PORT:-8899}"

if [ "${VIDEO_PROCESSOR_REQUIRE_AUTH:-0}" = 1 ] && [ -z "${VIDEO_PROCESSOR_AUTH_PASSWORD:-}" ]; then
  echo 'Set VIDEO_PROCESSOR_AUTH_PASSWORD in the trusted environment file before starting production.' >&2
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python not executable: $PYTHON_BIN. Create .venv or set VIDEO_PROCESSOR_PYTHON." >&2
  exit 1
fi

cd "$APP_DIR"
exec "$PYTHON_BIN" -m uvicorn web_app.main:app --host "$APP_HOST" --port "$APP_PORT" --workers 1
