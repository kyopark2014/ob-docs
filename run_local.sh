#!/usr/bin/env bash
# Local build + run: frontend (npm) then FastAPI on :8502
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8502

cd "$ROOT"

echo "==> Frontend build (web/)"
cd web
npm install
npm run build
cd "$ROOT"

echo "==> Freeing port ${PORT} if occupied"
if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${PIDS}" ]]; then
    echo "    Port ${PORT} in use by PID(s): ${PIDS} — killing"
    # shellcheck disable=SC2086
    kill ${PIDS} 2>/dev/null || true
    sleep 1
  fi
fi

echo "==> Starting uvicorn on 0.0.0.0:${PORT}"
echo "    Open http://localhost:${PORT}/vault"
export ENABLE_API_DOCS="${ENABLE_API_DOCS:-1}"
export ALLOW_LOCAL_AUTH_BYPASS="${ALLOW_LOCAL_AUTH_BYPASS:-1}"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec uvicorn application.server:app --host 0.0.0.0 --port "${PORT}" --no-server-header
