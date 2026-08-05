#!/usr/bin/env bash
set -euo pipefail
exec python3 -m unittest discover -s "$(dirname "$0")" -p 'test_*.py' -v
