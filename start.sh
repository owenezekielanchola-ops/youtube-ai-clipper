#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "=================================================="
echo "  ClipForge AI - Starting Backend & Frontend"
echo "=================================================="

# 1. Check Python 3.10+
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 was not found on PATH. Please install Python 3.10+."
  exit 1
fi

# 2. Check Node.js / npm
if ! command -v npm >/dev/null 2>&1; then
  echo "[ERROR] Node.js / npm was not found on PATH. Install Node.js 22+ (also used by yt-dlp)."
  exit 1
fi

# 3. Check FFmpeg
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[WARNING] FFmpeg was not found on PATH!"
  echo "          Demo video, YouTube ingest, and MP4 export require FFmpeg."
  echo "          macOS:         brew install ffmpeg"
  echo "          Ubuntu/Debian: sudo apt update && sudo apt install -y ffmpeg"
  echo ""
fi

# 4. Create backend/.env from .env.example if missing
if [ ! -f "backend/.env" ] && [ -f "backend/.env.example" ]; then
  cp "backend/.env.example" "backend/.env"
  echo "[INFO] Created backend/.env from backend/.env.example."
  echo "       Add your GROQ_API_KEY and GEMINI_API_KEY inside backend/.env."
fi

# 5. Create backend/.venv and install Python requirements
if [ ! -x "backend/.venv/bin/python" ]; then
  echo "[INFO] Creating Python virtual environment in backend/.venv..."
  python3 -m venv "backend/.venv"
  "backend/.venv/bin/python" -m pip install --upgrade pip
  "backend/.venv/bin/python" -m pip install -r "backend/requirements.txt"
elif ! "backend/.venv/bin/python" -c "import fastapi, uvicorn, yt_dlp" >/dev/null 2>&1; then
  echo "[INFO] Installing missing backend Python dependencies..."
  "backend/.venv/bin/python" -m pip install -r "backend/requirements.txt"
fi

# 6. Install frontend npm packages if needed
if [ ! -d "frontend/node_modules" ]; then
  echo "[INFO] Installing frontend npm dependencies..."
  (cd frontend && npm install)
fi

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo ""
  echo "[INFO] Shutting down ClipForge servers..."
  if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup INT TERM EXIT

# 7. Launch Backend and Frontend
echo "[INFO] Starting FastAPI backend on http://localhost:8000 ..."
(
  cd "$ROOT_DIR/backend"
  exec "$ROOT_DIR/backend/.venv/bin/python" -m uvicorn main:app --reload --port 8000
) &
BACKEND_PID=$!

echo "[INFO] Starting Vite frontend on http://localhost:5173 ..."
(
  cd "$ROOT_DIR/frontend"
  exec npm run dev
) &
FRONTEND_PID=$!

echo ""
echo "=================================================="
echo "  ClipForge Studio is running! (Press Ctrl+C to stop)"
echo "  Frontend UI:   http://localhost:5173"
echo "  Backend API:   http://localhost:8000/api/health"
echo "  API Keys file: backend/.env"
echo "=================================================="
echo ""

wait
