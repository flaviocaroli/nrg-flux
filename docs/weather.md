# Weather pipeline: current state and upgrade path

Forecast features need two things: **history** for training and a **forward
forecast** for D+1..D+7 inference (plan §9, steps 2–3).

## What ships today

| Need | Source in this repo | Notes |
|---|---|---|
| Forward forecast | **MET Norway Locationforecast** at zone centroids | Clean hourly JSON, ~9 days ahead, global. Requires only an identifying `User-Agent` (set `METNO_USER_AGENT`). This is the default production path in `app/ingestion/weather.py::zone_temperature_forecast`. |
| Forward forecast (alt) | **NOAA GFS** on AWS (`noaa-gfs-bdp-pds`) | Anonymous S3, 4 runs/day. Client lists cycles and downloads GRIB2; decoding needs `pip install cfgrib xarray`. |
| Forward forecast (alt) | **ECMWF open data** | Free subset; `pip install ecmwf-opendata`, see `ecmwf_retrieve_t2m`. Higher skill than GFS at these horizons. |
| Forward forecast (alt) | **DWD ICON-EU** | No key, `dwd_icon_index()` lists GRIB files. Good over central Europe. |
| Training history | Synthetic in demo mode | Same generative process as the seeded DB. |

## A note on the credentials you hold

- **MET Norway** locationforecast does not use an API key — identification is
  via the `User-Agent` header. Keep any client credentials for their
  authenticated products only.
- **NOAA GFS on AWS** is anonymous; no token is sent. A NOAA token belongs to
  the separate NCEI/CDO climate web services.
- **ECMWF open data** needs no key; keys apply to licensed/archive datasets
  (e.g. full-resolution or historical MARS retrievals).
- **DWD** open data is keyless by design.

Set whichever of these you use in `backend/.env` — never in code.

## Production upgrade path (plan §9 steps 4–5)

1. **ERA5 history** — register at the Copernicus Climate Data Store, install
   `cdsapi`, and backfill 2+ years of hourly 2m temperature / dewpoint / wind /
   radiation over Italy. Store per-grid-cell Parquet in object storage.
2. **Zone polygons** — approximate bidding-zone boundaries from region
   administrative borders (NORD ≈ northern regions, etc.).
3. **Population weighting** — download the Eurostat GISCO 1 km population
   grid, overlay with the weather grid (rasterio/xarray), and compute
   population-weighted zone temperature and HDD/CDD. This is what makes the
   temperature feature match where the load actually is.
4. **Multi-provider blend** — average MET Norway + ECMWF + GFS at matched
   valid-times; monitor per-provider drift in the data-quality dashboard
   (plan §15, weather risk mitigation).
