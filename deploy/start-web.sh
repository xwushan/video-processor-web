#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${VIDEO_PROCESSOR_APP_DIR:-/data/video-processor-web}"
ENV_FILE="${VIDEO_PROCESSOR_ENV_FILE:-/data/video-processor-data/video-processor.env}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

export VIDEO_PROCESSOR_ROOT="${VIDEO_PROCESSOR_ROOT:-/data/video-processor-data}"

cd "$APP_DIR"
exec "$APP_DIR/.venv/bin/uvicorn" web_app.main:app --host 0.0.0.0 --port 8899 --workers 1
