"""NRG-Flux API — EU power-market Data-as-a-Service.

Run locally:
    uvicorn app.main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .api import forecast, market, meta
from .config import get_settings
from .db.models import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="NRG-Flux API",
    version="0.1.0",
    description=(
        "Developer-first European power-market data: day-ahead prices, load, "
        "cross-border flows, outages, EIC resolution, and explainable D+1..D+7 "
        "load forecasts with p10/p50/p90 and SHAP attributions.\n\n"
        "**Attribution:** market data © ENTSO-E Transparency Platform. "
        "Weather features: MET Norway, NOAA GFS (AWS Open Data), DWD, ECMWF open data."
    ),
    contact={"name": "NRG-Flux", "url": "https://github.com/your-org/nrg-flux"},
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(market.router)
app.include_router(forecast.router)
app.include_router(meta.router)


@app.get("/", include_in_schema=False)
def root():
    return {"service": "nrg-flux-api", "docs": "/docs",
            "dashboard_feed": "/v1/dashboard/italy", "status": "/v1/status"}
