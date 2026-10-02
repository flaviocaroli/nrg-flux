"""Reissue forecasts from the latest ingested data without retraining models.

This is the cheap hourly operation. Model fitting remains a daily job.
Existing model artifacts are loaded from MODEL_DIR; markets without a trained
model are skipped cleanly.

Environment:
  NRGFLUX_MARKETS   comma list, default IT,FR,DE,CH
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import get_settings  # noqa: E402
from app.services.forecast_service import issue_forecast, issue_price_forecast  # noqa: E402
from app.utils.eic import EU_MARKETS  # noqa: E402


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] forecast-refresh: {msg}",
          flush=True)


def main() -> int:
    settings = get_settings()
    model_dir = Path(settings.model_dir)
    markets = [
        cc.strip().upper()
        for cc in os.environ.get("NRGFLUX_MARKETS", "IT,FR,DE,CH").split(",")
        if cc.strip().upper() in EU_MARKETS
    ]

    attempted = 0
    failed = 0

    # Load forecasts first: price forecasts can use them beyond TSO D+1.
    for cc in markets:
        market = EU_MARKETS[cc]
        area = market["national"]
        card = model_dir / f"model_card_{area}.json"
        if not card.exists():
            log(f"skip load {cc}: no trained model card ({card.name})")
            continue
        attempted += 1
        try:
            summary = issue_forecast(area, horizon_hours=168)
            log(f"load {cc}: issued {summary['points']} points at {summary['issued_at_utc']}")
        except Exception as exc:
            failed += 1
            log(f"load {cc}: FAILED: {exc}")

    # Refresh every price model that actually exists. Italy may have only the
    # North-zone model; in that case the other five zones are simply skipped.
    for cc in markets:
        market = EU_MARKETS[cc]
        for zone in market["zones"]:
            card = model_dir / f"model_card_price_{zone}.json"
            if not card.exists():
                continue
            attempted += 1
            try:
                summary = issue_price_forecast(zone, horizon_hours=48)
                log(f"price {cc} {zone}: issued {summary['points']} points at "
                    f"{summary['issued_at_utc']}")
            except Exception as exc:
                failed += 1
                log(f"price {cc} {zone}: FAILED: {exc}")

    if attempted == 0:
        log("no trained model artifacts found for the configured markets")
        return 0
    log(f"done: attempted={attempted}, failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
