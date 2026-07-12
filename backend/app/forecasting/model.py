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

MODEL_VERSION = "0.1.0"
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

    # ------------------------------------------------------------- training

    def train(self, load: pd.Series, temp: pd.Series, n_rounds: int = 400) -> TrainResult:
        df = build_training_frame(load, temp)
        # hold out the last 8 weeks for the backtest
        cutoff = df.index[-1] - pd.Timedelta(weeks=8)
        train_df, test_df = df[df.index <= cutoff], df[df.index > cutoff]

        for name, alpha in QUANTILES.items():
            params = {**LGB_PARAMS, "alpha": alpha}
            dtrain = lgb.Dataset(train_df[FEATURES], label=train_df["y"])
            self.boosters[name] = lgb.train(params, dtrain, num_boost_round=n_rounds)

        # --- backtest vs naive previous-week baseline ---
        y = test_df["y"].to_numpy()
        p50 = self.boosters["p50"].predict(test_df[FEATURES])
        p10 = self.boosters["p10"].predict(test_df[FEATURES])
        p90 = self.boosters["p90"].predict(test_df[FEATURES])
        naive = test_df["load_lag_168h"].to_numpy()
        coverage = float(np.mean((y >= p10) & (y <= p90)) * 100)

        metrics = {
            "wape_model": round(wape(y, p50), 2),
            "wape_naive_weekly": round(wape(y, naive), 2),
            "mape_model": round(mape(y, p50), 2),
            "rmse_model": round(float(np.sqrt(np.mean((y - p50) ** 2))), 1),
            "p10_p90_coverage_pct": round(coverage, 1),
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
            return True
        except Exception:
            return False

    def predict(self, history: pd.Series, temp_forecast: pd.Series,
                horizon_hours: int) -> pd.DataFrame:
        X = build_inference_frame(history, temp_forecast, horizon_hours)
        out = pd.DataFrame(index=X.index)
        for name in QUANTILES:
            out[name] = self.boosters[name].predict(X[FEATURES])
        # enforce monotone quantiles
        out["p10"] = np.minimum(out["p10"], out["p50"])
        out["p90"] = np.maximum(out["p90"], out["p50"])
        out["_X"] = list(X[FEATURES].itertuples(index=False, name=None))
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
