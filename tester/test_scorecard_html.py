from __future__ import annotations
import re
import pandas as pd
from research_platform.scorecard_html import render_scorecard_html, _diverging


def _rgb(s):
    return [int(x) for x in re.match(r"rgb\((\d+),(\d+),(\d+)\)", s).groups()]


def test_diverging_scale_distinguishes_sign():
    r_pos, g_pos, b_pos = _rgb(_diverging(1.0))   # positive -> blue-dominant
    r_neg, g_neg, b_neg = _rgb(_diverging(-1.0))  # negative -> red/warm-dominant
    assert b_pos > r_pos, "positive correlation should be blue-dominant"
    assert r_neg > b_neg, "negative correlation should be red-dominant"
    assert _diverging(0.0) == "rgb(255,255,255)"  # zero -> white

def _inputs():
    factor_tbl = pd.DataFrame({
        "factor": ["cand", "reversal_5d", "momentum_126d"],
        "rank_ic": [0.031, 0.026, 0.012],
        "icir_annualized": [0.91, 0.74, 0.38],
        "portfolio_sharpe": [-0.4, -0.6, 0.16],
    })
    corr = pd.DataFrame(
        [[1.0, 0.61, -0.12], [0.61, 1.0, -0.08], [-0.12, -0.08, 1.0]],
        index=["cand","reversal_5d","momentum_126d"],
        columns=["cand","reversal_5d","momentum_126d"],
    )
    ic_by_h = pd.DataFrame(
        {"cand":[0.03,0.031,0.02,0.01,0.005], "reversal_5d":[0.02,0.026,0.02,0.01,0.0]},
        index=[1,5,10,21,42],
    )
    return factor_tbl, corr, ic_by_h

def test_html_is_self_contained_and_contains_names():
    factor_tbl, corr, ic_by_h = _inputs()
    html = render_scorecard_html("summary line", factor_tbl, corr, ic_by_h)
    assert "<svg" in html and "cand" in html and "momentum_126d" in html
    assert "summary line" in html
    # self-contained: no external resources, no scripts
    assert "http://" not in html and "https://" not in html
    assert "<script" not in html
    assert re.search(r"无判决|no verdict", html)

def test_html_is_deterministic():
    args = _inputs()
    assert render_scorecard_html("s", *args) == render_scorecard_html("s", *args)


def test_html_includes_rebalance_tradeoff_when_provided():
    factor_tbl, corr, ic_by_h = _inputs()
    rb = pd.DataFrame({"rebalance_days": [5, 10, 21], "avg_turnover": [0.5, 0.3, 0.1],
                       "gross_sharpe": [0.2, 0.0, 0.05], "net_sharpe": [-0.6, -0.4, -0.2]})
    html = render_scorecard_html("s", factor_tbl, corr, ic_by_h, rebalance_tradeoff=rb)
    assert "调仓频率权衡" in html and "rb=" in html
    assert "http://" not in html and "https://" not in html and "<script" not in html
    # backward compatible: omitting the tradeoff still renders (no extra section)
    assert "调仓频率权衡" not in render_scorecard_html("s", factor_tbl, corr, ic_by_h)
