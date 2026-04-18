#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── backend ──────────────────────────────────────────────────────────────────
echo "Starting backend on http://localhost:8000 ..."
cd "$ROOT"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r backend/requirements.txt

uvicorn backend.main:app --reload --port 8000 &
BACKEND_PID=$!

# ── frontend ─────────────────────────────────────────────────────────────────
echo "Starting frontend on http://localhost:5173 ..."
cd "$ROOT/frontend"
npm install --silent
npm run dev &
FRONTEND_PID=$!

echo ""
echo "  Backend  → http://localhost:8000"
echo "  Frontend → http://localhost:5173"
echo "  API docs → http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
