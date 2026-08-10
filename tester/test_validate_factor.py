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

def test_auto_summary_picks_best_horizon():
    grid = pd.DataFrame({"factor":["candidate"]*3, "horizon":[1,5,10],
                         "ic_bar":[0.005,0.03,0.01], "z_stat":[0.5,3.1,1.0],
                         "p_global":[1.0,0.02,0.9], "verdict":["REJECT","REJECT","REJECT"]})
    s = auto_summary("candidate", grid, best_sharpe=-0.4, monotonicity=0.8, capacity_aum=4e7)
    assert "h=5" in s and "0.03" in s

import pathlib
HAS_REAL = (pathlib.Path("data/reports/composite_alpha_latest.csv").exists())

@pytest.mark.skipif(not HAS_REAL, reason="real data unavailable")
def test_validate_factor_end_to_end(tmp_path):
    from scripts.validate_factor import validate_factor
    res = validate_factor("-(close/delay(close,5)-1)", output_dir=tmp_path)
    assert (res.output_dir / "scorecard.md").exists()
    assert (res.output_dir / "scorecard.html").exists()
    assert (res.output_dir / "formula.txt").exists()
    assert (tmp_path / "ledger.jsonl").exists()
    html = (res.output_dir / "scorecard.html").read_text()
    assert "http://" not in html and "https://" not in html  # self-contained
