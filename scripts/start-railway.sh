#!/bin/sh
set -eu

: "${PORT:?Railway must provide PORT}"

CORE_PORT="${CORE_PORT:-8081}"
DATA_DIR="${DATA_DIR:-/data}"
export DATA_DIR
export RESEARCH_ADVISOR_DATA_DIR="${RESEARCH_ADVISOR_DATA_DIR:-$DATA_DIR}"
export CPP_CORE_URL="${CPP_CORE_URL:-http://127.0.0.1:${CORE_PORT}}"

mkdir -p "$DATA_DIR" "$RESEARCH_ADVISOR_DATA_DIR"

./research-advisor-server "$CORE_PORT" ./cpp/web &
core_pid=$!
api_pid=""

shutdown() {
    if [ -n "$api_pid" ]; then
        kill -TERM "$api_pid" 2>/dev/null || true
    fi
    kill -TERM "$core_pid" 2>/dev/null || true
    wait "$core_pid" 2>/dev/null || true
}
trap shutdown EXIT INT TERM

python - "$CORE_PORT" "$core_pid" <<'PY'
import json
import os
import sys
import time
from urllib.request import urlopen

port, pid = sys.argv[1], int(sys.argv[2])
url = f"http://127.0.0.1:{port}/api/health"
for _ in range(80):
    try:
        with urlopen(url, timeout=1) as response:
            health = json.load(response)
        if health.get("service") == "research-advisor-cpp":
            if not health.get("analysis_configured"):
                raise SystemExit(
                    "C++ core started, but LLM_API_KEY or LLM_MODEL is missing."
                )
            print("C++ analysis core is ready.", flush=True)
            break
    except SystemExit:
        raise
    except Exception:
        try:
            os.kill(pid, 0)
        except OSError:
            raise SystemExit("C++ analysis core exited before becoming ready.")
        time.sleep(0.25)
else:
    raise SystemExit("C++ analysis core did not become ready in time.")
PY

uvicorn app.main:app --host 0.0.0.0 --port "$PORT" &
api_pid=$!
wait "$api_pid"
