# NRG-Flux ⚡ — EU Power-Market Data-as-a-Service

Developer-first access to European electricity market data (starting with
**Italy**), plus **explainable D+1..D+7 load forecasts**: clean JSON over one
API, an operator-grade live dashboard, and Excel/Power BI connectors.

Built from the NRG-Flux business project plan: harmonized ENTSO-E data
(day-ahead prices, load, cross-border flows, outages), UTC + market-time
handling with DST golden tests, EIC resolution, data-quality events, webhooks,
and LightGBM quantile forecasts with per-hour SHAP driver attributions.

**Dashboard — "Italy Power Watch"**: live price ticker, zonal price chart,
an animated single-line schematic of the Italian bidding zones with border
imports, probabilistic load forecast vs the TSO's own day-ahead forecast, and
a "why demand moves" SHAP panel with an hour slider.

---

## Quickstart (Docker — recommended)

```bash
docker compose up --build
```

First start seeds 45 days of demo data and trains the forecaster (~1 min).

| URL | What |
|---|---|
| http://localhost:8080 | Italy Power Watch dashboard |
| http://localhost:8080/docs | Interactive API docs (OpenAPI) |
| http://localhost:8000/v1/status | Source freshness / trust dashboard |

Runs fully self-contained on synthetic-but-realistic demo data — no
credentials needed to evaluate everything.

## Quickstart (local dev)

```bash
# backend
cd backend
pip install -r requirements.txt
python scripts/seed_demo.py        # 45 days of demo market data
python scripts/train_forecast.py   # trains + prints backtest + issues forecast
uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev                        # http://localhost:5173 (proxies /v1 to :8000)
```

Tests (includes the DST golden tests the plan gates pilots on):

```bash
cd backend && python -m pytest tests/ -q
```

---

## Going live (real ENTSO-E data)

1. Register at https://transparency.entsoe.eu and request Web API access
   (My Account Settings → Web API Security Token). Note: ENTSO-E must approve
   REST access by email (up to 3 business days) — until then the token
   returns 401.
2. `cp .env.example backend/.env` and set `ENTSOE_API_TOKEN=...`
3. Backfill and switch modes:

```bash
cd backend
python scripts/backfill_entsoe.py --days 30
# set DEMO_MODE=false in backend/.env, restart the API
python scripts/train_forecast.py
```

**GB expansion (NESO):** the repo also ships a NESO Data Portal client for
Great Britain national demand — `python scripts/backfill_neso.py --days 30`
(no key required; `NESO_API_KEY` raises rate limits). GB is registered in the
EIC resolver as `10YGB----------A` and flows through the same API and
forecasting pipeline.

