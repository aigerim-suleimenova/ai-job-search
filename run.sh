#!/bin/bash
# Запуск AI Job Search: http://127.0.0.1:8765
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi
exec ./.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8765
