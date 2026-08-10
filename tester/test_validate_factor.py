from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from scripts.validate_factor import DEFAULT_BENCHMARKS, build_panels, new_factor_ic_grid, auto_summary

def _bundle(n=140, k=60):
    from research_platform.market_data import ResearchOHLCVBundle
    dates = pd.bdate_range("2021-01-01", periods=n)
    rng = np.random.default_rng(7)
    frames = {}
    for i in range(k):
        base = 100 + np.cumsum(rng.standard_normal(n))
        frames[f"T{i}"] = pd.DataFrame({"Open":base,"High":base+1,"Low":base-1,"Close":base,
                                        "Volume":rng.integers(1e5,1e6,n).astype(float)}, index=dates)
    return ResearchOHLCVBundle(frames=frames, metadata={})

def test_default_benchmarks_are_valid_formulas():
    from research_platform.formula_dsl import evaluate_formula
    panels = build_panels(_bundle())
    for name, formula in DEFAULT_BENCHMARKS.items():
        out = evaluate_formula(formula, panels)
        assert out.shape == panels["close"].shape

def test_build_panels_has_all_allowed_inputs():
    from research_platform.formula_dsl import ALLOWED_INPUTS
    panels = build_panels(_bundle())
    assert set(ALLOWED_INPUTS).issubset(panels)

def test_new_factor_ic_grid_family_is_new_factor_only():
    panels = build_panels(_bundle())
    from research_platform.formula_dsl import evaluate_formula
    from research_platform.preprocessing import standardize_panel
    factor = standardize_panel(evaluate_formula("-(close/delay(close,5)-1)", panels))
    ic_panel = new_factor_ic_grid(factor, panels["close"], horizons=(1,5,10), min_names=10)
    fams = {f for (f, h) in ic_panel.columns}
    assert fams == {"candidate"}  # benchmarks excluded from the family

def test_new_factor_ic_grid_labels_with_custom_name():
    # the grid's factor label must follow --name, else auto_summary's
    # grid[factor==name] filter comes up empty for a custom-named factor.
    panels = build_panels(_bundle())
    from research_platform.formula_dsl import evaluate_formula
    from research_platform.preprocessing import standardize_panel
    factor = standardize_panel(evaluate_formula("-(close/delay(close,5)-1)", panels))
    ic_panel = new_factor_ic_grid(factor, panels["close"], horizons=(1,5), min_names=10, name="rev5")
    assert {f for (f, h) in ic_panel.columns} == {"rev5"}

def test_auto_summary_picks_best_horizon():
    grid = pd.DataFrame({"factor":["candidate"]*3, "horizon":[1,5,10],
                         "ic_bar":[0.005,0.03,0.01], "z_stat":[0.5,3.1,1.0],
                         "p_global":[1.0,0.02,0.9], "verdict":["REJECT","REJECT","REJECT"]})
    s = auto_summary("candidate", grid, gross_sharpe=0.2, net_sharpe=-0.4, monotonicity=0.8, capacity_aum=4e7)
    assert "h=5" in s and "0.03" in s
    assert "扣成本前" in s and "0.20" in s and "-0.40" in s


def test_factor_portfolio_row_fits_direction_and_reports_gross_net():
    from scripts.validate_factor import _factor_portfolio_row
    dates = pd.bdate_range("2021-01-01", periods=80)
    cols = [f"T{i}" for i in range(120)]  # enough names for the 0.05 name cap to be feasible
    rng = np.random.default_rng(11)
    fwd = pd.DataFrame(rng.standard_normal((len(dates), len(cols))), index=dates, columns=cols)
    score = -fwd + rng.standard_normal((len(dates), len(cols))) * 0.1  # score predicts fwd negatively -> IC<0
    row, _ = _factor_portfolio_row("x", score, fwd, fwd, primary_horizon=5, min_names=10)
    for k in ["direction", "gross_sharpe", "net_sharpe", "cost_drag"]:
        assert k in row
    assert row["direction"] == -1              # negative IC -> traded flipped
    assert row["cost_drag"] >= -1e-9           # cost only ever reduces return


def test_build_rebalance_tradeoff_columns_and_turnover_monotone():
    from scripts.validate_factor import build_rebalance_tradeoff
    dates = pd.bdate_range("2021-01-01", periods=90)
    cols = [f"T{i}" for i in range(120)]
    rng = np.random.default_rng(5)
    ar = pd.DataFrame(rng.standard_normal((len(dates), len(cols))) * 0.01, index=dates, columns=cols)
    score = pd.DataFrame(rng.standard_normal((len(dates), len(cols))), index=dates, columns=cols)
    tbl = build_rebalance_tradeoff(score, direction=1, asset_returns=ar, rebalance_grid=(5, 21)).set_index("rebalance_days")
    for c in ["avg_turnover", "gross_sharpe", "net_sharpe"]:
        assert c in tbl.columns
    assert tbl.loc[21, "avg_turnover"] <= tbl.loc[5, "avg_turnover"] + 1e-9  # slower -> less turnover

def test_validate_factor_rejects_name_collision_with_benchmark():
    from scripts.validate_factor import validate_factor
    bundle = _bundle()
    with pytest.raises(ValueError):
        validate_factor("-(close/delay(close,5)-1)", name="reversal_5d", bundle=bundle)

import pathlib
HAS_REAL = (pathlib.Path("data/reports/composite_alpha_latest.csv").exists())

@pytest.mark.skipif(not HAS_REAL, reason="real data unavailable")
def test_validate_factor_end_to_end(tmp_path):
    from scripts.validate_factor import validate_factor
    res = validate_factor("-(close/delay(close,5)-1)", name="rev5", output_dir=tmp_path)
    assert (res.output_dir / "scorecard.md").exists()
    assert (res.output_dir / "scorecard.html").exists()
    assert (res.output_dir / "formula.txt").exists()
    assert (tmp_path / "ledger.jsonl").exists()
    # custom --name must produce a populated summary, not the empty-grid fallback
    assert "无有效 IC 网格" not in res.summary and "h=" in res.summary
    # gross/net cost story is surfaced in the summary and portfolio table
    assert "扣成本前" in res.summary and "扣成本后" in res.summary
    port = res.tables["portfolio"]
    for col in ["direction", "gross_sharpe", "net_sharpe", "cost_drag"]:
        assert col in port.columns
    assert "rebalance_tradeoff" in res.tables
    html = (res.output_dir / "scorecard.html").read_text()
    assert "http://" not in html and "https://" not in html  # self-contained
    assert "调仓频率权衡" in html  # rebalance tradeoff chart rendered
