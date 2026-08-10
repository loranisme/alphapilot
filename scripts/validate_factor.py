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
from research_platform.market_data import load_research_ohlcv
from research_platform.preprocessing import neutralize_panel, standardize_panel
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
from scripts.run_research_platform_validation import load_pit_context

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

def new_factor_ic_grid(factor: pd.DataFrame, close: pd.DataFrame, horizons=HORIZONS, min_names=30, name: str = NEW_NAME) -> pd.DataFrame:
    cols = {}
    for h in horizons:
        fwd = close.shift(-h).div(close).sub(1.0)
        cols[(name, h)] = evaluate_ic(factor, fwd, min_names=min_names)
    frame = pd.DataFrame(cols)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["factor", "horizon"])
    return frame.sort_index()

def ic_by_horizon(panels_std: dict[str, pd.DataFrame], close: pd.DataFrame, horizons=HORIZONS, min_names=30) -> pd.DataFrame:
    rows = {}
    for name, f in panels_std.items():
        rows[name] = {h: float(evaluate_ic(f, close.shift(-h).div(close).sub(1.0), min_names=min_names).mean()) for h in horizons}
    return pd.DataFrame(rows, index=list(horizons))

def _factor_portfolio_row(name, score, forward, asset_returns, primary_horizon, entry_q=0.2, exit_q=0.3, name_cap=0.05, cost_bps=10.0, min_names=30):
    # Trade the factor in its IC-implied direction so a wrong-signed factor is not
    # mistaken for "no alpha", and report gross vs net so cost drag ("real but
    # untradeable") is visible at a glance.
    ic_mean = float(evaluate_ic(score, forward, min_names=min_names).mean())
    direction = -1 if np.isfinite(ic_mean) and ic_mean < 0 else 1
    ar = asset_returns.reindex(index=score.index, columns=score.columns)
    buffered = build_buffered_targets(score * direction, rebalance_interval=primary_horizon, entry_quantile=entry_q, exit_quantile=exit_q, max_weight=name_cap)
    gross = simulate_portfolio(buffered.targets, ar, cost_bps=0.0)
    net = simulate_portfolio(buffered.targets, ar, cost_bps=cost_bps)
    gm, nm = gross.metrics, net.metrics
    return {
        "factor": name, "direction": direction,
        "gross_sharpe": gm.get("sharpe"), "net_sharpe": nm.get("sharpe"),
        "cost_drag": float(gm.get("annualized_return", np.nan) - nm.get("annualized_return", np.nan)),
        "annualized_return": nm.get("annualized_return"),
        "sortino": sortino_ratio(net.net_returns), "max_drawdown": nm.get("max_drawdown"),
        "calmar": calmar_ratio(nm.get("annualized_return", np.nan), nm.get("max_drawdown", np.nan)),
        "win_rate": win_rate(net.net_returns), "average_turnover": nm.get("average_turnover"),
        "total_cost": nm.get("total_cost"),
    }, buffered.targets

def auto_summary(name, grid, gross_sharpe, net_sharpe, monotonicity, capacity_aum) -> str:
    g = grid[grid["factor"] == name].assign(_abs_z=lambda d: d["z_stat"].abs())
    g = g.sort_values(["p_global", "_abs_z"], ascending=[True, False])
    if g.empty:
        return "无有效 IC 网格。"
    best = g.iloc[0]
    sig = "显著" if float(best["p_global"]) < 0.05 else "不显著"
    cap = f"~${capacity_aum/1e6:.0f}M" if np.isfinite(capacity_aum) else "n/a"
    return (f"最佳 horizon h={int(best['horizon'])}: RankIC={float(best['ic_bar']):.3f}, "
            f"z={float(best['z_stat']):.2f} ({sig}, p_global={float(best['p_global']):.3f}); "
            f"分组单调性={monotonicity:.2f}; 方向拟合后 Sharpe 扣成本前={gross_sharpe:.2f} / 扣成本后={net_sharpe:.2f}; "
            f"容量 {cap}。（无判决）")

