#!/usr/bin/env bash
set -euo pipefail

PID_FILE="/usr/local/nginx/logs/nginx.pid"

if [ -s "$PID_FILE" ]; then
  kill -HUP "$(cat "$PID_FILE")"
else
  kill -HUP "$(pgrep -o nginx)"
fi
