"""Day-ahead PRICE forecaster (premium module, sits on top of the load model).

Design decisions worth knowing before you sell this:

* Prices are far less predictable than load. We therefore ship a QUANTILE
  model (p10/p50/p90) and sell the *distribution*, never a point number.
* The strongest honest driver we own is our own load forecast: price is
  largely a function of residual demand against a supply stack. So the load
  p50 (and its ramp) are first-class features here.
* Every response carries its backtest vs a naive baseline plus a `skill`
  label. If the model cannot beat the naive baseline, the API says so — the
  same gate the load model uses.
* Known blind spots (documented in the model card, not hidden): fuel-price
  shocks, scarcity spikes, negative-price hours driven by renewables gluts,
  and market-design changes. Spike hours are where WAPE degrades most.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd

from .features import calendar_frame

PRICE_MODEL_VERSION = "0.1.0"
QUANTILES = {"p10": 0.10, "p50": 0.50, "p90": 0.90}

PRICE_FEATURES = [
    "hour", "weekday", "month", "is_weekend", "is_holiday",
    "load_p50", "load_ramp_3h", "load_vs_week_mean",
    "price_lag_24h", "price_lag_168h",
    "price_roll_24h_mean", "price_roll_168h_mean", "price_roll_24h_std",
    # --- fuel block: the marginal generator in EU power is usually gas ---
    "gas_ttf", "gas_ttf_chg_7d", "spark_spread",
]

# Efficiency of a modern CCGT. spark_spread = power - gas/efficiency, i.e. the
# margin of the marginal gas plant. When it collapses, gas sets the price.
CCGT_EFFICIENCY = 0.55

LGB_PARAMS = dict(
    objective="quantile", metric="quantile", learning_rate=0.05,
    num_leaves=31, min_data_in_leaf=60, feature_fraction=0.85,
    bagging_fraction=0.85, bagging_freq=1, verbosity=-1, seed=42,
)

FRIENDLY_PRICE = {
    "hour": "hour of day", "weekday": "day of week", "month": "month",
    "is_weekend": "weekend", "is_holiday": "public holiday",
    "load_p50": "our load forecast", "load_ramp_3h": "load ramp (3h)",
    "load_vs_week_mean": "demand vs weekly average",
    "price_lag_24h": "price yesterday, same hour",
    "price_lag_168h": "price last week, same hour",
    "price_roll_24h_mean": "24h average price",
    "price_roll_168h_mean": "7-day average price",
    "price_roll_24h_std": "recent price volatility",
    "gas_ttf": "TTF gas price",
    "gas_ttf_chg_7d": "gas price change (7d)",
    "spark_spread": "spark spread (CCGT margin)",
}


def wape(a: np.ndarray, p: np.ndarray) -> float:
    return float(np.abs(a - p).sum() / np.abs(a).sum() * 100)


def pinball(a: np.ndarray, p: np.ndarray, q: float) -> float:
    d = a - p
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def _build_frame(price: pd.Series, load: pd.Series,
                 gas: pd.Series | None = None) -> pd.DataFrame:
    """Feature matrix aligned on the price index."""
    df = calendar_frame(price.index)
    df["y"] = price.values

    ld = load.reindex(price.index).interpolate(limit=6).ffill().bfill()
    df["load_p50"] = ld.values
    df["load_ramp_3h"] = ld.diff(3).fillna(0).values
    df["load_vs_week_mean"] = (ld - ld.rolling(168, min_periods=1).mean()).values

    df["price_lag_24h"] = price.shift(24).values
    df["price_lag_168h"] = price.shift(168).values
    df["price_roll_24h_mean"] = price.shift(1).rolling(24, min_periods=1).mean().values
    df["price_roll_168h_mean"] = price.shift(1).rolling(168, min_periods=1).mean().values
    df["price_roll_24h_std"] = price.shift(1).rolling(24, min_periods=2).std().fillna(0).values
    _attach_gas(df, price.index, gas, price.shift(24))   # lagged: no leakage
    return df.dropna(subset=["price_lag_168h"])


def _attach_gas(df: pd.DataFrame, idx, gas: pd.Series | None,
                power_known: pd.Series | None = None) -> None:
    """Add the fuel block.

    CRITICAL — no target leakage: `spark_spread` must be built from a price
    that is KNOWN at forecast time (yesterday's same-hour price), never the
    contemporaneous price, which is the target itself. Using the current price
    here would let the model read the answer and produce backtest results that
    collapse in production.
    """
    if gas is None or len(gas) == 0:
        df["gas_ttf"] = 0.0
        df["gas_ttf_chg_7d"] = 0.0
        df["spark_spread"] = 0.0
        return
    from ..ingestion.fuel import to_hourly
    g = to_hourly(gas, idx)
    df["gas_ttf"] = g.values
    df["gas_ttf_chg_7d"] = g.diff(24 * 7).fillna(0).values
    if power_known is not None:
        # margin of the marginal CCGT, using the last price we actually knew
        df["spark_spread"] = (power_known.values - g.values / CCGT_EFFICIENCY)
    else:
        df["spark_spread"] = 0.0


@dataclass
class PriceTrainResult:
    metrics: dict
    model_card: dict


class PriceForecaster:
    """Quantile day-ahead price model for one bidding zone."""

    def __init__(self, model_dir: str, area_eic: str):
        self.model_dir = model_dir
        self.area_eic = area_eic
        self.boosters: dict[str, lgb.Booster] = {}
        self.model_card: dict = {}

    def _path(self, q: str) -> str:
        return os.path.join(self.model_dir, f"lgbm_price_{self.area_eic}_{q}.txt")

    def _card_path(self) -> str:
        return os.path.join(self.model_dir, f"model_card_price_{self.area_eic}.json")

    # ------------------------------------------------------------- training

    def train(self, price: pd.Series, load: pd.Series,
              gas: pd.Series | None = None, n_rounds: int = 350) -> PriceTrainResult:
        df = _build_frame(price, load, gas)
        if len(df) < 24 * 30:
            raise RuntimeError("need at least ~30 days of overlapping price+load history")
        cutoff = df.index[-1] - pd.Timedelta(weeks=4)
        train_df, test_df = df[df.index <= cutoff], df[df.index > cutoff]
        if test_df.empty:
            train_df, test_df = df.iloc[:-168], df.iloc[-168:]

        for name, alpha in QUANTILES.items():
            params = {**LGB_PARAMS, "alpha": alpha}
            dtrain = lgb.Dataset(train_df[PRICE_FEATURES], label=train_df["y"])
            self.boosters[name] = lgb.train(params, dtrain, num_boost_round=n_rounds)

        y = test_df["y"].to_numpy()
        p50 = self.boosters["p50"].predict(test_df[PRICE_FEATURES])
        p10 = self.boosters["p10"].predict(test_df[PRICE_FEATURES])
        p90 = self.boosters["p90"].predict(test_df[PRICE_FEATURES])
        naive = test_df["price_lag_168h"].to_numpy()      # last week, same hour
        naive_d1 = test_df["price_lag_24h"].to_numpy()    # yesterday, same hour

        metrics = {
            "wape_model": round(wape(y, p50), 2),
            "wape_naive_weekly": round(wape(y, naive), 2),
            "wape_naive_daily": round(wape(y, naive_d1), 2),
            "mae_model_eur_mwh": round(float(np.mean(np.abs(y - p50))), 2),
            "rmse_model_eur_mwh": round(float(np.sqrt(np.mean((y - p50) ** 2))), 2),
            "pinball_p50": round(pinball(y, p50, 0.5), 3),
            "p10_p90_coverage_pct": round(float(np.mean((y >= p10) & (y <= p90)) * 100), 1),
            "test_hours": int(len(test_df)),
        }
        best_naive = min(metrics["wape_naive_weekly"], metrics["wape_naive_daily"])
        beats = metrics["wape_model"] < best_naive
        lift = round((best_naive - metrics["wape_model"]) / best_naive * 100, 1) if best_naive else 0.0

        self.model_card = {
            "model": "LightGBM quantile GBM for day-ahead price (p10/p50/p90)",
            "model_version": PRICE_MODEL_VERSION,
            "area_eic": self.area_eic,
            "trained_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "training_window": [str(train_df.index[0]), str(train_df.index[-1])],
            "backtest_window": [str(test_df.index[0]), str(test_df.index[-1])],
            "features": PRICE_FEATURES,
            "key_input": "NRG-Flux load forecast p50 (our own model)",
            "gas_feature_active": bool(gas is not None and len(gas) > 0),
            "backtest": metrics,
            "beats_naive_baseline": bool(beats),
            "skill_vs_best_naive_pct": lift,
            "known_weaknesses": [
                "Carbon (EUA) not yet modelled; gas active only if a TTF feed is configured",
                "Scarcity spikes and negative-price hours are systematically under-predicted",
                "Market-design or capacity changes break historical relationships",
                "Accuracy degrades most in the highest and lowest price deciles",
            ],
            "intended_use": "Range/uncertainty guidance and driver attribution. "
                            "NOT a trading signal and not a guarantee.",
            "update_frequency": "retrain weekly; re-issue after each load forecast",
        }
        os.makedirs(self.model_dir, exist_ok=True)
        for name, b in self.boosters.items():
            b.save_model(self._path(name))
        with open(self._card_path(), "w") as f:
            json.dump(self.model_card, f, indent=2)
        return PriceTrainResult(metrics, self.model_card)

    # ------------------------------------------------------------ inference

    def load_models(self) -> bool:
        try:
            for q in QUANTILES:
                self.boosters[q] = lgb.Booster(model_file=self._path(q))
            if os.path.exists(self._card_path()):
                with open(self._card_path()) as f:
                    self.model_card = json.load(f)
            return True
        except Exception:
            return False

    def _inference_frame(self, price_hist: pd.Series, load_fc: pd.Series,
                         horizon: int, gas: pd.Series | None = None) -> pd.DataFrame:
        idx = pd.date_range(price_hist.index[-1] + pd.Timedelta(hours=1),
                            periods=horizon, freq="h", tz="UTC")
        df = calendar_frame(idx)
        ld = load_fc.reindex(idx).interpolate(limit=12).ffill().bfill()
        df["load_p50"] = ld.values
        df["load_ramp_3h"] = ld.diff(3).fillna(0).values
        df["load_vs_week_mean"] = (ld - float(ld.mean())).values

        def latest(ts, step):
            t = ts - pd.Timedelta(hours=step)
            first = price_hist.index[0]
            while t not in price_hist.index and t > first:
                t -= pd.Timedelta(hours=step)
            return float(price_hist.get(t, price_hist.iloc[-step:].mean()))

        df["price_lag_24h"] = [latest(ts, 24) for ts in idx]
        df["price_lag_168h"] = [latest(ts, 168) for ts in idx]
        df["price_roll_24h_mean"] = float(price_hist.tail(24).mean())
        df["price_roll_168h_mean"] = float(price_hist.tail(168).mean())
        df["price_roll_24h_std"] = float(price_hist.tail(24).std() or 0.0)
        # forward gas: hold the last known settlement flat across the horizon
        # same construction as training: spark spread off the known price lag
        known = pd.Series(df["price_lag_24h"].values, index=idx)
        _attach_gas(df, idx, gas, known)
        return df[PRICE_FEATURES].fillna(0.0)

    def predict(self, price_hist: pd.Series, load_fc: pd.Series,
                horizon: int = 48, gas: pd.Series | None = None) -> pd.DataFrame:
        X = self._inference_frame(price_hist, load_fc, horizon, gas)
        out = pd.DataFrame(index=X.index)
        for q in QUANTILES:
            out[q] = self.boosters[q].predict(X[PRICE_FEATURES])
        out["p10"] = np.minimum(out["p10"], out["p50"])
        out["p90"] = np.maximum(out["p90"], out["p50"])
        return out

    def explain(self, price_hist: pd.Series, load_fc: pd.Series,
                horizon: int = 48, top_k: int = 6,
                gas: pd.Series | None = None) -> list[dict]:
        X = self._inference_frame(price_hist, load_fc, horizon, gas)
        contrib = self.boosters["p50"].predict(X[PRICE_FEATURES], pred_contrib=True)
        rows = []
        for i, ts in enumerate(X.index):
            pairs = sorted(zip(PRICE_FEATURES, contrib[i, :-1], X.iloc[i][PRICE_FEATURES]),
                           key=lambda p: -abs(p[1]))[:top_k]
            rows.append({
                "ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "base_eur_mwh": round(float(contrib[i, -1]), 2),
                "drivers": [{
                    "feature": f, "label": FRIENDLY_PRICE.get(f, f),
                    "feature_value": round(float(np.nan_to_num(v)), 2),
                    "impact_eur_mwh": round(float(c), 2),
                    "direction": "raises price" if c > 0.5 else
                                 "lowers price" if c < -0.5 else "no effect",
                } for f, c, v in pairs],
            })
        return rows
