# NRG-Flux

NRG-Flux is an explainable European electricity-market intelligence and energy-cost demonstration platform. It combines market data, load and price forecasts, cross-border flows, generation outages, weather-derived features, and a browser-based consumption-profile cost benchmark.

The current demonstration is centred on Italy and includes a European market explorer for Italy, France, Germany, Switzerland, Austria, Slovenia, Greece, Belgium, the Netherlands, Spain and Great Britain.

## What the application currently provides

- Italian and European electricity-market exploration.
- Day-ahead prices where the selected source provides them.
- Actual load and TSO load forecasts.
- Cross-border physical-flow data.
- Italian generation and transmission outage information.
- Explainable probabilistic load forecasts with P10, P50 and P90 values.
- Price-forecast models for supported bidding zones.
- A CSV profile-to-cost benchmark for an auditable customer demonstration.
- Data provenance, update times and explicit missing-data states.
- A temporary public HTTPS demonstration link through Cloudflare Quick Tunnel.

Great Britain currently uses NESO national demand data. The interface must not invent a GB price series when a suitable price source is unavailable.

## Important forecast status

The application can train and issue forecasts, but public accuracy claims must come from saved, versioned evaluation evidence.

The present Italian LightGBM model uses domestic calendar, temperature and historical-load features. European data is visible in the platform, but the current production Italian model does not yet prove that neighbouring-market variables improve Italian forecasts.

The planned evaluation compares, on identical forecast origins and target timestamps:

1. Weekly seasonal-naive forecast.
2. ENTSO-E or TSO published forecast, when its historical issue time can be proven.
3. Domestic-feature LightGBM.
4. European-feature LightGBM.
5. SARIMAX.
6. Dynamic linear or structural state-space model.

Until that evaluation is complete, model cards and current holdout results should be described as preliminary development evidence.

## Forecast Arena benchmark

The `italy-models` work adds a saved, reproducible 24-hour comparison for Italy.
It evaluates weekly seasonal naive, the retrieved ENTSO-E/TSO revision,
domestic and cumulative five-market LightGBM variants, SARIMAX and a dynamic
linear model on common timestamps across four rolling origins.

The historical benchmark uses only information available by each D+1 cutoff.
Observed target-hour weather is excluded, and missing European load or flow
features are never replaced with zero. TSO results remain `PRELIMINARY` until
the database preserves immutable publication revisions.

Generate the evidence artifact from the backend directory:

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:DATABASE_URL = "sqlite:///C:/Users/hp/nrgflux-data/nrgflux.db"
python scripts\benchmark_load_models.py --folds 4 --spacing-days 7
```

The command writes under `backend/models/benchmarks/` atomically. The API only
serves that saved artifact at:

```text
GET /v1/forecast/benchmark?area=10YIT-GRTN-----B
```

The React Forecast Arena reads this endpoint; it does not calculate or select
headline metrics in the browser.

## Repository structure

```text
nrg-flux/
├── backend/
│   ├── app/                  FastAPI application, database and forecasting code
│   ├── scripts/              Backfill, training and scheduler commands
│   ├── tests/                Backend regression tests
│   └── requirements.txt
├── frontend/
│   ├── src/                  React application
│   ├── vite.config.js
│   └── package.json
├── docker-compose.yml
└── README.md
```

## Current Windows development environment

The commands below assume:

```text
Repository: C:\Users\hp\Desktop\nrg-x\nrg-flux
Python:     C:\Users\hp\.venvs\nrgflux\Scripts\python.exe
Database:   C:\Users\hp\nrgflux-data\nrgflux.db
```

The active database intentionally lives outside the Git repository. Do not move it into Git or commit it.

## Prerequisites

- Windows 10 or 11.
- PowerShell.
- Git.
- Python virtual environment with `backend/requirements.txt` installed.
- Node.js and npm.
- SQLite command-line utility for integrity checks.
- ENTSO-E API token for live ENTSO-E ingestion.
- A free Cloudflare Quick Tunnel requires no Cloudflare account for temporary demonstration use.

## First-time installation

### Backend

```powershell
$Repo = "C:\Users\hp\Desktop\nrg-x\nrg-flux"
$Backend = Join-Path $Repo "backend"
$Venv = Join-Path $env:USERPROFILE ".venvs\nrgflux"
$Py = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Py)) {
    python -m venv $Venv
}

