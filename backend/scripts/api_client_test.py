#!/usr/bin/env python3
"""
NRG-Flux API — client download test.

This is exactly what a CUSTOMER would run to pull data from your service.
Run it against your local API (or your deployed URL) to confirm a key works
and to see every data type download into pandas + CSV files.

Usage:
    # 1. make sure your API is running:  uvicorn app.main:app --port 8000
    # 2. from the backend/ folder:
    pip install requests pandas
    python scripts/api_client_test.py --key demo-key
    # or a real customer key you issued:
    python scripts/api_client_test.py --key nrgf_xxx_yyy --url http://localhost:8000

Every successful call also writes a CSV to ./api_test_output/ so you can open
the downloaded data in Excel and see it's real.
"""
import argparse
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("pip install requests pandas  # then re-run")

IT_NORTH = "10Y1001A1001A73I"
NATIONAL = "10YIT-GRTN-----B"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True, help="your X-Api-Key (e.g. demo-key or nrgf_...)")
    ap.add_argument("--url", default="http://localhost:8000", help="API base URL")
    ap.add_argument("--area", default=IT_NORTH, help="bidding-zone EIC code")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    headers = {"X-Api-Key": args.key}
    outdir = "api_test_output"
    os.makedirs(outdir, exist_ok=True)

    try:
        import pandas as pd
    except ImportError:
        pd = None

    # (label, path, params, json-key-holding-the-list)
    calls = [
        ("Day-ahead prices",   "/v1/prices/dayahead",     {"area": args.area}, "series"),
        ("Actual load",        "/v1/load/actual",         {"area": NATIONAL},  "series"),
        ("TSO load forecast",  "/v1/load/forecast/tso",   {"area": NATIONAL},  "series"),
        ("Load forecast (ML)", "/v1/forecast/load",       {"horizon": 168},    "forecast"),
        ("Forecast drivers",   "/v1/forecast/explain",    {},                  "points"),
        ("Outages",            "/v1/outages",             {},                  "series"),
        ("Dashboard feed",     "/v1/dashboard/italy",     {},                  None),
    ]

    print(f"\n  NRG-Flux API test  ->  {base}")
    print(f"  Key: {args.key[:14]}{'...' if len(args.key) > 14 else ''}\n" + "-" * 58)

    # 1) auth / health probe
    try:
        r = requests.get(f"{base}/v1/status", timeout=15)
    except requests.exceptions.ConnectionError:
        sys.exit(f"\n  Cannot reach {base} — is the API running?\n"
                 f"  Start it with:  uvicorn app.main:app --port 8000\n")
    status = r.json()
    print(f"  Service reachable. demo_mode = {status.get('demo_mode')}")
    for ds, info in status.get("datasets", {}).items():
        print(f"    {ds:<18} rows={info.get('rows'):>7}  lag={info.get('freshness_lag_min')} min")
    print("-" * 58)

    ok = 0
    for label, path, params, key in calls:
        try:
            resp = requests.get(f"{base}{path}", params=params, headers=headers, timeout=30)
        except Exception as e:
            print(f"  [ERR ] {label:<20} {e}")
            continue

        if resp.status_code == 401:
            print(f"  [401 ] {label:<20} key rejected — check --key")
            continue
        if resp.status_code == 429:
            print(f"  [429 ] {label:<20} rate-limited (plan quota) — expected under load")
            continue
        if resp.status_code != 200:
            print(f"  [{resp.status_code} ] {label:<20} {resp.text[:80]}")
            continue

        data = resp.json()
        rows = data.get(key, data) if key else data
        n = len(rows) if isinstance(rows, list) else 1
        print(f"  [ OK ] {label:<20} {n} record(s)")
        ok += 1

        # write CSV so you can open it in Excel
        if pd is not None and isinstance(rows, list) and rows:
            try:
                fn = os.path.join(outdir, path.strip("/").replace("/", "_") + ".csv")
                pd.json_normalize(rows).to_csv(fn, index=False)
            except Exception:
                pass

    print("-" * 58)
    print(f"  {ok}/{len(calls)} endpoints returned data.")
    if pd is not None:
        print(f"  CSVs written to ./{outdir}/  — open them in Excel to verify.")
    else:
        print("  (pip install pandas to also export CSVs)")
    print()


if __name__ == "__main__":
    main()
