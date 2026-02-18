#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  python3 -m venv "$ROOT_DIR/.venv"
fi

if [[ ! -f "$ROOT_DIR/backend/.env" ]]; then
  cp "$ROOT_DIR/backend/.env.example" "$ROOT_DIR/backend/.env"
fi

if [[ ! -f "$ROOT_DIR/frontend/.env" ]]; then
  cp "$ROOT_DIR/frontend/.env.example" "$ROOT_DIR/frontend/.env"
fi

echo "Install deps first:"
echo "  $ROOT_DIR/.venv/bin/pip install -r $ROOT_DIR/backend/requirements.txt"
echo "  cd $ROOT_DIR/frontend && npm install"
echo ""
echo "Run in two terminals:"
echo "  Terminal 1: cd $ROOT_DIR/backend && $ROOT_DIR/.venv/bin/uvicorn app.main:app --reload --port 8000"
echo "  Terminal 2: cd $ROOT_DIR/frontend && npm run dev"
