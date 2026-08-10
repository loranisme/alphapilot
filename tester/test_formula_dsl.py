# tester/test_formula_dsl.py
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from research_platform.formula_dsl import FormulaError, evaluate_formula, ALLOWED_INPUTS, ALLOWED_OPERATORS

def _panels():
    dates = pd.bdate_range("2021-01-01", periods=40)
    cols = [f"T{i}" for i in range(10)]
    rng = np.random.default_rng(0)
    mk = lambda: pd.DataFrame(rng.random((len(dates), len(cols))) + 1.0, index=dates, columns=cols)
    return {k: mk() for k in ALLOWED_INPUTS}

def test_bool_constant_is_rejected():
    with pytest.raises(FormulaError):
        evaluate_formula("close + True", _panels())

def test_missing_input_fails_cleanly_as_formula_error():
    p = _panels(); p.pop("vwap")
    with pytest.raises(FormulaError):
        evaluate_formula("vwap + close", p)

def test_overlong_formula_rejected_without_recursionerror():
    with pytest.raises(FormulaError):
        evaluate_formula("close" + "+close" * 5000, _panels())

def test_evaluate_simple_reversal_returns_panel():
    p = _panels()
    out = evaluate_formula("-(close / delay(close,5) - 1)", p)
    assert isinstance(out, pd.DataFrame)
    assert out.shape == p["close"].shape
    # first 5 rows are NaN (delay warmup)
    assert out.iloc[:5].isna().all().all()

def test_operators_and_inputs_are_the_whitelist():
    assert "rank" in ALLOWED_OPERATORS and "correlation" in ALLOWED_OPERATORS
    assert set(ALLOWED_INPUTS) >= {"open","high","low","close","volume","returns","vwap","adv20"}

@pytest.mark.parametrize("bad", [
    "__import__('os').system('echo hi')",
    "close.rolling(5).mean()",     # attribute access
    "close[0]",                     # subscript
    "(lambda x: x)(close)",         # lambda
    "foo(close)",                   # non-whitelisted call
    "bar",                          # unknown name
    "close + 'x'",                  # non-numeric constant
])
def test_whitelist_rejects_unsafe_or_unknown(bad):
    with pytest.raises(FormulaError):
        evaluate_formula(bad, _panels())

def test_scalar_result_is_rejected():
    with pytest.raises(FormulaError):
        evaluate_formula("1 + 2", _panels())

def test_formula_reproduces_alpha101_012():
    from research_platform.market_data import ResearchOHLCVBundle
    from factor_section.alpha101 import _input_panels, build_alpha101_factors
    dates = pd.bdate_range("2021-01-01", periods=60)
    rng = np.random.default_rng(3)
    frames = {}
    for t in ["A","B","C","D","E"]:
        base = 100 + np.cumsum(rng.standard_normal(len(dates)))
        frames[t] = pd.DataFrame({
            "Open": base, "High": base+1, "Low": base-1, "Close": base,
            "Volume": rng.integers(1e5, 1e6, len(dates)).astype(float),
        }, index=dates)
    bundle = ResearchOHLCVBundle(frames=frames, metadata={})
    panels = _input_panels(bundle)
    panels["vwap"] = (panels["open"]+panels["high"]+panels["low"]+panels["close"])/4.0
    got = evaluate_formula("sign(delta(volume,1)) * (-delta(close,1))", panels)
    want = build_alpha101_factors(bundle)["alpha101_012"]
    pd.testing.assert_frame_equal(got.reindex_like(want), want, check_dtype=False)