Weather features for live forecasts default to **MET Norway** (needs only an
identifying `METNO_USER_AGENT`); NOAA GFS on AWS, DWD and ECMWF open data
clients are included — see [docs/weather.md](docs/weather.md) for which
services actually need keys (fewer than you'd think) and the
population-weighting upgrade path.

## ⚠️ Security

- **Never commit `.env`** — the `.gitignore` already excludes it.
- **Rotate any credential that has ever been shared in plaintext** (chat,
  email, docs) before deploying. Treat such keys as compromised.
- API keys for your customers go in `API_KEYS` (comma-separated) and are
  checked via the `X-Api-Key` header.

---

## What's inside

```
backend/
  app/
    api/            /v1 routers: market data, forecasting, status/quality/webhooks
    ingestion/      ENTSO-E client (slicing, retries, raw-first) + weather clients
    parsers/        ENTSO-E XML → normalized rows (resolution & curveType aware)
    forecasting/    features, LightGBM p10/p50/p90, TreeSHAP explanations, model card
    quality/        gap / staleness / outlier checks → data-quality events
    demo/           synthetic Italian market generator (demo mode)
    db/             canonical schema (plan §10) — SQLite default, Postgres-ready
    utils/          UTC/market-time + DST handling, EIC registry & resolver
  scripts/          seed_demo.py · train_forecast.py · backfill_entsoe.py
  tests/            DST golden tests, parser tests, EIC scoring, API smoke tests
frontend/           Vite + React + ECharts dashboard (Italy Power Watch)
docs/               excel-powerbi.md · weather.md
```

### API surface (v1)

`/v1/prices/dayahead` · `/v1/load/actual` · `/v1/load/forecast/tso` ·
`/v1/flows/physical` · `/v1/outages` · `/v1/eic/resolve` ·
`/v1/forecast/load` · `/v1/forecast/explain` · `/v1/forecast/whatif` ·
`/v1/forecast/modelcard` · `/v1/status` · `/v1/quality/events` ·
`/v1/webhooks` · `/v1/dashboard/italy`

Every response carries units, UTC + market-day timestamps, lineage
(source/document type/parser version) and quality flags.

### Forecast honesty (plan §9 acceptance gate)

`train_forecast.py` prints a rolling backtest vs the naive previous-week
baseline and writes a model card. On the demo generator: **WAPE 1.2% vs naive
4.9%**. The API exposes the backtest and `beats_naive_baseline` on every
forecast response — if the model can't beat the baseline, don't sell it.

> Demo-mode numbers are on synthetic data and will differ on live data.
> Forecasts are probabilistic decision support, **not for trading decisions**.

## Roadmap vs the 90-day plan

- **Days 0–30 (foundation)** — shipped here: ENTSO-E ingestion, canonical
  schema, DST golden tests, API v1, status page, Excel guide.
- **Days 31–60 (differentiation)** — shipped here: quantile forecasts + SHAP,
  what-if endpoint, webhooks, dashboard.
- **Days 61–90 (commercialization)** — next: API-key portal with usage
  metering, more bidding zones (DE-LU, FR, ES), alerting rules, TimescaleDB
  migration (compose file has the stub), SLA monitoring.

## Attribution & license

Market data © ENTSO-E Transparency Platform (attribution required when
republishing). Weather: MET Norway (CC-BY 4.0), NOAA, DWD, ECMWF open data.
Code: MIT.

to start:
```bash
git clone
cd backend
pip install -r requirements.txt

#download italian market data for the last 30 days (or more)
python3 scripts/backfill_entsoe.py --days 30

python3 scripts/train_forecast.py

#start the application backend
uvicorn app.main:app --port 8000

#open another terminal and start the frontend
cd frontend
npm install
npm run dev

# EUROPE 
# 2 year fill from entso-e power data for Italy, France, Germany and Switzerland
python3 scripts/backfill_eu.py --markets IT FR DE CH --days 730

# weather data for the last 3 years for Italy, France, Germany and Switzerland
python3 scripts/backfill_era5.py --area 10YIT-GRTN-----B   --years 3
python3 scripts/backfill_era5.py --area 10YFR-RTE------C   --years 3
python3 scripts/backfill_era5.py --area 10Y1001A1001A83F   --years 3
python3 scripts/backfill_era5.py --area 10YCH-SWISSGRIDZ   --years 3

#train load prediction models 
python3 scripts/train_forecast.py --area 10YIT-GRTN-----B    # done, 3.04% ✅
python3 scripts/train_forecast.py --area 10YFR-RTE------C
python3 scripts/train_forecast.py --area 10Y1001A1001A83F    # DE control area
python3 scripts/train_forecast.py --area 10YCH-SWISSGRIDZ

#train price prediction models
python3 scripts/train_price_forecast.py --area 10Y1001A1001A73I   # IT NORD
python3 scripts/train_price_forecast.py --area 10YFR-RTE------C   # FR
python3 scripts/train_price_forecast.py --area 10Y1001A1001A82H   # DE-LU zone ≠ load area
python3 scripts/train_price_forecast.py --area 10YCH-SWISSGRIDZ   # CH

#view on 2 terminals
uvicorn app.main:app --port 8000        # terminal 1
cd ../frontend && npm run dev           # terminal 2