& $Py -m pip install --upgrade pip
& $Py -m pip install -r "$Backend\requirements.txt"
```

### Frontend

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\frontend"
npm install
```

## Run the local and public demonstration

The complete demonstration uses three terminals. Start them in the following order and leave all three running.

### Terminal 1 — backend

Copy and run this entire block in PowerShell:

```powershell
$ErrorActionPreference = "Stop"

$Repo = "C:\Users\hp\Desktop\nrg-x\nrg-flux"
$Backend = Join-Path $Repo "backend"
$Py = Join-Path $env:USERPROFILE ".venvs\nrgflux\Scripts\python.exe"
$DbNative = Join-Path $env:USERPROFILE "nrgflux-data\nrgflux.db"
$DbUriPath = $DbNative -replace "\\", "/"

if (-not (Test-Path -LiteralPath $Py)) {
    throw "Python environment not found: $Py"
}

if (-not (Test-Path -LiteralPath $DbNative)) {
    throw "Database not found: $DbNative"
}

Set-Location $Backend

$env:PYTHONPATH = $Backend
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:DATABASE_URL = "sqlite:///$DbUriPath"
$env:DEMO_MODE = "false"

Write-Host "DATABASE=$DbNative"
Write-Host "Starting backend: http://127.0.0.1:8000"

& $Py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected result:

```text
Uvicorn running on http://127.0.0.1:8000
```

Local API documentation:

```text
http://127.0.0.1:8000/docs
```

### Terminal 2 — frontend

```powershell
$ErrorActionPreference = "Stop"

Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\frontend"

npm run dev -- --host 127.0.0.1 --strictPort
```

Expected local URL:

```text
http://127.0.0.1:5173
```

Open the local URL and confirm that the application loads before starting the tunnel.

### Terminal 3 — Cloudflare temporary HTTPS link

```powershell
$ErrorActionPreference = "Stop"

Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\frontend"

