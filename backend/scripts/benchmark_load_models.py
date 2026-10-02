"""Generate the saved Italy 24-hour Forecast Arena evidence artifact."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.forecasting.evaluation import run_benchmark  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--spacing-days", type=int, default=7)
    parser.add_argument("--skip-sarimax", action="store_true")
    args = parser.parse_args()
    if not 2 <= args.folds <= 12:
        parser.error("--folds must be between 2 and 12")
    if not 1 <= args.spacing_days <= 30:
        parser.error("--spacing-days must be between 1 and 30")

    settings = get_settings()
    print("Forecast Arena: Italy national load, next 24 hours")
    print(f"  rolling origins : {args.folds}")
    print(f"  origin spacing  : {args.spacing_days} days")
    print("  TSO policy      : retrieved/final revision (PRELIMINARY)")
    print("  target weather  : excluded; lagged observations only")
    artifact, path = run_benchmark(
        settings.model_dir, folds=args.folds, spacing_days=args.spacing_days,
        include_statistical=not args.skip_sarimax,
    )
    print("\nMODEL COMPARISON")
    print(f"{'model':<38} {'status':<18} {'WAPE':>8} {'MAE MW':>10} {'skill':>9} {'n':>5}")
    for row in artifact["models"]:
        wape = "—" if row.get("wape") is None else f"{row['wape']:.3f}%"
        mae = "—" if row.get("mae_mw") is None else f"{row['mae_mw']:.1f}"
        skill = "—" if row.get("skill_vs_naive_pct") is None else f"{row['skill_vs_naive_pct']:.2f}%"
        print(f"{row['label']:<38} {row['status']:<18} {wape:>8} {mae:>10} {skill:>9} {row.get('sample_count', 0):>5}")
    gate = artifact["europe_acceptance"]
    print("\nEUROPEAN FEATURE GATE")
    print(f"  accepted        : {gate['accepted']}")
    print(f"  relative gain   : {gate['relative_wape_gain_pct']}")
    print(f"  folds won       : {gate['folds_won']}/{artifact['protocol']['rolling_origins']}")
    print(f"  peak gate       : {gate['peak_mae_gate_passed']}")
    evidence = artifact.get("tso_comparison")
    if evidence:
        low, high = evidence["day_block_bootstrap_95_ci_points"]
        print("\nTSO COMPARISON EVIDENCE")
        print(f"  paired WAPE gap : {evidence['observed_wape_difference_points']:+.4f} points")
        print(f"  day-block 95% CI: [{low:+.4f}, {high:+.4f}]")
        print(f"  P(NRG better)   : {evidence['bootstrap_probability_best_nrg_better_pct']:.1f}%")
        print(f"  conclusion      : {evidence['conclusion']}")
    if artifact["failures"]:
        print("\nFAILURES REQUIRING REVIEW")
        for failure in artifact["failures"]:
            print(f"  fold {failure['fold']} · {failure['model_id']}: {failure['reason']}")
    else:
        print("\nFAILURES REQUIRING REVIEW: none")
    print(f"\nARTIFACT={path.resolve()}")
    print("BENCHMARK_COMPLETE=PASS")


if __name__ == "__main__":
    main()
