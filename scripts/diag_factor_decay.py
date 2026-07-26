"""Diagnostic: where does the alpha actually die?

Not part of the research pipeline. Read-only measurement of the same inputs the
Alpha101 correlation experiment uses.
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
from scripts.run_alpha101_correlation_oos import _load_real_inputs

existing, alpha101, close, industry, meta = _load_real_inputs(PROJECT_ROOT, False)
horizon = 5
forward = close.shift(-horizon).div(close).sub(1.0)
dates = close.index
oos_start = dates[305]
print(f"panel {dates[0].date()} .. {dates[-1].date()}  n_dates={len(dates)} "
      f"n_names={close.shape[1]}")
print(f"first OOS test date = {oos_start.date()}\n")

# 1. per-factor IC: full sample, IS-only region, OOS region, and per calendar year
rows = []
for name, panel in {**existing, **alpha101}.items():
    ic = evaluate_ic(panel, forward, min_names=30)
    is_ic = ic.loc[ic.index < oos_start]
    oos_ic = ic.loc[ic.index >= oos_start]
    row = {
        "factor": name,
        "ic_is": is_ic.mean(),
        "ic_oos": oos_ic.mean(),
        "ic_std": ic.std(),
        # t-stat with Newey-West-ish haircut for 5-day overlap: sqrt(n/horizon)
        "t_oos": oos_ic.mean() / oos_ic.std() * np.sqrt(len(oos_ic) / horizon),
    }
    for year, chunk in ic.groupby(ic.index.year):
        row[f"ic_{year}"] = chunk.mean()
    rows.append(row)
table = pd.DataFrame(rows).set_index("factor")
print("=== per-factor IC (5d forward, spearman) ===")
print(table.round(4).to_string())

# 2. how correlated are the 13 "existing" factors with each other?
print("\n=== cross-factor value correlation (mean over dates, existing pool) ===")
names = list(existing)
flat = pd.DataFrame({n: existing[n].stack(future_stack=True) for n in names})
corr = flat.corr(method="spearman")
print(corr.round(2).to_string())
eig = np.linalg.eigvalsh(corr.fillna(0.0).to_numpy())
eig = np.sort(eig)[::-1]
print(f"\ntop eigenvalue share = {eig[0] / eig.sum():.1%}  "
      f"(n factors = {len(names)}, effective breadth ~ "
      f"{eig.sum() ** 2 / (eig ** 2).sum():.1f})")

# 3. IC decay by horizon for the strongest reversal factor
print("\n=== IC by horizon: short_reversal_1d / reversal_5d / momentum_126d ===")
for name in ("short_reversal_1d", "reversal_5d", "momentum_126d"):
    out = {}
    for h in (1, 2, 3, 5, 10, 21):
        fwd = close.shift(-h).div(close).sub(1.0)
        ic = evaluate_ic(existing[name], fwd, min_names=30)
        out[f"h{h}"] = ic.loc[ic.index >= oos_start].mean()
    print(name, {k: round(v, 4) for k, v in out.items()})

# 4. cost breakeven: what gross IC would be needed?
print("\n=== turnover / cost arithmetic ===")
port = pd.read_csv(PROJECT_ROOT / "outputs/alpha101_correlation_oos/cost_stress.csv")
a_raw = port[(port.arm == "A") & (port.path == "raw")]
print(a_raw[["cost_bps", "annualized_return", "sharpe", "average_turnover"]].to_string(index=False))
turn = float(a_raw["average_turnover"].iloc[0])
print(f"mean DAILY turnover = {turn:.3f} -> annual cost @10bps = "
      f"{turn * 252 * 0.001:.2%}")
print(f"implied per-rebalance (5d) one-way turnover = {turn * 5:.2f} of a 2.0 gross book")
