#!/bin/sh
# Seed demo data + train the model on first start (skipped if DB already exists
# or DEMO_MODE=false). Then launch the API.
set -e
DB_FILE=$(echo "${DATABASE_URL:-sqlite:///./nrgflux.db}" | sed 's|sqlite:///||;s|sqlite:////|/|')
mkdir -p "$(dirname "$DB_FILE")" "${MODEL_DIR:-./models}" 2>/dev/null || true

if [ "${DEMO_MODE:-true}" = "true" ] && [ ! -s "$DB_FILE" ]; then
  echo "[nrg-flux] first start: seeding demo data and training the forecaster ..."
  python scripts/seed_demo.py
  python scripts/train_forecast.py
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