def _load_default_bundle(project_root: Path = PROJECT_ROOT):
    """Load the project's real OHLCV bundle, mirroring
    ``scripts.run_alpha101_correlation_oos._load_real_inputs``'s bundle load: the
    factor-report date window plus the S&P classification snapshot's ticker
    filter, then a plain ``load_research_ohlcv`` over the raw CSVs.
    """
    from scripts.run_research_platform_validation import (
        _load_classification_snapshot, _load_factor_report,
    )

    report_path = project_root / "data" / "reports" / "composite_alpha_latest.csv"
    report, _invalid_dates = _load_factor_report(report_path)
    dates = report.dropna(how="all").index
    classification = _load_classification_snapshot(
        project_root / "data" / "metadata" / "sp500_constituents.csv", allow_network=False,
    )
    raw_dir = project_root / "data" / "raw"
    tickers = [
        ticker
        for ticker in report.columns
        if pd.notna(classification.get(ticker)) and (raw_dir / f"{ticker}_20years.csv").exists()
    ]
    return load_research_ohlcv(
        raw_dir, tickers, start=dates.min() - pd.offsets.BDay(320), end=dates.max(),
    )

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
    if name in benchmarks:
        raise ValueError(f"factor name {name!r} collides with a benchmark name; choose another --name")
    if bundle is None:
        bundle = _load_default_bundle(project_root)
    panels = build_panels(bundle)
    close = panels["close"]
    asset_returns = close.pct_change(fill_method=None)

    factor_panels = {name: evaluate_formula(formula, panels)}
    for bname, bformula in benchmarks.items():
        try:
            factor_panels[bname] = evaluate_formula(bformula, panels)
        except FormulaError as exc:
            warnings.warn(f"benchmark {bname} skipped: {exc}")

    # Point-in-time universe: exclude names on dates before they joined the
    # index so cross-sectional standardization never sees them (matches
    # scripts.run_research_platform_validation.build_real_inputs's pattern).
    industry, member_mask, pit_meta = load_pit_context(
        project_root, close.index, close.columns, allow_network=False
    )
    factor_panels = {k: v.where(member_mask) for k, v in factor_panels.items()}
    std = {k: standardize_panel(v) for k, v in factor_panels.items()}

    forward = close.shift(-primary_horizon).div(close).sub(1.0)
    grid = evaluate_factor_grid(new_factor_ic_grid(std[name], close, min_names=min_names, name=name))
    ic_h = ic_by_horizon(std, close, min_names=min_names)
    factor_tbl = build_factor_scorecard(std, forward, close.index, n_groups=5, min_names=min_names, horizon=primary_horizon)
    group_tbl = build_group_backtest(std, forward, n_groups=5, min_names=min_names, horizon=primary_horizon)
    corr = build_correlation_views(std, forward, close.index, min_names=min_names)

    # Industry-neutral variant: factor layer + significance each get a raw and
    # a neutral cut; portfolio/capacity stay on raw only (spec §6/§7).
    neutral = {k: neutralize_panel(v, industry, min_names=min_names).values for k, v in std.items()}
    factor_tbl_neutral = build_factor_scorecard(neutral, forward, close.index, n_groups=5, min_names=min_names, horizon=primary_horizon)
    grid_neutral = evaluate_factor_grid(new_factor_ic_grid(neutral[name], close, min_names=min_names, name=name)).reset_index(drop=True)

    port_rows, targets_by_factor = [], {}
    for fname, score in std.items():
        row, targets = _factor_portfolio_row(fname, score, forward, asset_returns, primary_horizon, min_names=min_names)
        port_rows.append(row); targets_by_factor[fname] = targets
    portfolio_tbl = pd.DataFrame(port_rows)
    cap = capacity_curve(targets_by_factor[name], asset_returns.reindex(index=std[name].index, columns=std[name].columns), panels["adv20"], AUM_GRID)

    regime_rows = []
    for fname, score in std.items():
        summ = ic_stability_summary(group_daily_ic(evaluate_ic(score, forward, min_names=min_names), calendar_year_labels(pd.DatetimeIndex(score.index))))
        regime_rows.append({"factor": fname, "sign_consistency": summ["sign_consistency"], "n_subperiods": summ["n_subperiods"]})
    regime_tbl = pd.DataFrame(regime_rows)

    # merge net portfolio sharpe into the factor table for the bar chart
    factor_tbl = factor_tbl.merge(portfolio_tbl[["factor", "net_sharpe"]].rename(columns={"net_sharpe": "portfolio_sharpe"}), on="factor", how="left")
    new_port = portfolio_tbl.loc[portfolio_tbl["factor"] == name].iloc[0]
    gross_sharpe = float(new_port["gross_sharpe"])
    net_sharpe = float(new_port["net_sharpe"])
    monotonicity = float(factor_tbl.loc[factor_tbl["factor"] == name, "monotonicity"].iloc[0])
    cap_aum = float(cap.loc[cap["net_sharpe"] >= 0, "aum"].max()) if (cap["net_sharpe"] >= 0).any() else np.nan
    summary = auto_summary(name, grid, gross_sharpe, net_sharpe, monotonicity, cap_aum)

    tables = {"factor_scorecard": factor_tbl, "significance_grid": grid.reset_index(drop=True),
              "factor_scorecard_neutral": factor_tbl_neutral, "significance_grid_neutral": grid_neutral,
              "group_backtest": group_tbl, "portfolio": portfolio_tbl, "capacity": cap,
              "regime": regime_tbl, "value_matrix": corr["value_matrix"], "ic_matrix": corr["ic_matrix"],
              "clusters": corr["clusters"]}
    markdown = f"# 因子验证记分卡\n\n> {summary}\n\n" + render_scorecard_markdown(tables).split("\n", 1)[1]
    html = render_scorecard_html(summary, factor_tbl, corr["value_matrix"], ic_h)

    slug = sha256(formula.encode("utf-8")).hexdigest()[:12]
    out = Path(output_dir) / slug
    write_scorecard(tables, markdown, out)
    _atomic_text(out / "scorecard.html", html)
    _atomic_text(out / "formula.txt", f"name={name}\nformula={formula}\nbenchmarks={benchmarks}\nprimary_horizon={primary_horizon}\npit_meta={pit_meta}\n")
    best = grid.sort_values("p_global").iloc[0]
    append_record(Path(output_dir) / "ledger.jsonl", ExperimentRecord(
        name=name, family_wise_p=float(best["p_global"]), n_hypotheses=len(HORIZONS),
        passed_local=bool(best["p_global"] < 0.05), family="single_factor_dsl",
        metadata={"formula": formula, "slug": slug, "best_horizon": int(best["horizon"]), "gross_sharpe": gross_sharpe, "net_sharpe": net_sharpe, "pit_meta": pit_meta}))
    return FactorValidationResult(slug=slug, output_dir=out, summary=summary, tables=tables)
