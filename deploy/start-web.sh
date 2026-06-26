#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${VIDEO_PROCESSOR_APP_DIR:-/data/video-processor-web}"
export VIDEO_PROCESSOR_ROOT="${VIDEO_PROCESSOR_ROOT:-/data/video-processor-data}"

cd "$APP_DIR"
exec "$APP_DIR/.venv/bin/uvicorn" web_app.main:app --host 0.0.0.0 --port 8899 --workers 1
