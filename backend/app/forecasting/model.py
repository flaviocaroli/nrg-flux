"""Explainable load forecaster (section 9).

Three LightGBM gradient-boosting models predict p10 / p50 / p90 load.
Explanations use LightGBM's native TreeSHAP (predict(pred_contrib=True)),
so every forecast point ships with per-feature MW contributions.

The trainer also runs a rolling-origin backtest against the naive
previous-week-same-hour baseline and writes a model card — the acceptance
criteria in the plan ("if ML cannot beat the baseline, do not sell forecast
claims") are checked here, not asserted by marketing.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd

from .features import FEATURES, FRIENDLY, build_inference_frame, build_training_frame

MODEL_VERSION = "0.2.0"
TARGET_COVERAGE = 0.80
QUANTILES = {"p10": 0.10, "p50": 0.50, "p90": 0.90}

LGB_PARAMS = dict(
    objective="quantile", metric="quantile", learning_rate=0.06,
    num_leaves=63, min_data_in_leaf=40, feature_fraction=0.9,
    bagging_fraction=0.9, bagging_freq=1, verbosity=-1, seed=42,
)


def wape(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.abs(actual - pred).sum() / np.abs(actual).sum() * 100)


def mape(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.mean(np.abs((actual - pred) / actual)) * 100)


@dataclass
class TrainResult:
    model_dir: str
    metrics: dict
    model_card: dict


class LoadForecaster:
    def __init__(self, model_dir: str, area_eic: str):
        self.model_dir = model_dir
        self.area_eic = area_eic
        self.boosters: dict[str, lgb.Booster] = {}
        self.model_card: dict = {}
        self.conformal_q: float = 0.0       # additive CQR widening (MW), reporting
        self.conformal_scale: float = 0.0   # legacy symmetric factor (back-compat)
        self.scale_lo: float = 0.0          # asymmetric CQR: lower-edge factor
        self.scale_hi: float = 0.0          # asymmetric CQR: upper-edge factor
        self.calibration_mode: str = "normalized"

    # ------------------------------------------------------------- training

    def train(self, load: pd.Series, temp: pd.Series, n_rounds: int = 400,
              source: str = "unknown") -> TrainResult:
        df = build_training_frame(load, temp)
        # Hold out the last 8 weeks — but never more than 25% of the history,
        # otherwise a short backfill leaves nothing to train on.
        holdout = min(int(len(df) * 0.25), 24 * 7 * 8)
        if len(df) - holdout < 24 * 21:
            raise RuntimeError(
                f"not enough history to train and backtest ({len(df)}h). "
                f"Backfill more days: scripts/backfill_entsoe.py --days 120")
        trainval, test_df = df.iloc[:-holdout], df.iloc[-holdout:]

        # Conformalized Quantile Regression: hold out a calibration slice so the
        # p10-p90 band can be corrected to its nominal coverage instead of
        # being systematically too narrow (raw GBM quantiles under-cover).
        n_tv = len(trainval)
        # v6 ROLLING slice: the most recent NRGFLUX_CAL_DAYS (default 45) of
        # the fit history. Conformal factors are only guaranteed to transfer
        # when the calibration slice looks like tomorrow — a recent window
        # does, a 100-day archaeology of old regimes does not. The daily
        # retrain makes this a *rolling* recalibration for free.
        from .calibration import rolling_cal_hours
        cal_n = rolling_cal_hours(n_tv)
        train_df, cal_df = trainval.iloc[:-cal_n], trainval.iloc[-cal_n:]

        for name, alpha in QUANTILES.items():
            params = {**LGB_PARAMS, "alpha": alpha}
            dtrain = lgb.Dataset(train_df[FEATURES], label=train_df["y"])
            self.boosters[name] = lgb.train(params, dtrain, num_boost_round=n_rounds)

        y_cal = cal_df["y"].to_numpy()
        lo_cal = self.boosters["p10"].predict(cal_df[FEATURES])
        hi_cal = self.boosters["p90"].predict(cal_df[FEATURES])

        # NORMALIZED + ASYMMETRIC CQR (v6): each band edge gets its own
        # multiplicative factor, calibrated on its own tail with half the miss
        # budget. Load errors are mildly asymmetric (under-forecast on heat
        # ramps); prices far more so — the shared module serves both.
        from .calibration import apply_scales, asymmetric_cqr
        scales = asymmetric_cqr(y_cal, lo_cal, hi_cal, TARGET_COVERAGE)
        self.scale_lo, self.scale_hi = scales.lo, scales.hi
        self.conformal_scale = scales.symmetric_equivalent  # legacy field
        raw_scores = np.maximum(lo_cal - y_cal, y_cal - hi_cal)
        level = min(np.ceil((len(raw_scores) + 1) * TARGET_COVERAGE) / len(raw_scores), 1.0)
        self.conformal_q = max(0.0, float(np.quantile(raw_scores, level, method="higher")))

        # Normalized CQR is useful when the raw quantile width is stable, but
        # a nearly collapsed raw band can create enormous multiplicative
        # factors (observed on the French regional model). Select a stable
        # additive MW correction before looking at the test set whenever the
        # normalized calibration would be implausibly wide.
        p10_norm, p90_norm = apply_scales(lo_cal, hi_cal, self.scale_lo, self.scale_hi)
        typical_load = max(float(np.median(np.abs(y_cal))), 1.0)
        normalized_width = float(np.median(p90_norm - p10_norm))
        factors_finite = np.isfinite([self.scale_lo, self.scale_hi]).all()
        if (not factors_finite or max(abs(self.scale_lo), abs(self.scale_hi)) > 5.0
                or normalized_width > typical_load * 0.75):
            self.calibration_mode = "additive"
        else:
            self.calibration_mode = "normalized"

        # --- backtest vs naive previous-week baseline ---
        y = test_df["y"].to_numpy()
        p50 = self.boosters["p50"].predict(test_df[FEATURES])
        p10_raw = self.boosters["p10"].predict(test_df[FEATURES])
        p90_raw = self.boosters["p90"].predict(test_df[FEATURES])
        if self.calibration_mode == "additive":
            p10, p90 = p10_raw - self.conformal_q, p90_raw + self.conformal_q
        else:
            p10, p90 = apply_scales(p10_raw, p90_raw, self.scale_lo, self.scale_hi)
        naive = test_df["load_lag_168h"].to_numpy()
        coverage = float(np.mean((y >= p10) & (y <= p90)) * 100)
        coverage_raw = float(np.mean((y >= p10_raw) & (y <= p90_raw)) * 100)

        metrics = {
            "wape_model": round(wape(y, p50), 2),
            "wape_naive_weekly": round(wape(y, naive), 2),
            "mape_model": round(mape(y, p50), 2),
            "rmse_model": round(float(np.sqrt(np.mean((y - p50) ** 2))), 1),
            "p10_p90_coverage_pct": round(coverage, 1),
            "p10_p90_coverage_uncalibrated_pct": round(coverage_raw, 1),
            "coverage_target_pct": round(TARGET_COVERAGE * 100, 1),
            "conformal_widening_mw": round(self.conformal_q, 1),
            "conformal_scale": round(self.conformal_scale, 3),
            "mean_band_width_mw": round(float(np.mean(p90 - p10)), 1),
            "test_hours": int(len(test_df)),
        }
        beats_baseline = metrics["wape_model"] < metrics["wape_naive_weekly"]

        self.model_card = {
            "model": "LightGBM quantile GBM (p10/p50/p90)",
            "model_version": MODEL_VERSION,
            "area_eic": self.area_eic,
            "trained_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "training_window": [str(train_df.index[0]), str(train_df.index[-1])],
            "backtest_window": [str(test_df.index[0]), str(test_df.index[-1])],
            "features": FEATURES,
            "training_source": source,
            "calibration": {
                "method": "Conformalized Quantile Regression (split conformal)",
                "target_coverage_pct": round(TARGET_COVERAGE * 100, 1),
                "conformal_q_mw": round(self.conformal_q, 2),
                "conformal_scale": round(self.conformal_scale, 4),
                "conformal_scale_lo": round(self.scale_lo, 4),
                "conformal_scale_hi": round(self.scale_hi, 4),
                "mode": self.calibration_mode,
                "calibration_hours": int(cal_n),
            },
            "load_range_mw": [round(float(load.min())), round(float(load.max()))],
            "backtest": metrics,
            "beats_naive_baseline": bool(beats_baseline),
            "known_weaknesses": [
                "Holiday bridges (ponti) partially captured by holiday flag only",
                "Extreme heat waves outside training range extrapolate poorly",
                "Industrial strike days and exceptional events are not modelled",
            ],
            "update_frequency": "retrain weekly; re-issue forecast on every new weather run",
            "disclaimer": "Probabilistic decision-support output, not a guaranteed outcome.",
        }

        os.makedirs(self.model_dir, exist_ok=True)
        for name, booster in self.boosters.items():
            booster.save_model(self._path(name))
        with open(os.path.join(self.model_dir, f"model_card_{self.area_eic}.json"), "w") as f:
            json.dump(self.model_card, f, indent=2)
        return TrainResult(self.model_dir, metrics, self.model_card)

    # ------------------------------------------------------------ inference

    def load_models(self) -> bool:
        try:
            for name in QUANTILES:
                self.boosters[name] = lgb.Booster(model_file=self._path(name))
            card = os.path.join(self.model_dir, f"model_card_{self.area_eic}.json")
            if os.path.exists(card):
                with open(card) as f:
                    self.model_card = json.load(f)
                cal = self.model_card.get("calibration", {})
                self.conformal_q = float(cal.get("conformal_q_mw", 0.0))
                self.conformal_scale = float(cal.get("conformal_scale", 0.0))
                # v6 asymmetric factors; older cards fall back to symmetric
                self.scale_lo = float(cal.get("conformal_scale_lo",
                                              self.conformal_scale))
                self.scale_hi = float(cal.get("conformal_scale_hi",
                                              self.conformal_scale))
                self.calibration_mode = str(cal.get("mode", "normalized"))
            return True
        except Exception:
            return False

    def predict(self, history: pd.Series, temp_forecast: pd.Series,
                horizon_hours: int, recursive: bool = True) -> pd.DataFrame:
        """Multi-step forecast.

        RECURSIVE (default): step hour by hour, appending each p50 prediction
        back onto the history so the lag/rolling features of the next step see
        it. Without this, every hour beyond h=24 receives an IDENTICAL feature
        vector (the lag walk-back lands on the same observation), so the model
        emits the same 24h shape on repeat — a square wave, not a forecast.

        recursive=False keeps the old one-shot behaviour, which is only valid
        for horizons <= 24h.
        """
        if not recursive or horizon_hours <= 24:
            X = build_inference_frame(history, temp_forecast, horizon_hours)
            out = pd.DataFrame(index=X.index)
            for name in QUANTILES:
                out[name] = self.boosters[name].predict(X[FEATURES])
            return self._finish(out)

        hist = history.copy()
        idx, p10s, p50s, p90s = [], [], [], []
        for _ in range(horizon_hours):
            X = build_inference_frame(hist, temp_forecast, 1)
            ts = X.index[0]
            row = X[FEATURES]
            p50 = float(self.boosters["p50"].predict(row)[0])
            p10s.append(float(self.boosters["p10"].predict(row)[0]))
            p90s.append(float(self.boosters["p90"].predict(row)[0]))
            p50s.append(p50)
            idx.append(ts)
            # feed the central estimate back in — this is what makes step h+1
            # aware of step h
            hist = pd.concat([hist, pd.Series([p50], index=[ts])])
        out = pd.DataFrame({"p10": p10s, "p50": p50s, "p90": p90s},
                           index=pd.DatetimeIndex(idx, tz="UTC"))
        return self._finish(out)

    def _finish(self, out: pd.DataFrame) -> pd.DataFrame:
        raw_p10, raw_p90 = out["p10"].copy(), out["p90"].copy()
        width = np.maximum(out["p90"] - out["p10"], 1e-6)
        if self.calibration_mode == "additive":
            out["p10"] = out["p10"] - self.conformal_q
            out["p90"] = out["p90"] + self.conformal_q
        else:
            out["p10"] = out["p10"] - self.scale_lo * width
            out["p90"] = out["p90"] + self.scale_hi * width

        # Backward-compatible guard for already-trained model cards: if an
        # old multiplicative calibration explodes at inference, use its saved
        # additive MW correction rather than publishing a chart-destroying
        # interval. The frontend independently withholds any remaining invalid
        # band and keeps the central forecast visible.
        typical = max(float(np.median(np.abs(out["p50"]))), 1.0)
        calibrated_width = np.asarray(out["p90"] - out["p10"], dtype=float)
        unstable = (not np.isfinite(calibrated_width).all()
                    or float(np.median(calibrated_width)) > typical * 0.75)
        if unstable:
            out["p10"] = raw_p10 - self.conformal_q
            out["p90"] = raw_p90 + self.conformal_q
        # enforce monotone quantiles
        out["p10"] = np.minimum(out["p10"], out["p50"])
        out["p90"] = np.maximum(out["p90"], out["p50"])
        return out

    def explain(self, history: pd.Series, temp_forecast: pd.Series,
                horizon_hours: int, top_k: int = 6) -> list[dict]:
        """Per-timestamp SHAP contributions in MW for the p50 model."""
        X = build_inference_frame(history, temp_forecast, horizon_hours)
        contrib = self.boosters["p50"].predict(X[FEATURES], pred_contrib=True)
        base_value = contrib[:, -1]
        rows = []
        for i, ts in enumerate(X.index):
            pairs = sorted(zip(FEATURES, contrib[i, :-1], X.iloc[i][FEATURES]),
                           key=lambda p: -abs(p[1]))[:top_k]
            rows.append({
                "ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "base_mw": round(float(base_value[i]), 1),
                "drivers": [{
                    "feature": f, "label": FRIENDLY.get(f, f),
                    "feature_value": round(float(np.nan_to_num(v)), 2),
                    "impact_mw": round(float(c), 1),
                    "direction": "increases demand" if c > 25 else
                                 "decreases demand" if c < -25 else "no effect",
                } for f, c, v in pairs],
            })
        return rows

    def _path(self, name: str) -> str:
        return os.path.join(self.model_dir, f"lgbm_{self.area_eic}_{name}.txt")
