# 单因子 DSL 自动验证器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Input one DSL formula string → the platform causally evaluates it into a factor panel, runs the full standalone validation (multi-horizon IC + significance, group/monotonicity, single-factor portfolio + cost + capacity, regime), compares against a small benchmark set, and emits a no-verdict `scorecard.md` + a self-contained `scorecard.html` chart page.

**Architecture:** Three new, cleanly-separated modules — `formula_dsl.py` (AST-whitelist parser/evaluator, causal by construction), `scorecard_html.py` (inline-SVG chart page), and `scripts/validate_factor.py` (orchestration) — plus a thin `validate-factor` CLI subcommand. Everything downstream reuses existing platform modules.

**Tech Stack:** Python 3.13, pandas, numpy, `ast`, pytest. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-08-09-single-factor-dsl-validator-design.md`

---

## Subagent assignment

- **D1** → Task 1 (`formula_dsl.py`) — independent.
- **D2** → Task 2 (`scorecard_html.py`) — independent of D1.
- **D3** → Task 3 (`scripts/validate_factor.py`) — depends on D1 + D2.
- **D4** → Task 4 (CLI + integration/real run) — depends on D3.

D1 and D2 touch disjoint files and may run in either order; D3 after both; D4 last. Main agent runs the two-stage review after each task and an overall structure/flow review at the end.

## Verified existing APIs (do not re-derive)

- `factor_section.alpha101`: module-level operators `rank, delay, delta, ts_sum, ts_min, ts_max, stddev, ts_rank, correlation, covariance, sign, signed_power, safe_divide, where, adv`; helper `_finite(frame)`; `_input_panels(bundle) -> dict` (keys: open/high/low/close/volume[=OHLC4 dollar]/reported_volume/adv20/returns); `build_alpha101_factors(bundle) -> dict`.
- `research_platform.market_data.load_research_ohlcv(raw_dir, tickers, start, end) -> ResearchOHLCVBundle(frames, metadata)`.
- `scripts.run_research_platform_validation.load_pit_context(project_root, dates, columns, allow_network) -> (industry, member_mask, pit_meta)`; `_load_classification_snapshot`, `_load_factor_report`.
- `research_platform.preprocessing.standardize_panel(panel)`, `neutralize_panel(panel, industry, min_names)`.
- `research_platform.evaluation.evaluate_ic(factor, forward, method="spearman", min_names=30)`.
- `research_platform.significance.evaluate_factor_grid(ic_panel, n_boot=2000, seed=42, p_threshold=0.05) -> DataFrame[factor,horizon,ic_bar,z_stat,crit_z_5pct,t_naive,p_horizon,p_global,verdict]` — `ic_panel` columns are a `MultiIndex[(factor,horizon)]` of daily IC (see `scripts/diag_factor_evidence.build_ic_panel`).
- `research_platform.scorecard.build_factor_scorecard, build_group_backtest, build_correlation_views, render_scorecard_markdown, write_scorecard, sortino_ratio, calmar_ratio, win_rate`.
- `research_platform.portfolio.build_buffered_targets(score, rebalance_interval, entry_quantile, exit_quantile, max_weight), simulate_portfolio(targets, asset_returns, cost_bps), capacity_curve(target_weights, asset_returns, adv_dollar, aum_grid)`.
- `research_platform.regime.calendar_year_labels, group_daily_ic, ic_stability_summary`.
- `research_platform.registry.ExperimentRecord(name, family_wise_p, n_hypotheses, passed_local, timestamp="", family="default", metadata={}), append_record(ledger_path, record)`.
- `research_platform.reporting._atomic_text(path, content)`.

---

## Task 1 (D1): `formula_dsl.py` — AST-whitelist causal formula evaluator

**Files:** Create `research_platform/formula_dsl.py`, `tester/test_formula_dsl.py`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tester/test_formula_dsl.py -q` → ImportError.

- [ ] **Step 3: Implement**

