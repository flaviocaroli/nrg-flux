"""NRG-Flux configuration.

All credentials come from environment variables (see .env.example).
NEVER hardcode tokens in this repository — the repo is meant to be public.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- runtime ---
    app_name: str = "NRG-Flux API"
    environment: str = "development"          # development | production
    demo_mode: bool = True                    # serve synthetic data until real ingestion is configured
    database_url: str = "sqlite:///./nrgflux.db"
    cors_origins: str = "*"                   # comma separated in production
    api_keys: str = "demo-key"                # comma separated list of accepted API keys

    # --- ENTSO-E Transparency Platform (primary market-data source) ---
    # Register at https://transparency.entsoe.eu -> "My Account Settings" -> Web API token
    entsoe_api_token: str = ""
    entsoe_base_url: str = "https://web-api.tp.entsoe.eu/api"
    entsoe_max_days_per_request: int = 30     # request slicing window
    entsoe_rps: float = 0.5                   # polite request rate (req/sec)

    # --- Weather sources for forecasting features ---
    # MET Norway: requires an identifying User-Agent (https://api.met.no/doc/TermsOfService)
    metno_user_agent: str = "nrg-flux/0.1 contact@example.com"
    metno_client_id: str = ""                 # optional, for authenticated endpoints
    metno_api_key: str = ""

    # ECMWF open data (https://www.ecmwf.int/en/forecasts/datasets/open-data)
    # The open-data feed is free; a key is only needed for licensed/archived datasets.
    ecmwf_api_key: str = ""
    ecmwf_base_url: str = "https://data.ecmwf.int/forecasts"

    # DWD Open Data (https://opendata.dwd.de) — no key required
    dwd_base_url: str = "https://opendata.dwd.de"

    # NOAA GFS on AWS Open Data (anonymous S3) + optional NOAA token for CDO/NCEI services
    noaa_gfs_bucket: str = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
    noaa_api_token: str = ""

    # Copernicus CDS (ERA5 reanalysis) — free registration, format "<uid>:<key>"
    # https://cds.climate.copernicus.eu  (accept the ERA5 licence too)
    cds_api_key: str = ""

    # NESO (GB National Energy System Operator) Data Portal — CKAN API.
    # Most open datasets need no key; a key raises rate limits / enables
    # authenticated endpoints. https://www.neso.energy/data-portal
    neso_base_url: str = "https://api.neso.energy"
    neso_api_key: str = ""

    # --- forecasting ---
    model_dir: str = "./models"
    forecast_default_area: str = "10YIT-GRTN-----B"

    # --- webhooks ---
    webhook_signing_secret: str = "change-me"

    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
