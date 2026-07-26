"""Phase B diagnostic: does any factor in the current 25-factor pool have OOS
predictive power that survives multiple-testing correction?

Read-only. Reuses the identical inputs of the Alpha101 correlation experiment,
computes a 25-factor x 7-horizon daily IC panel, and runs a studentized
block-bootstrap family-wise test with horizon-matched blocks (each horizon its
own family, Bonferroni across horizons). Writes an evidence table + report.

    python scripts/diag_factor_evidence.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research_platform.evaluation import evaluate_ic
from research_platform.significance import (
    default_block_for_horizon,
    evaluate_factor_grid,
)
from scripts.run_alpha101_correlation_oos import _load_real_inputs

HORIZONS = (1, 2, 3, 5, 10, 21, 42)
N_BOOT = 2000
SEED = 42
P_THRESHOLD = 0.05
MIN_NAMES = 30


def build_ic_panel(
    factors: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    horizons=HORIZONS,
    min_names: int = MIN_NAMES,
) -> pd.DataFrame:
    """Daily Spearman IC for every (factor, horizon) cell, aligned on dates."""
    forwards = {h: close.shift(-h).div(close).sub(1.0) for h in horizons}
    columns: dict[tuple[str, int], pd.Series] = {}
    for name, panel in factors.items():
        for h in horizons:
            columns[(name, h)] = evaluate_ic(panel, forwards[h], min_names=min_names)
    frame = pd.DataFrame(columns)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["factor", "horizon"])
    return frame.sort_index()


def _best_horizon_table(grid: pd.DataFrame) -> pd.DataFrame:
    """One row per factor: its most significant horizon, sorted by p_global."""
    best = grid.sort_values(["p_global", "z_stat"], ascending=[True, False])
    best = best.drop_duplicates("factor")
    return best[
        ["factor", "horizon", "ic_bar", "z_stat", "crit_z_5pct", "t_naive",
         "p_horizon", "p_global", "verdict"]
    ]


def _heat_table(grid: pd.DataFrame) -> pd.DataFrame:
    return grid.pivot(index="factor", columns="horizon", values="ic_bar").reindex(
        columns=sorted(grid["horizon"].unique())
    )


def write_report(grid: pd.DataFrame, metadata: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    grid.to_csv(output_dir / "evidence.csv", index=False)

    blocks = {int(h): int(default_block_for_horizon(h)) for h in HORIZONS}
    crit = grid.groupby("horizon")["crit_z_5pct"].first().round(3).to_dict()
    summary = {
        "n_boot": N_BOOT,
        "seed": SEED,
        "p_threshold": P_THRESHOLD,
        "horizons": list(HORIZONS),
        "statistic": "studentized mean IC (mean / block-bootstrap SE)",
        "correction": "per-horizon family-wise max-T across factors, "
        "Bonferroni across horizons",
        "block_by_horizon": blocks,
        "family_wise_critical_z_5pct_by_horizon": {int(k): float(v) for k, v in crit.items()},
    }
    (output_dir / "null_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    n_pass = int((grid["verdict"] == "PASS").sum())
    n_horizon = int((grid["verdict_horizon"] == "PASS").sum())
    best = _best_horizon_table(grid)
    heat = _heat_table(grid)

    lines = [
        "# Factor Evidence Significance Report (Phase B)",
        "",
        "## Method",
        "",
        f"- Grid: {grid['factor'].nunique()} factors x {len(HORIZONS)} horizons "
        f"= {len(grid)} cells; horizons = {list(HORIZONS)} trading days.",
        "- Per-cell statistic: mean daily Spearman IC of the factor vs h-day "
        f"forward return (min_names={MIN_NAMES}), studentized by its "
        "block-bootstrap SE so cells of different sample size share one scale.",
        "- Null: circular block bootstrap, each column demeaned to impose H0 "
        f"(mean IC = 0), n_boot={N_BOOT}, seed={SEED}.",
        f"- Block is horizon-matched ({blocks}); a block shorter than the horizon "
        "underestimates SE for overlapping returns and inflates significance.",
        "- Correction: within each horizon, family-wise max-T across the 25 "
        "factors; then Bonferroni across the 7 horizons for the global verdict.",
        f"- Verdict: PASS if global p < {P_THRESHOLD}.",
        "",
        "## Conclusion",
        "",
        f"- **PASS (global, p < {P_THRESHOLD}): {n_pass} / {len(grid)} cells.**",
        f"- PASS at per-horizon level only (before the across-horizon Bonferroni): "
        f"{n_horizon} cells.",
    ]
    survivors = grid[grid["verdict"] == "PASS"]
    if n_pass == 0:
        lines.append(
            "- No factor at any horizon survives honest multiple-testing "
            "correction. The current OHLCV pool has no statistically usable "
            "cross-sectional alpha — motivating Phase C (new data) over further "
            "construction tuning on this pool."
        )
    else:
        names = ", ".join(
            f"{r.factor}@h{r.horizon} (p={r.p_global:.3f})"
            for r in survivors.itertuples()
        )
        lines.append(f"- Survivors: {names}.")
        if (survivors["horizon"] <= 2).all():
            lines.append(
                "- Every survivor is a horizon-1/2 daily-reversal signal — real "
                "but the highest-turnover, least-tradeable kind, precisely the "
                "cells whose edge is destroyed by cost in the portfolio "
                "experiment. No slower, tradeable-horizon factor clears the bar."
            )
    lines += [
        "",
        "## Best horizon per factor (sorted by global p)",
        "",
        best.round(4).to_markdown(index=False),
        "",
        "## IC_bar heat table (factor x horizon)",
        "",
        heat.round(4).to_markdown(),
        "",
        "## Caveats",
        "",
        "- **Survivorship bias:** universe is the *current* "
        f"{metadata.get('loaded_tickers', 'N/A')} S&P constituents over "
        f"{pd.Timestamp(metadata['date_start']).date()}.."
        f"{pd.Timestamp(metadata['date_end']).date()}. This inflates momentum and "
        "understates delisted-name reversal; absolute IC is optimistic.",
        "- IC is rank predictiveness, not tradeable P&L. A PASS is necessary, not "
        "sufficient; cost/turnover still apply downstream (Phase A).",
        "- n_eff = n_obs / horizon and t_naive are analytic cross-checks only; the "
        "verdict is the studentized block-bootstrap p-value.",
        "",
    ]
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run(output_dir: Path, project_root: Path = PROJECT_ROOT) -> pd.DataFrame:
    existing, alpha101, close, _industry, metadata = _load_real_inputs(
        project_root, allow_network=False
    )
    factors = {**existing, **alpha101}
    ic_panel = build_ic_panel(factors, close)
    grid = evaluate_factor_grid(
        ic_panel, n_boot=N_BOOT, seed=SEED, p_threshold=P_THRESHOLD
    )
    write_report(grid, metadata, output_dir)
    return grid


def main() -> int:
    grid = run(PROJECT_ROOT / "outputs" / "factor_evidence")
    n_pass = int((grid["verdict"] == "PASS").sum())
    print(f"PASS {n_pass} / {len(grid)} cells (global p<{P_THRESHOLD}). "
          "See outputs/factor_evidence/report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