```python
# research_platform/formula_dsl.py
"""Safe formula DSL: parse a restricted Python expression into a causal factor panel.

A factor formula is a single Python expression over OHLCV-derived panels and the
platform's causal Alpha101 operators. Parsing goes through an AST whitelist
(deny-by-default), so the only expressible computations are causal by
construction — no look-ahead is possible and no arbitrary code executes.
"""
from __future__ import annotations
import ast
import numpy as np
import pandas as pd
from factor_section.alpha101 import (
    _finite, adv, correlation, covariance, delay, delta, rank, safe_divide,
    sign, signed_power, stddev, ts_max, ts_min, ts_rank, ts_sum, where,
)

class FormulaError(ValueError):
    """Raised when a formula uses a construct or name outside the whitelist."""

def _log(frame): return _finite(np.log(frame.where(frame > 0)))
def _abs(frame): return _finite(frame.abs())

ALLOWED_OPERATORS = {
    "rank": rank, "delay": delay, "delta": delta, "ts_sum": ts_sum,
    "ts_min": ts_min, "ts_max": ts_max, "stddev": stddev, "ts_rank": ts_rank,
    "correlation": correlation, "covariance": covariance, "sign": sign,
    "signed_power": signed_power, "safe_divide": safe_divide, "where": where,
    "adv": adv, "log": _log, "abs": _abs,
}
ALLOWED_INPUTS = ("open", "high", "low", "close", "volume", "returns", "vwap", "adv20")

_ALLOWED_NODES = (
    ast.Expression, ast.Call, ast.Name, ast.Load, ast.Constant,
    ast.BinOp, ast.UnaryOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
    ast.USub, ast.UAdd,
)

def validate_ast(tree: ast.AST) -> None:
    allowed_names = set(ALLOWED_OPERATORS) | set(ALLOWED_INPUTS)
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_OPERATORS:
                raise FormulaError("call to non-whitelisted function")
            if node.keywords:
                raise FormulaError("keyword arguments are not allowed in formulas")
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise FormulaError(
                f"unknown name '{node.id}'. allowed inputs: {sorted(ALLOWED_INPUTS)}; "
                f"allowed operators: {sorted(ALLOWED_OPERATORS)}"
            )
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise FormulaError(f"only numeric constants allowed, got {node.value!r}")

def evaluate_formula(expr: str, panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"could not parse formula: {exc}") from exc
    validate_ast(tree)
    namespace = {**ALLOWED_OPERATORS, **{k: panels[k] for k in ALLOWED_INPUTS if k in panels}}
    result = eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}, namespace)
    if not isinstance(result, pd.DataFrame):
        raise FormulaError("formula must evaluate to a panel (DataFrame), got a scalar/other")
    return _finite(result)
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tester/test_formula_dsl.py -q` → all pass.

- [ ] **Step 5: Reproduce a known alpha101 formula (equivalence test)**

Append:
```python
# tester/test_formula_dsl.py
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
```
Run: `python -m pytest tester/test_formula_dsl.py -q` → all pass.

- [ ] **Step 6: Commit** — `git add research_platform/formula_dsl.py tester/test_formula_dsl.py && git commit -m "feat(dsl): AST-whitelist causal formula evaluator"`

---

## Task 2 (D2): `scorecard_html.py` — self-contained inline-SVG chart page

**Files:** Create `research_platform/scorecard_html.py`, `tester/test_scorecard_html.py`.

Design: three pure SVG builders + one assembler. All inline, no `<script src>`, no external URLs, deterministic, theme-neutral (light surface, dark text).

- [ ] **Step 1: Write the failing tests**

```python
# tester/test_scorecard_html.py
from __future__ import annotations
import re
import pandas as pd
from research_platform.scorecard_html import render_scorecard_html

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
```

- [ ] **Step 2: Run to verify fail** — ImportError.

- [ ] **Step 3: Implement**

