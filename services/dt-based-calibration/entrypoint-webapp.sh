#!/usr/bin/env bash
set -e

case "$1" in
  file-upload-web)
    exec /app/.venv/bin/python3.10 demo_web.py
    ;;
  manual-assisted-web)
    exec /app/.venv/bin/python3.10 manual_calibration_web.py
    ;;
  api-server)
    exec /app/.venv/bin/python3.10 api.py
    ;;
  accuracy-server)
    exec /app/.venv/bin/python3.10 accuracy.py
    ;;
  roi-server)
    exec /app/.venv/bin/python3.10 -m http.server 8080
    ;;
  *)
    echo "Usage: $0 {file-upload-web|manual-assisted-web|api-server|accuracy-server|roi-server}"
    exit 1
    ;;
esac
