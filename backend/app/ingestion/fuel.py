"""Fuel price ingestion — TTF gas and EUA carbon.

WHY THIS IS NOT AS EASY AS ENTSO-E
----------------------------------
ENTSO-E publishes electricity data free under EU transparency rules. Gas and
carbon prices are NOT covered by that mandate — they are exchange-owned data
(ICE, EEX). There is no free, licensed, official TTF feed. Anyone who tells
you otherwise is scraping.

So this module is deliberately PLUGGABLE, with three providers:

  1. "csv"      — you supply a CSV. Always legal, always works. This is the
                  production path until you license a feed. You can export
                  daily settlements from your broker/exchange terminal, or
                  buy a cheap feed and dump it here.
  2. "yfinance" — Yahoo Finance futures tickers (TTF=F, CO2 proxies).
                  FOR EVALUATION AND BACKTESTING ONLY. Yahoo's terms do not
                  permit commercial redistribution; do not resell this data
                  or put it in a customer-facing response. Use it to prove
                  the feature works, then license a real feed.
  3. "manual"   — insert a flat assumed price (useful for what-if scenarios).

For production, the realistic licensed options are EEX market data, ICE, or
a vendor like Montel. Budget for it — gas is the single biggest driver of
European power prices and the feature that most improves price forecasts.

Units: TTF is quoted in EUR/MWh (thermal). EUA in EUR/tCO2.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger("nrgflux.fuel")

# Yahoo tickers (evaluation only — see module docstring)
YF_TICKERS = {
    "TTF": "TTF=F",     # Dutch TTF Natural Gas Futures (EUR/MWh)
    "EUA": "CO2.L",     # carbon proxy; swap for your licensed EUA series
}


def from_csv(path: str, fuel: str = "TTF") -> pd.Series:
    """Load a fuel price series from CSV.

    Expected columns (case-insensitive): date, price
    Extra columns are ignored. One row per day; forward-filled to hourly later.

        date,price
        2026-07-01,34.10
        2026-07-02,33.85
    """
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if "date" not in df.columns or "price" not in df.columns:
        raise ValueError("CSV needs 'date' and 'price' columns")
    idx = pd.to_datetime(df["date"], utc=True)
    s = pd.Series(pd.to_numeric(df["price"], errors="coerce").values, index=idx)
    return s.dropna().sort_index()


def from_yfinance(fuel: str = "TTF", days: int = 730) -> pd.Series:
    """Evaluation-only fetch via Yahoo Finance. Requires `pip install yfinance`.

    Do NOT redistribute this data to customers. It exists so you can prove the
    gas feature improves the model before paying for a licensed feed.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("pip install yfinance  (evaluation only)")
    ticker = YF_TICKERS.get(fuel.upper())
    if not ticker:
        raise ValueError(f"no ticker mapped for {fuel}")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    log.warning("Fetching %s from Yahoo Finance — EVALUATION ONLY, not for "
                "redistribution. License a feed (EEX/ICE) for production.", fuel)
    df = yf.download(ticker, start=start.date(), end=end.date(),
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"no data returned for {ticker}")
    close = df["Close"]
    if hasattr(close, "columns"):          # yfinance may return a DataFrame
        close = close.iloc[:, 0]
    s = pd.Series(close.values, index=pd.DatetimeIndex(close.index, tz="UTC"))
    return s.dropna().sort_index()


def flat(price: float, days: int = 730) -> pd.Series:
    """A constant assumed price — for scenarios or when you have no feed."""
    end = pd.Timestamp.now(tz="UTC").normalize()
    idx = pd.date_range(end - timedelta(days=days), end, freq="D", tz="UTC")
    return pd.Series([price] * len(idx), index=idx)


def to_hourly(daily: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Expand a daily settlement series onto an hourly index.

    Gas settles once per day; power trades hourly. We forward-fill: the price
    known at the time of the day-ahead auction applies to all delivery hours.
    """
    if daily.empty:
        return pd.Series(index=index, dtype=float)
    d = daily.copy()
    d.index = pd.DatetimeIndex(d.index).tz_convert("UTC").normalize()
    d = d[~d.index.duplicated(keep="last")]
    return d.reindex(index.normalize(), method="ffill").set_axis(index).ffill().bfill()


def fetch(provider: str, fuel: str = "TTF", path: str = "",
          price: float = 35.0, days: int = 730) -> pd.Series:
    """Single entry point used by scripts and the trainer."""
    provider = (provider or "csv").lower()
    if provider == "csv":
        if not path:
            raise ValueError("provider=csv needs --path")
        return from_csv(path, fuel)
    if provider == "yfinance":
        return from_yfinance(fuel, days)
    if provider in ("manual", "flat"):
        return flat(price, days)
    raise ValueError(f"unknown provider: {provider}")