```python
# research_platform/scorecard_html.py
"""Self-contained HTML scorecard: inline-SVG charts, no external resources, no JS.

Renders a benchmark-comparison view (grouped bars, correlation heatmap,
IC-by-horizon lines) as a single deterministic HTML string. Theme-neutral so it
opens identically anywhere. Metrics only — no verdict.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

_PALETTE = ["#2f6f9f", "#c9772e", "#5a9367", "#8a5a9e", "#a03d3d", "#4a4a4a"]

def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _diverging(value: float) -> str:
    # +1 -> blue, 0 -> white, -1 -> red
    v = max(-1.0, min(1.0, float(value)))
    if v >= 0:
        r, g, b = 255 - 155*v, 255 - 90*v, 255 - 40*v
    else:
        r, g, b = 255 + 40*v, 255 + 90*v, 255 - 40*v
    clamp = lambda c: max(0, min(255, int(c)))
    return f"rgb({clamp(r)},{clamp(g)},{clamp(b)})"

def _bar_chart(factor_tbl: pd.DataFrame, metrics: list[str], width=680, row_h=26) -> str:
    labels = [str(x) for x in factor_tbl["factor"].tolist()]
    groups = [(m, [float(x) for x in factor_tbl[m].tolist()]) for m in metrics if m in factor_tbl.columns]
    if not groups:
        return ""
    all_vals = [v for _, vals in groups for v in vals if np.isfinite(v)]
    lo, hi = (min(all_vals+[0.0]), max(all_vals+[0.0])) if all_vals else (0.0, 1.0)
    span = (hi - lo) or 1.0
    left, chart_w = 130, width - 170
    zero_x = left + (0 - lo) / span * chart_w
    n = len(groups)
    height = 30 + len(labels) * (row_h * n + 8)
    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">']
    y = 24
    for li, lab in enumerate(labels):
        parts.append(f'<text x="8" y="{y+row_h*n/2}" font-size="12" fill="#333">{_esc(lab)}</text>')
        for gi, (m, vals) in enumerate(groups):
            v = vals[li] if np.isfinite(vals[li]) else 0.0
            x = left + (min(v,0) - lo)/span*chart_w
            w = abs(v)/span*chart_w
            parts.append(f'<rect x="{x:.1f}" y="{y+gi*row_h}" width="{max(1,w):.1f}" height="{row_h-6}" fill="{_PALETTE[gi%len(_PALETTE)]}"/>')
            parts.append(f'<text x="{left+chart_w+6}" y="{y+gi*row_h+row_h-8}" font-size="10" fill="#555">{m}={v:.3f}</text>')
        y += row_h*n + 8
    parts.append(f'<line x1="{zero_x:.1f}" y1="16" x2="{zero_x:.1f}" y2="{height-6}" stroke="#999" stroke-width="0.5"/>')
    parts.append("</svg>")
    return "".join(parts)

def _heatmap(corr: pd.DataFrame, cell=54) -> str:
    names = [str(c) for c in corr.columns]
    n = len(names)
    pad = 90
    size = pad + n*cell
    parts = [f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" role="img">']
    for j, cj in enumerate(names):
        parts.append(f'<text x="{pad+j*cell+cell/2}" y="{pad-6}" font-size="10" fill="#333" text-anchor="middle">{_esc(cj[:8])}</text>')
        parts.append(f'<text x="{pad-6}" y="{pad+j*cell+cell/2}" font-size="10" fill="#333" text-anchor="end">{_esc(cj[:8])}</text>')
    for i in range(n):
        for j in range(n):
            v = float(corr.iloc[i, j]) if np.isfinite(corr.iloc[i, j]) else 0.0
            x, y = pad+j*cell, pad+i*cell
            parts.append(f'<rect x="{x}" y="{y}" width="{cell-1}" height="{cell-1}" fill="{_diverging(v)}" stroke="#fff"/>')
            parts.append(f'<text x="{x+cell/2}" y="{y+cell/2+3}" font-size="10" fill="#222" text-anchor="middle">{v:.2f}</text>')
    parts.append("</svg>")
    return "".join(parts)

def _line_chart(ic_by_h: pd.DataFrame, width=680, height=260) -> str:
    horizons = [float(h) for h in ic_by_h.index]
    if not horizons:
        return ""
    left, right, top, bot = 50, width-120, 20, height-30
    xs = {h: left + (i/(max(1,len(horizons)-1)))*(right-left) for i, h in enumerate(horizons)}
    vals = [float(v) for v in ic_by_h.to_numpy().ravel() if np.isfinite(v)]
    lo, hi = (min(vals+[0.0]), max(vals+[0.0])) if vals else (0.0, 1.0)
    span = (hi-lo) or 1.0
    def py(v): return bot - (v-lo)/span*(bot-top)
    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">']
    parts.append(f'<line x1="{left}" y1="{py(0):.1f}" x2="{right}" y2="{py(0):.1f}" stroke="#999" stroke-width="0.5"/>')
    for h in horizons:
        parts.append(f'<text x="{xs[h]:.1f}" y="{height-12}" font-size="10" fill="#555" text-anchor="middle">h={int(h)}</text>')
    for ci, col in enumerate(ic_by_h.columns):
        color = _PALETTE[ci%len(_PALETTE)]
        pts = " ".join(f"{xs[h]:.1f},{py(float(ic_by_h.loc[h,col])):.1f}" for h in horizons if np.isfinite(ic_by_h.loc[h,col]))
        if pts:
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/>')
            parts.append(f'<text x="{right+6}" y="{20+ci*16}" font-size="11" fill="{color}">{_esc(str(col)[:10])}</text>')
    parts.append("</svg>")
    return "".join(parts)

def render_scorecard_html(summary: str, factor_tbl: pd.DataFrame, corr: pd.DataFrame,
                          ic_by_horizon: pd.DataFrame, title: str = "因子验证记分卡") -> str:
    css = ("body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#fafafa;color:#222;margin:24px}"
           "h1{font-size:20px}h2{font-size:15px;margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:4px}"
           ".note{color:#666;font-size:13px}.summary{background:#f0f4f8;border-left:3px solid #2f6f9f;padding:10px 14px;font-size:14px}")
    body = [
        f"<style>{css}</style>",
        f"<h1>{_esc(title)}</h1>",
        f'<div class="summary">{_esc(summary)}</div>',
        '<p class="note">纯指标汇总，无判决 (no verdict)。</p>',
        "<h2>各因子指标对比</h2>", _bar_chart(factor_tbl, ["rank_ic","icir_annualized","portfolio_sharpe"]),
        "<h2>相关性热力图</h2>", _heatmap(corr),
        "<h2>IC by horizon</h2>", _line_chart(ic_by_horizon),
    ]
    return "<!doctype html><meta charset='utf-8'>" + "".join(body) + "\n"
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tester/test_scorecard_html.py -q` → pass.

