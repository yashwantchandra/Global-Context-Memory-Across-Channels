#!/usr/bin/env bash
# Restart the Yaad app: exactly one server on :8000 (old ones are killed, not asked nicely).
cd "$(dirname "$0")/.."
pkill -9 -f "yaad serve" 2>/dev/null; sleep 1
nohup .venv/bin/python -m yaad serve > /tmp/yaad_server.log 2>&1 &
sleep 3
n=$(pgrep -f "yaad serve" | wc -l | tr -d ' ')
curl -s -o /dev/null -w "app http %{http_code} · servers running: $n\n" http://127.0.0.1:8000/
