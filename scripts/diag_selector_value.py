"""Diagnostic: does the walk-forward selector add anything over doing nothing?

Compares, on the identical OOS window / portfolio construction / cost model:
  1. the shipped selector composite (arm A raw score)
  2. naive equal-weight over all 13 factors, no selection, no direction fitting
  3. momentum_126d alone at its own natural (slower) horizon
"""

from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research_platform.evaluation import evaluate_ic
from research_platform.portfolio import build_buffered_targets, simulate_portfolio
from research_platform.preprocessing import standardize_panel
from scripts.run_alpha101_correlation_oos import _load_real_inputs

existing, alpha101, close, industry, meta = _load_real_inputs(PROJECT_ROOT, False)
dates = close.index
oos_dates = dates[305:]
asset_returns = close.pct_change(fill_method=None).reindex(index=oos_dates)


def run(score: pd.DataFrame, label: str, interval: int, bps=(0.0, 10.0)) -> None:
    score = score.reindex(index=oos_dates)
    buffered = build_buffered_targets(
        score, rebalance_interval=interval, entry_quantile=0.20,
        exit_quantile=0.30, max_weight=0.02,
    )
    out = []
    for cost in bps:
        m = simulate_portfolio(buffered.targets, asset_returns, cost_bps=cost).metrics
        out.append(f"{cost:>4.0f}bps ret={m['annualized_return']:+.2%} "
                   f"sharpe={m['sharpe']:+.2f} turn={m['average_turnover']:.3f}")
    fwd = close.shift(-interval).div(close).sub(1.0)
    ic = evaluate_ic(score, fwd, min_names=30)
    print(f"{label:<38} IC(h={interval})={ic.mean():+.4f} | " + " | ".join(out))


print(f"OOS window {oos_dates[0].date()} .. {oos_dates[-1].date()}\n")

# 1. shipped selector output (arm A raw path), replayed
shipped = pd.read_csv(
    PROJECT_ROOT / "outputs/alpha101_correlation_oos/fold_metrics.csv"
)
print("shipped arm A raw (from report): ret=-8.03% sharpe=-0.50 turn=0.378\n")

std = {n: standardize_panel(p.reindex(index=oos_dates)) for n, p in existing.items()}

# 2. naive equal weight over all 13, no selection at all
naive = sum(std.values()) / len(std)
run(naive, "naive equal-weight all 13", 5)

# 3. equal weight over the reversal block only
rev = [n for n in existing if n not in
       ("momentum_126d", "momentum_63d", "price_volume_corr", "amihud_illiq")]
run(sum(std[n] for n in rev) / len(rev), "reversal block only (9 factors)", 5)

# 4. the three genuinely independent non-reversal ideas
run((std["momentum_126d"] + std["price_volume_corr"] + std["min_ret_reversal"]) / 3,
    "momentum + pvcorr + minret (breadth 3)", 5)

# 5. momentum alone, at horizons matching its actual IC persistence
for interval in (5, 21, 42):
    run(std["momentum_126d"], f"momentum_126d alone", interval)

# 6. what gross return is needed to break even on cost?
print("\n--- cost breakeven ---")
for turn in (0.378, 0.20, 0.10, 0.05):
    print(f"daily turnover {turn:.3f} -> need gross annualized "
          f"{turn * 252 * 0.001:.2%} just to reach zero at 10bps")