- [ ] **Step 5: Commit** — `git add research_platform/scorecard_html.py tester/test_scorecard_html.py && git commit -m "feat(scorecard-html): self-contained inline-SVG chart page"`

---

## Task 3 (D3): `scripts/validate_factor.py` — orchestration

**Files:** Create `scripts/validate_factor.py`, `tester/test_validate_factor.py`.

Responsibilities: default benchmark set; build OHLCV panels (incl. `vwap`); evaluate new + benchmark formulas; PIT mask + standardize; multi-horizon IC grid **for the new factor only** → `evaluate_factor_grid`; per-factor IC-by-horizon (all factors, for display); scorecard tables at primary horizon; per-factor portfolio + capacity; regime; auto-summary; write `scorecard.md`+`scorecard.html`+CSV+`formula.txt`; append ledger row.

- [ ] **Step 1: Write the failing test** (synthetic bundle, no network)

```python
# tester/test_validate_factor.py
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
```

- [ ] **Step 2: Run to verify fail** — ImportError.

- [ ] **Step 3: Implement**

```python
# scripts/validate_factor.py
"""Single-factor DSL validator: one formula → full standalone validation report."""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import sys
import warnings

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factor_section.alpha101 import _input_panels
from research_platform.evaluation import evaluate_ic
from research_platform.formula_dsl import FormulaError, evaluate_formula
from research_platform.preprocessing import standardize_panel
from research_platform.regime import calendar_year_labels, group_daily_ic, ic_stability_summary
from research_platform.registry import ExperimentRecord, append_record
from research_platform.reporting import _atomic_text
from research_platform.scorecard import (
    build_correlation_views, build_factor_scorecard, build_group_backtest,
    calmar_ratio, render_scorecard_markdown, sortino_ratio, win_rate, write_scorecard,
)
from research_platform.scorecard_html import render_scorecard_html
from research_platform.significance import evaluate_factor_grid
from research_platform.portfolio import build_buffered_targets, capacity_curve, simulate_portfolio

DEFAULT_BENCHMARKS = {
    "alpha101_012": "sign(delta(volume,1)) * (-delta(close,1))",
    "alpha101_101": "(close - open) / ((high - low) + 0.001)",
    "reversal_5d": "-(close / delay(close,5) - 1)",
    "momentum_126d": "delay(close,5) / delay(close,126) - 1",
}
NEW_NAME = "candidate"
HORIZONS = (1, 5, 10, 21, 42)
AUM_GRID = (1e6, 1e7, 1e8, 1e9)

def build_panels(bundle) -> dict[str, pd.DataFrame]:
    panels = _input_panels(bundle)
    panels["vwap"] = (panels["open"] + panels["high"] + panels["low"] + panels["close"]) / 4.0
    return panels

def new_factor_ic_grid(factor: pd.DataFrame, close: pd.DataFrame, horizons=HORIZONS, min_names=30) -> pd.DataFrame:
    cols = {}
    for h in horizons:
        fwd = close.shift(-h).div(close).sub(1.0)
        cols[(NEW_NAME, h)] = evaluate_ic(factor, fwd, min_names=min_names)
    frame = pd.DataFrame(cols)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["factor", "horizon"])
    return frame.sort_index()

def ic_by_horizon(panels_std: dict[str, pd.DataFrame], close: pd.DataFrame, horizons=HORIZONS, min_names=30) -> pd.DataFrame:
    rows = {}
    for name, f in panels_std.items():
        rows[name] = {h: float(evaluate_ic(f, close.shift(-h).div(close).sub(1.0), min_names=min_names).mean()) for h in horizons}
    return pd.DataFrame(rows, index=list(horizons))

def _factor_portfolio_row(name, score, close, asset_returns, primary_horizon, entry_q=0.2, exit_q=0.3, name_cap=0.05, cost_bps=10.0):
    buffered = build_buffered_targets(score, rebalance_interval=primary_horizon, entry_quantile=entry_q, exit_quantile=exit_q, max_weight=name_cap)
    port = simulate_portfolio(buffered.targets, asset_returns.reindex(index=score.index, columns=score.columns), cost_bps=cost_bps)
    m = port.metrics
    return {
        "factor": name, "annualized_return": m.get("annualized_return"), "sharpe": m.get("sharpe"),
        "sortino": sortino_ratio(port.net_returns), "max_drawdown": m.get("max_drawdown"),
        "calmar": calmar_ratio(m.get("annualized_return", np.nan), m.get("max_drawdown", np.nan)),
        "win_rate": win_rate(port.net_returns), "average_turnover": m.get("average_turnover"),
        "total_cost": m.get("total_cost"),
    }, buffered.targets

def auto_summary(name, grid, best_sharpe, monotonicity, capacity_aum) -> str:
    g = grid[grid["factor"] == name].sort_values(["p_global", "z_stat"], ascending=[True, False])
    if g.empty:
        return "无有效 IC 网格。"
    best = g.iloc[0]
    sig = "显著" if float(best["p_global"]) < 0.05 else "不显著"
    cap = f"~${capacity_aum/1e6:.0f}M" if np.isfinite(capacity_aum) else "n/a"
    return (f"最佳 horizon h={int(best['horizon'])}: RankIC={float(best['ic_bar']):.3f}, "
            f"z={float(best['z_stat']):.2f} ({sig}, p_global={float(best['p_global']):.3f}); "
            f"分组单调性={monotonicity:.2f}; 扣成本 Sharpe={best_sharpe:.2f}; 容量 {cap}。（无判决）")

@dataclass(frozen=True)
class FactorValidationResult:
    slug: str
    output_dir: Path
    summary: str
    tables: dict

def validate_factor(formula: str, name: str = NEW_NAME, benchmarks: dict | None = None,
                    output_dir: str | Path = PROJECT_ROOT / "outputs" / "factor_validation",
                    project_root: Path = PROJECT_ROOT, bundle=None, primary_horizon: int = 5,
                    min_names: int = 30) -> FactorValidationResult:
    benchmarks = DEFAULT_BENCHMARKS if benchmarks is None else benchmarks
    if bundle is None:
        bundle = _load_default_bundle(project_root)      # see Step 5
    panels = build_panels(bundle)
    close = panels["close"]
    asset_returns = close.pct_change(fill_method=None)

    factor_panels = {name: evaluate_formula(formula, panels)}
    for bname, bformula in benchmarks.items():
        if bname == name:
            continue
        try:
            factor_panels[bname] = evaluate_formula(bformula, panels)
        except FormulaError as exc:
            warnings.warn(f"benchmark {bname} skipped: {exc}")
    std = {k: standardize_panel(v) for k, v in factor_panels.items()}

    forward = close.shift(-primary_horizon).div(close).sub(1.0)
    grid = evaluate_factor_grid(new_factor_ic_grid(std[name], close, min_names=min_names))
    ic_h = ic_by_horizon(std, close, min_names=min_names)
    factor_tbl = build_factor_scorecard(std, forward, close.index, n_groups=5, min_names=min_names, horizon=primary_horizon)
    group_tbl = build_group_backtest(std, forward, n_groups=5, min_names=min_names, horizon=primary_horizon)
    corr = build_correlation_views(std, forward, close.index, min_names=min_names)

    port_rows, targets_by_factor = [], {}
    for fname, score in std.items():
        row, targets = _factor_portfolio_row(fname, score, close, asset_returns, primary_horizon)
        port_rows.append(row); targets_by_factor[fname] = targets
    portfolio_tbl = pd.DataFrame(port_rows)
    cap = capacity_curve(targets_by_factor[name], asset_returns.reindex(index=std[name].index, columns=std[name].columns), panels["adv20"], AUM_GRID)

    regime_rows = []
    for fname, score in std.items():
        summ = ic_stability_summary(group_daily_ic(evaluate_ic(score, forward, min_names=min_names), calendar_year_labels(pd.DatetimeIndex(score.index))))
        regime_rows.append({"factor": fname, "sign_consistency": summ["sign_consistency"], "n_subperiods": summ["n_subperiods"]})
    regime_tbl = pd.DataFrame(regime_rows)

    # merge portfolio sharpe into the factor table for the bar chart
    factor_tbl = factor_tbl.merge(portfolio_tbl[["factor", "sharpe"]].rename(columns={"sharpe": "portfolio_sharpe"}), on="factor", how="left")
    best_sharpe = float(portfolio_tbl.loc[portfolio_tbl["factor"] == name, "sharpe"].iloc[0])
    monotonicity = float(factor_tbl.loc[factor_tbl["factor"] == name, "monotonicity"].iloc[0])
    cap_aum = float(cap.loc[cap["net_sharpe"] >= 0, "aum"].max()) if (cap["net_sharpe"] >= 0).any() else np.nan
    summary = auto_summary(name, grid, best_sharpe, monotonicity, cap_aum)

    tables = {"factor_scorecard": factor_tbl, "significance_grid": grid.reset_index(drop=True),
              "group_backtest": group_tbl, "portfolio": portfolio_tbl, "capacity": cap,
              "regime": regime_tbl, "value_matrix": corr["value_matrix"], "ic_matrix": corr["ic_matrix"],
              "clusters": corr["clusters"]}
    markdown = f"# 因子验证记分卡\n\n> {summary}\n\n" + render_scorecard_markdown(tables).split("\n", 1)[1]
    html = render_scorecard_html(summary, factor_tbl, corr["value_matrix"], ic_h)

    slug = sha256(formula.encode("utf-8")).hexdigest()[:12]
    out = Path(output_dir) / slug
    write_scorecard(tables, markdown, out)
    _atomic_text(out / "scorecard.html", html)
    _atomic_text(out / "formula.txt", f"name={name}\nformula={formula}\nbenchmarks={benchmarks}\nprimary_horizon={primary_horizon}\n")
    best = grid.sort_values("p_global").iloc[0]
    append_record(Path(output_dir) / "ledger.jsonl", ExperimentRecord(
        name=name, family_wise_p=float(best["p_global"]), n_hypotheses=len(HORIZONS),
        passed_local=bool(best["p_global"] < 0.05), family="single_factor_dsl",
        metadata={"formula": formula, "slug": slug, "best_horizon": int(best["horizon"]), "portfolio_sharpe": best_sharpe}))
    return FactorValidationResult(slug=slug, output_dir=out, summary=summary, tables=tables)
```