npx --yes wrangler@4.144.0 tunnel quick-start http://127.0.0.1:5173
```

The command prints a temporary URL similar to:

```text
https://random-words.trycloudflare.com
```

The URL works only while:

- The computer is awake and online.
- The backend terminal is running.
- The frontend terminal is running.
- The Cloudflare terminal is running.

The URL changes whenever the Quick Tunnel is restarted.

## Vite configuration for Cloudflare

`frontend/vite.config.js` should contain:

```javascript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    allowedHosts: ['.trycloudflare.com'],
    proxy: {
      '/v1': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
```

The leading dot in `.trycloudflare.com` permits temporary subdomains. Do not add a trailing space, and do not use `allowedHosts: true`.

Only the frontend needs a public tunnel. Requests beginning with `/v1` are proxied locally from Vite to FastAPI.

## Verify the running system

Check these addresses:

```text
Local frontend:  http://127.0.0.1:5173
Local API docs:  http://127.0.0.1:8000/docs
Local status:    http://127.0.0.1:8000/v1/status
Public frontend: https://random-words.trycloudflare.com
Public status:   https://random-words.trycloudflare.com/v1/status
```

If the public status address returns JSON, the browser-to-database path is working.

## Common startup errors

### Port 5173 is already in use

An earlier Vite process is still running. Locate it with:

```powershell
Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess
```

Close the earlier frontend terminal before starting another one.

### Port 8000 is already in use

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess
```

Close the earlier backend terminal.

### Cloudflare returns 502 Bad Gateway

The tunnel cannot reach Vite. Confirm that Terminal 2 is still running at `127.0.0.1:5173`.

### Vite reports that the host is not allowed

Confirm that the Vite configuration contains exactly:

```javascript
allowedHosts: ['.trycloudflare.com'],
```

Restart the Vite terminal after changing the configuration.

### The page loads but market data fails

Open the local and public `/v1/status` addresses. Inspect Terminal 1 for database or API errors.

## Database configuration and integrity

The active database is:

```text
C:\Users\hp\nrgflux-data\nrgflux.db
```

Check it while ingestion and training writers are stopped:

```powershell
$Db = Join-Path $env:USERPROFILE "nrgflux-data\nrgflux.db"
$Sqlite = (Get-Command sqlite3.exe -ErrorAction Stop).Source

& $Sqlite $Db "PRAGMA quick_check;"
```

Expected result:

```text
ok
```

Do not continue ingestion or training when the result is not `ok`.

### Create a safe SQLite checkpoint

Stop ingestion, schedulers, training jobs and the API before creating a checkpoint.

```powershell
$ErrorActionPreference = "Stop"

$Db = Join-Path $env:USERPROFILE "nrgflux-data\nrgflux.db"
$CheckpointRoot = Join-Path $env:USERPROFILE "nrgflux-checkpoints"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Checkpoint = Join-Path $CheckpointRoot "nrgflux-$Stamp.db"

New-Item -ItemType Directory -Path $CheckpointRoot -Force | Out-Null

@'
import pathlib
import sqlite3
import sys

source_path = pathlib.Path(sys.argv[1]).resolve()
backup_path = pathlib.Path(sys.argv[2]).resolve()

source = sqlite3.connect(source_path)
destination = sqlite3.connect(backup_path)

try:
    source.backup(destination)
finally:
    destination.close()
    source.close()

print(backup_path)
'@ | & "$env:USERPROFILE\.venvs\nrgflux\Scripts\python.exe" - $Db $Checkpoint

sqlite3.exe $Checkpoint "PRAGMA integrity_check;"
Get-FileHash $Checkpoint -Algorithm SHA256
```

Do not copy only a live SQLite database file when write-ahead logging is active.

## Environment variables

Keep credentials in `backend/.env` or in session environment variables. Never commit the real `.env` file.

Typical settings:

```dotenv
DEMO_MODE=false
DATABASE_URL=sqlite:///C:/Users/hp/nrgflux-data/nrgflux.db
ENTSOE_API_TOKEN=replace-with-your-token
MODEL_DIR=./models
FORECAST_DEFAULT_AREA=10YIT-GRTN-----B
```

Never commit:

- ENTSO-E tokens.
- API keys.
- SSH private keys.
- Customer CSV files.
- SQLite databases.
- Raw audit exports containing sensitive information.

## Supported markets

| Code | Market | Main load area |
|---|---|---|
| IT | Italy | `10YIT-GRTN-----B` |
| FR | France | `10YFR-RTE------C` |
| DE | Germany–Luxembourg | `10Y1001A1001A83F` |
| CH | Switzerland | `10YCH-SWISSGRIDZ` |
| GB | Great Britain | `10YGB----------A` |
| ES | Spain | `10YES-REE------0` |
| BE | Belgium | `10YBE----------2` |
| AT | Austria | `10YAT-APG------L` |
| SI | Slovenia | `10YSI-ELES-----O` |
| GR | Greece | `10YGR-HTSO-----Y` |
| NL | Netherlands | `10YNL----------L` |

## Data ingestion

Always run database-writing jobs sequentially. Do not run multiple SQLite backfills, retraining jobs or the scheduler concurrently.

### Inspect current command options

The backfill scripts have changed during hardening. Before a long ingestion, inspect the options from the checked-out branch:

```powershell
$Repo = "C:\Users\hp\Desktop\nrg-x\nrg-flux"
$Backend = Join-Path $Repo "backend"
$Py = Join-Path $env:USERPROFILE ".venvs\nrgflux\Scripts\python.exe"

Set-Location $Backend

& $Py scripts\backfill_entsoe.py --help
& $Py scripts\backfill_eu.py --help
& $Py scripts\backfill_neso.py --help
& $Py scripts\backfill_era5.py --help
```

### Short Italian ingestion

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\backend"
$Py = Join-Path $env:USERPROFILE ".venvs\nrgflux\Scripts\python.exe"

$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:DATABASE_URL = "sqlite:///C:/Users/hp/nrgflux-data/nrgflux.db"

& $Py -X utf8 scripts\backfill_entsoe.py --days 7
```

### European ingestion

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\backend"
$Py = Join-Path $env:USERPROFILE ".venvs\nrgflux\Scripts\python.exe"

$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:DATABASE_URL = "sqlite:///C:/Users/hp/nrgflux-data/nrgflux.db"

& $Py -X utf8 scripts\backfill_eu.py `
    --markets FR DE CH AT SI GR BE ES NL `
    --days 30
```

### Great Britain demand from NESO

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\backend"
$Py = Join-Path $env:USERPROFILE ".venvs\nrgflux\Scripts\python.exe"

$env:PYTHONPATH = (Get-Location).Path
$env:DATABASE_URL = "sqlite:///C:/Users/hp/nrgflux-data/nrgflux.db"

& $Py -X utf8 scripts\backfill_neso.py --days 60
```

### Weather history

Example:

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\backend"
$Py = Join-Path $env:USERPROFILE ".venvs\nrgflux\Scripts\python.exe"

$env:PYTHONPATH = (Get-Location).Path
$env:DATABASE_URL = "sqlite:///C:/Users/hp/nrgflux-data/nrgflux.db"

& $Py -X utf8 scripts\backfill_era5.py `
    --area 10YIT-GRTN-----B `
    --years 1
```

ERA5 is historical reanalysis. Do not describe observed ERA5 weather as an archived weather forecast in issue-time model evaluations.

## Forecast training

Do not train while another process is writing to SQLite. Create a checkpoint first.

### Train one load model

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\backend"
$Py = Join-Path $env:USERPROFILE ".venvs\nrgflux\Scripts\python.exe"

$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:DATABASE_URL = "sqlite:///C:/Users/hp/nrgflux-data/nrgflux.db"

& $Py -X utf8 scripts\train_forecast.py `
    --area 10YIT-GRTN-----B `
    --source db
```

### Train one price model

Italian North bidding-zone example:

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\backend"
$Py = Join-Path $env:USERPROFILE ".venvs\nrgflux\Scripts\python.exe"

$env:PYTHONPATH = (Get-Location).Path
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:DATABASE_URL = "sqlite:///C:/Users/hp/nrgflux-data/nrgflux.db"

& $Py -X utf8 scripts\train_price_forecast.py `
    --area 10Y1001A1001A73I
```

### Continuous scheduler

The scheduler performs recurring ingestion and forecasting. Run it only when no manual backfill or training command is writing to the database:

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\backend"
$Py = Join-Path $env:USERPROFILE ".venvs\nrgflux\Scripts\python.exe"

$env:PYTHONPATH = (Get-Location).Path
$env:DATABASE_URL = "sqlite:///C:/Users/hp/nrgflux-data/nrgflux.db"

& $Py -X utf8 scripts\run_scheduler.py
```

## Current load-forecast model

The current model trains independent LightGBM quantile models for P10, P50 and P90.

Current domestic features include:

- Hour of day.
- Weekday and month.
- Weekend, holiday and daylight-saving flags.
- Temperature.
- Heating and cooling degree values.
- Temperature anomaly.
- Load lagged by 24 hours.
- Load lagged by 168 hours.
- Trailing 24-hour and 168-hour mean load.

Forecast intervals are adjusted through conformal calibration.

The next validation stage will add only issue-time-safe European features and compare them against the unchanged domestic model. Directly relevant markets are France, Switzerland, Austria, Slovenia and Greece, with Germany as an upstream driver.

## Profile-to-cost benchmark

The browser workflow accepts a documented consumption profile and calculates a spot-energy benchmark.

Minimum CSV contract:

```csv
timestamp_utc,energy_kwh
2026-10-01T08:00:00Z,100
2026-10-01T09:00:00Z,200
```

Rules:

- Timestamps must contain an explicit UTC marker or timezone offset.
- Timestamps identify interval starts.
- Energy is expressed in kWh.
- Duplicate and missing intervals must be reported.
- Units must never be guessed.
- Incomplete price coverage must not be filled with zero.
- Negative electricity prices remain valid.
- Uploaded customer data should not be retained by default.

For interval (t):

```text
energy_mwh[t] = energy_kwh[t] / 1000
cost_eur[t] = energy_mwh[t] × price_eur_mwh[t]
total_cost_eur = sum(cost_eur[t])
```

The result is a spot-energy benchmark, not a final electricity bill. Network charges, taxes, supplier margins, imbalance costs and contractual components are outside this calculation unless explicitly added.

## Tests and production build

### Backend tests

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\backend"
$Py = Join-Path $env:USERPROFILE ".venvs\nrgflux\Scripts\python.exe"

$env:PYTHONPATH = (Get-Location).Path
& $Py -m pytest tests\test_core.py -q
```

### Frontend production build

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux\frontend"
npm run build
```

The Vite bundle-size message is currently a warning, not a build failure. Code splitting can be handled after the presentation-critical workflow is stable.

## Git workflow

Current European-expansion work is developed on branch `europe`.

Before committing:

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux"

git branch --show-current
git status --short
git diff --check
```

Commit only reviewed source and documentation files. Do not use broad `git add .` when databases, logs, audit material or credentials may be present.

Example README and Vite commit:

```powershell
Set-Location "C:\Users\hp\Desktop\nrg-x\nrg-flux"

git add -- README.md frontend\vite.config.js
git diff --cached --check
git diff --cached --stat
git commit -m "Document public demo workflow"
git push origin europe
```

## Security and data handling

- Never commit secrets or private SSH keys.
- Never expose the SQLite database directly to the internet.
- The Cloudflare tunnel exposes Vite; Vite proxies only application API paths to the local backend.
- Do not leave temporary public tunnels running unattended.
- Do not retain uploaded customer consumption profiles by default.
- Validate file type, size, schema and row count before processing uploads.
- Public dashboards must display data sources, update times and missing-data states.
- Unknown or invalid outage values must not be converted to zero.

## Presentation-safe claims

Safe claims:

- The platform harmonizes multiple European market datasets.
- The dashboard exposes sources, timestamps and missing-data states.
- The application provides an auditable consumption-profile cost workflow.
- The forecasting pipeline supports probabilistic models and model cards.
- The product is being evaluated against explicit baselines.

Claims requiring completed evidence:

- European inputs improve Italian forecast accuracy.
- A model beats the TSO forecast.
- A particular WAPE applies outside its exact saved evaluation window.
- P10–P90 intervals achieve nominal coverage prospectively.
- Current forecasts would have been available at historical issue times.

## Forecast benchmark acceptance criteria

A public model-comparison table should use identical target timestamps and report:

- WAPE.
- MAE.
- RMSE.
- Peak-hour MAE.
- P10–P90 empirical coverage.
- Mean prediction-interval width.
- Skill relative to the weekly seasonal-naive forecast.
- Number of evaluated points.
- Evaluation window and horizon.
- Model and dataset versions.

A model must be withheld or marked for review when there is data leakage, insufficient common coverage, optimizer failure, invalid intervals, unproven TSO issue times or inconsistent performance across rolling windows.



## Temporary-demo limitations

Cloudflare Quick Tunnel is appropriate for a short supervised presentation but is not permanent hosting. The random hostname has no uptime guarantee and changes after restarting the tunnel.

After the presentation, move the frontend, backend and database to a persistent deployment with:

- A stable domain.
- HTTPS termination.
- Process supervision.
- Automated backups.
- Secrets management.
- Database migrations.
- Monitoring and structured logs.
- Access controls for customer data.

## Licence

Add the selected project licence before public or commercial distribution. Third-party data remains subject to its original provider's terms and attribution requirements.
