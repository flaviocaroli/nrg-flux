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
    # --- volatility block: lets the quantiles widen when the market is jumpy ---
    "price_roll_168h_std", "price_hour_std_28d", "price_range_24h",
    # --- fuel block: the marginal generator in EU power is usually gas ---
    "gas_ttf", "gas_ttf_chg_7d", "spark_spread",
]

# Nominal coverage of the p10-p90 band. Conformal calibration enforces it.
TARGET_COVERAGE = 0.80

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
    "price_roll_168h_std": "weekly price volatility",
    "price_hour_std_28d": "volatility of this hour (28d)",
    "price_range_24h": "yesterday's price range",
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
    df["price_roll_168h_std"] = price.shift(1).rolling(168, min_periods=6).std().fillna(0).values
    # how volatile has THIS hour-of-day been over the last 4 weeks? Peak hours
    # are far jumpier than night hours; a single global band ignores that.
    hour_std = (price.shift(24).groupby(price.index.hour)
                .transform(lambda x: x.rolling(28, min_periods=3).std()))
    df["price_hour_std_28d"] = hour_std.fillna(0).values
    roll_max = price.shift(1).rolling(24, min_periods=2).max()
    roll_min = price.shift(1).rolling(24, min_periods=2).min()
    df["price_range_24h"] = (roll_max - roll_min).fillna(0).values
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
        self.conformal_q: float = 0.0       # additive widening (reporting)
        self.conformal_scale: float = 0.0   # legacy symmetric factor (back-compat)
        self.scale_lo: float = 0.0          # asymmetric CQR: lower-edge factor
        self.scale_hi: float = 0.0          # asymmetric CQR: upper-edge factor
        # v7 conditional calibration: per-volatility-regime factors
        self.cond_threshold: float | None = None
        self.cond: dict = {}                # {calm_lo, calm_hi, vol_lo, vol_hi}

    def _path(self, q: str) -> str:
        return os.path.join(self.model_dir, f"lgbm_price_{self.area_eic}_{q}.txt")

    def _card_path(self) -> str:
        return os.path.join(self.model_dir, f"model_card_price_{self.area_eic}.json")

    # ------------------------------------------------------------- training

    def train(self, price: pd.Series, load: pd.Series,
              gas: pd.Series | None = None, n_rounds: int = 350,
              load_source: str = "unknown") -> PriceTrainResult:
        df = _build_frame(price, load, gas)
        if len(df) < 24 * 30:
            raise RuntimeError("need at least ~30 days of overlapping price+load history")
        # ---- three-way split: fit / calibrate / test ------------------------
        # Conformalized Quantile Regression (Romano et al. 2019). Raw GBM
        # quantiles are systematically too narrow on fat-tailed price data, so
        # we hold out a CALIBRATION slice, measure how far reality falls
        # outside the band, and widen by exactly that much. This buys a
        # finite-sample marginal coverage guarantee instead of a hope.
        cutoff = df.index[-1] - pd.Timedelta(weeks=4)
        trainval, test_df = df[df.index <= cutoff], df[df.index > cutoff]
        if test_df.empty:
            trainval, test_df = df.iloc[:-168], df.iloc[-168:]
        # Calibration slice = the most recent 20% of the remaining history,
        # capped at 2 weeks and floored at 72h, but never more than 40% —
        # with short histories a fixed 2-week window can leave nothing to fit.
        n_tv = len(trainval)
        # v6 ROLLING slice: most recent NRGFLUX_CAL_DAYS (default 45). Price
        # regimes shift with fuels and seasons; factors calibrated on the
        # recent window transfer, a 100-day mixed-regime slice does not. The
        # daily scheduler retrain turns this into rolling recalibration.
        from .calibration import rolling_cal_hours
        cal_n = rolling_cal_hours(n_tv)
        if n_tv - cal_n < 24 * 7:
            raise RuntimeError(
                f"not enough price history to fit AND calibrate "
                f"({n_tv}h available after the test split). Backfill more days.")
        train_df, cal_df = trainval.iloc[:-cal_n], trainval.iloc[-cal_n:]

        for name, alpha in QUANTILES.items():
            params = {**LGB_PARAMS, "alpha": alpha}
            dtrain = lgb.Dataset(train_df[PRICE_FEATURES], label=train_df["y"])
            self.boosters[name] = lgb.train(params, dtrain, num_boost_round=n_rounds)

        # ---- conformal calibration -----------------------------------------
        y_cal = cal_df["y"].to_numpy()
        lo_cal = self.boosters["p10"].predict(cal_df[PRICE_FEATURES])
        hi_cal = self.boosters["p90"].predict(cal_df[PRICE_FEATURES])
        # conformity score: how far outside the band did reality land?
        # (negative when inside, so the quantile below also tightens a band
        #  that happens to be too wide)
        # normalized CQR — multiplicative, so the band scales with how
        # uncertain the model already is (peak hours vs 3am, calm vs spiky)
        # ASYMMETRIC (v6): spikes break the band upward far more than
        # negative-price hours break it downward — one symmetric factor was
        # averaging the two and under-covering both tails.
        from .calibration import asymmetric_cqr, conditional_asymmetric_cqr
        scales = asymmetric_cqr(y_cal, lo_cal, hi_cal, TARGET_COVERAGE)
        self.scale_lo, self.scale_hi = scales.lo, scales.hi
        self.conformal_scale = scales.symmetric_equivalent  # legacy field
        # v7 CONDITIONAL: calm vs volatile regimes split on the per-hour 28d
        # volatility feature — the global factor under-covers spiky hours and
        # over-covers calm ones; per-regime factors fix both directions.
        cond_cal = cal_df["price_hour_std_28d"].to_numpy()
        # SELECTION GUARD: conditional helps where regimes are stable (DE/IT)
        # and hurts where they are not (FR). Decide per market on a held-out
        # tail of the calibration slice — never on the test window. Fit both
        # schemes on the first 80%, score coverage error on the last 20%,
        # keep the winner, then refit it on the full slice.
        from .calibration import apply_conditional, apply_scales as _aps
        k = max(int(len(y_cal) * 0.8), 1)
        g_fit = asymmetric_cqr(y_cal[:k], lo_cal[:k], hi_cal[:k], TARGET_COVERAGE)
        c_fit = conditional_asymmetric_cqr(y_cal[:k], lo_cal[:k], hi_cal[:k],
                                           cond_cal[:k], TARGET_COVERAGE)
        yv, lov, hiv, cv = y_cal[k:], lo_cal[k:], hi_cal[k:], cond_cal[k:]
        g10, g90 = _aps(lov, hiv, g_fit.lo, g_fit.hi)
        c10, c90 = apply_conditional(lov, hiv, cv, c_fit["threshold"],
                                     c_fit["calm"].lo, c_fit["calm"].hi,
                                     c_fit["volatile"].lo, c_fit["volatile"].hi)
        g_err = abs(float(np.mean((yv >= g10) & (yv <= g90))) - TARGET_COVERAGE)
        c_err = abs(float(np.mean((yv >= c10) & (yv <= c90))) - TARGET_COVERAGE)
        # conditional must beat global by a clear margin (2pp of coverage
        # error) on the validation tail — complexity needs evidence.
        self.calibration_scheme = "conditional" if c_err < g_err - 0.02 else "global"
        if self.calibration_scheme == "conditional":
            cres = conditional_asymmetric_cqr(y_cal, lo_cal, hi_cal, cond_cal,
                                              TARGET_COVERAGE)
            self.cond_threshold = cres["threshold"]
            self.cond = {"calm_lo": cres["calm"].lo, "calm_hi": cres["calm"].hi,
                         "vol_lo": cres["volatile"].lo,
                         "vol_hi": cres["volatile"].hi}
        else:
            self.cond_threshold, self.cond = None, {}
        raw_scores = np.maximum(lo_cal - y_cal, y_cal - hi_cal)
        n_cal = scales.n
        level = min(np.ceil((n_cal + 1) * TARGET_COVERAGE) / n_cal, 1.0)
        self.conformal_q = float(np.quantile(raw_scores, level, method="higher"))
        raw_cal_cov = scales.raw_coverage_pct

        y = test_df["y"].to_numpy()
        p50 = self.boosters["p50"].predict(test_df[PRICE_FEATURES])
        p10_raw = self.boosters["p10"].predict(test_df[PRICE_FEATURES])
        p90_raw = self.boosters["p90"].predict(test_df[PRICE_FEATURES])
        # apply the correction learned on the calibration slice
        if self.cond_threshold is not None:
            p10, p90 = apply_conditional(
                p10_raw, p90_raw, test_df["price_hour_std_28d"].to_numpy(),
                self.cond_threshold, self.cond["calm_lo"], self.cond["calm_hi"],
                self.cond["vol_lo"], self.cond["vol_hi"])
        else:
            p10, p90 = _aps(p10_raw, p90_raw, self.scale_lo, self.scale_hi)
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
            "p10_p90_coverage_uncalibrated_pct":
                round(float(np.mean((y >= p10_raw) & (y <= p90_raw)) * 100), 1),
            "coverage_target_pct": round(TARGET_COVERAGE * 100, 1),
            "conformal_widening_eur_mwh": round(self.conformal_q, 2),
            "conformal_scale": round(self.conformal_scale, 3),
            "mean_band_width_eur_mwh": round(float(np.mean(p90 - p10)), 2),
            "calibration_hours": int(n_cal),
            "calibration_raw_coverage_pct": round(raw_cal_cov, 1),
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
            "key_input": ("TSO day-ahead load forecast (ENTSO-E)"
                          if load_source == "tso_forecast"
                          else "actual load — WARNING: train/serve skew"),
            "load_feature_source": load_source,
            "train_serve_skew": load_source != "tso_forecast",
            "gas_feature_active": bool(gas is not None and len(gas) > 0),
            "backtest": metrics,
            "beats_naive_baseline": bool(beats),
            "skill_vs_best_naive_pct": lift,
            "calibration": {
                "method": "Conformalized Quantile Regression (split conformal)",
                "target_coverage_pct": round(TARGET_COVERAGE * 100, 1),
                "conformal_q_eur_mwh": round(self.conformal_q, 3),
                "conformal_scale": round(self.conformal_scale, 4),
                "conformal_scale_lo": round(self.scale_lo, 4),
                "conformal_scale_hi": round(self.scale_hi, 4),
                "scheme": getattr(self, "calibration_scheme", "global"),
                **({"conditional": {
                    "feature": "price_hour_std_28d",
                    "threshold": round(self.cond_threshold, 3),
                    "calm_lo": round(self.cond["calm_lo"], 4),
                    "calm_hi": round(self.cond["calm_hi"], 4),
                    "vol_lo": round(self.cond["vol_lo"], 4),
                    "vol_hi": round(self.cond["vol_hi"], 4),
                }} if self.cond_threshold is not None else {}),
                "mode": "normalized asymmetric CQR, rolling weighted window, "
                        "per-market scheme selection (v7)",
                "calibration_window": [str(cal_df.index[0]), str(cal_df.index[-1])],
                "note": "p10/p90 are widened by conformal_q, learned on a held-out "
                        "calibration slice. Raw GBM quantiles are too narrow on "
                        "fat-tailed price data; this restores nominal coverage.",
            },
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
                cal = self.model_card.get("calibration", {})
                self.conformal_q = float(cal.get("conformal_q_eur_mwh", 0.0))
                self.conformal_scale = float(cal.get("conformal_scale", 0.0))
                # v6 asymmetric factors; older cards fall back to symmetric
                self.scale_lo = float(cal.get("conformal_scale_lo",
                                              self.conformal_scale))
                self.scale_hi = float(cal.get("conformal_scale_hi",
                                              self.conformal_scale))
                c = cal.get("conditional")
                if c:
                    self.cond_threshold = float(c["threshold"])
                    self.cond = {k: float(c[k]) for k in
                                 ("calm_lo", "calm_hi", "vol_lo", "vol_hi")}
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
        # volatility block — computed from history that is known at issue time
        df["price_roll_168h_std"] = float(price_hist.tail(168).std() or 0.0)
        recent = price_hist.tail(24 * 28)
        by_hour = recent.groupby(recent.index.hour).std()
        df["price_hour_std_28d"] = [float(by_hour.get(ts.hour) or 0.0) for ts in idx]
        df["price_range_24h"] = float(price_hist.tail(24).max() - price_hist.tail(24).min())
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
        # normalized conformal widening, validated in the backtest
        width = np.maximum(out["p90"] - out["p10"], 1e-6)
        if self.cond_threshold is not None and self.cond:
            from .calibration import apply_conditional
            out["p10"], out["p90"] = apply_conditional(
                out["p10"].to_numpy(), out["p90"].to_numpy(),
                df["price_hour_std_28d"].to_numpy(), self.cond_threshold,
                self.cond["calm_lo"], self.cond["calm_hi"],
                self.cond["vol_lo"], self.cond["vol_hi"])
        else:
            out["p10"] = out["p10"] - self.scale_lo * width
            out["p90"] = out["p90"] + self.scale_hi * width
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