- [ ] **Step 4: Run the unit tests** — `python -m pytest tester/test_validate_factor.py -q` → pass (the four functions under test don't need `_load_default_bundle`).

- [ ] **Step 5: Add real-data loader + a guarded end-to-end test**

Add `_load_default_bundle(project_root)` mirroring `run_alpha101_correlation_oos._load_real_inputs`'s bundle load (read `data/reports/composite_alpha_latest.csv` for the date window + `data/metadata/sp500_constituents.csv` for the ticker filter, then `load_research_ohlcv(project_root/"data"/"raw", tickers, start=dates.min()-BDay(320), end=dates.max())`). Then append:
```python
# tester/test_validate_factor.py
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
```
Run: `python -m pytest tester/test_validate_factor.py -q` → pass.

- [ ] **Step 6: Commit** — `git add scripts/validate_factor.py tester/test_validate_factor.py && git commit -m "feat(validator): single-factor DSL orchestration"`

---

## Task 4 (D4): CLI subcommand + real run

**Files:** Modify `research_platform/cli.py`, `tester/test_cli.py`.

- [ ] **Step 1: Add failing CLI test**
```python
# tester/test_cli.py  (append)
def test_validate_factor_cli_parses(capsys):
    from research_platform.cli import build_parser
    args = build_parser().parse_args(["validate-factor", "--formula", "-(close/delay(close,5)-1)", "--name", "myrev"])
    assert args.command == "validate-factor" and args.formula and args.name == "myrev"
```

- [ ] **Step 2: Run to verify fail** — `argument command: invalid choice: 'validate-factor'`.

- [ ] **Step 3: Implement** — in `build_parser()` add:
```python
    vf = commands.add_parser("validate-factor", help="validate one DSL factor formula end-to-end")
    vf.add_argument("--formula", required=True)
    vf.add_argument("--name", default="candidate")
    vf.add_argument("--output-dir", default="outputs/factor_validation")
```
and in `main()`:
```python
    if args.command == "validate-factor":
        from scripts.validate_factor import validate_factor
        res = validate_factor(args.formula, name=args.name, output_dir=args.output_dir)
        print(json.dumps({"slug": res.slug, "summary": res.summary, "output_dir": str(res.output_dir)}, ensure_ascii=False))
        return 0
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tester/test_cli.py -q` → pass.

- [ ] **Step 5: Real run (eyeball the product)**
Run:
```bash
python -m research_platform.cli validate-factor --formula "-(close / delay(close,5) - 1)" --name rev5
```
Expected: prints slug + summary; `outputs/factor_validation/<slug>/scorecard.md`, `scorecard.html`, `formula.txt` exist; `outputs/factor_validation/ledger.jsonl` has a row. Open the HTML to confirm 3 charts render and it's self-contained.

- [ ] **Step 6: Commit** — `git add research_platform/cli.py tester/test_cli.py && git commit -m "feat(cli): validate-factor subcommand"`

---

## Task 5: Regression + structure/flow review (main agent)

- [ ] Run scorecard/DSL/HTML/validator unit tests: `python -m pytest tester/test_formula_dsl.py tester/test_scorecard_html.py tester/test_validate_factor.py tester/test_cli.py -q` → all pass.
- [ ] Run the fast unit suite (exclude slow real/integration): `python -m pytest tester -q --ignore=tester/test_backtester.py --ignore=tester/test_oos_real.py --ignore=tester/test_research_platform_real.py --ignore=tester/test_alpha101_correlation_real.py` → pass.
- [ ] Overall structure/flow review: confirm module boundaries (DSL pure/causal; HTML pure/self-contained; orchestration reuses existing modules; significance family = new factor only; no verdict logic). Fix any drift.
- [ ] Final real run of `validate-factor` and open the HTML.
- [ ] Commit any final fixes.

---

## Self-Review Notes

- **Spec coverage:** DSL whitelist §4 → Task 1; benchmark set §5 → Task 3 (`DEFAULT_BENCHMARKS`); significance family = new factor only §6/§7 → Task 3 (`new_factor_ic_grid` emits only `candidate` columns; test asserts it); scorecard tables §6 → reused builders in Task 3; auto-summary §7 → `auto_summary`; outputs §8 → Task 3 writes md/html/csv/formula.txt/ledger; HTML §9 → Task 2; error handling §10 → `FormulaError` + benchmark skip + degenerate handling inherited from `build_*`; testing §11 → each task's tests.
- **Deviation:** capacity summary uses "max AUM with net_sharpe ≥ 0" as the reported capacity figure (spec §7 said "net Sharpe 掉到某阈值前的 AUM"; 0 is the concrete threshold). Documented in `auto_summary`.
- **Type consistency:** `NEW_NAME="candidate"` used consistently in `new_factor_ic_grid`, `auto_summary`, and the family test; `render_scorecard_html(summary, factor_tbl, corr, ic_by_horizon)` signature matches its Task 2 definition and Task 3 call; `factor_tbl` carries `portfolio_sharpe` before being passed to the bar chart.
