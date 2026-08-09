"""Run the approved fold-specific OOS alpha improvement experiment."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factor_section.factor_design import FactorDesigner
from research_platform.contracts import dataframe_fingerprint
from research_platform.evaluation import group_stratification_table
from research_platform.oos import OOSConfig, OOSExperimentResult, run_oos_experiment
from research_platform.portfolio import build_buffered_targets, simulate_portfolio
from research_platform.reporting import build_oos_report_tables, write_oos_report
from research_platform.scorecard import (
    build_correlation_views,
    build_factor_scorecard,
    build_group_backtest,
    build_portfolio_scorecard,
    render_scorecard_markdown,
    write_scorecard,
)
from research_platform.universe import SizeBucket, apply_size_bucket, resolve_bucket
from scripts.run_research_platform_validation import (
    _load_classification_snapshot,
    _load_factor_report,
    _normalized_index,
    load_pit_context,
)


@dataclass(frozen=True)
class RealOOSValidationResult:
    experiment: OOSExperimentResult
    tables: dict[str, pd.DataFrame]
    quality: dict
    metadata: dict


_INPUT_CACHE: dict[
    tuple,
    tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame, str, dict, pd.DataFrame],
] = {}


def _causal_factor_inputs(
    project_root: Path,
    dates: pd.DatetimeIndex,
    tickers: list[str],
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    """Build current technical factors without full-sample time-series clipping.

    Also returns a per-name dollar-ADV panel (20-day mean of close x volume) for
    the liquidity/capacity cost model.
    """
    start = dates.min() - pd.offsets.BDay(300)
    end = dates.max()
    designer = FactorDesigner()
    designer._winsorize_series = lambda series, lower_q=0.01, upper_q=0.99: series.replace(
        [np.inf, -np.inf], np.nan
    )
    factor_series = {name: {} for name in designer.VOLUME_PRICE_ALPHA_FACTORS}
    close_series = {}
    advol_series = {}
    for ticker in tickers:
        path = project_root / "data" / "cleaned" / f"{ticker}_cleaned.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path, index_col=0)
        frame.index = _normalized_index(frame.index)
        frame = frame.loc[frame.index.notna()]
        frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
        required = ["Open", "High", "Low", "Close", "Volume"]
        if not set(required).issubset(frame.columns):
            continue
        frame = frame.loc[start:end, required].apply(pd.to_numeric, errors="coerce")
        if frame.empty:
            continue
        table = designer.build_factor_table(frame)
        close_series[ticker] = frame["Close"].reindex(dates)
        dollar_volume = frame["Close"] * frame["Volume"]
        advol_series[ticker] = dollar_volume.rolling(20, min_periods=5).mean().reindex(dates)
        for name in factor_series:
            factor_series[name][ticker] = table[name].reindex(dates)
    if not close_series:
        raise FileNotFoundError("no usable cleaned OHLCV files were found")
    close = pd.DataFrame(close_series, index=dates)
    adv_dollar = pd.DataFrame(advol_series, index=dates).reindex(columns=close.columns)
    factors = {
        name: pd.DataFrame(series, index=dates).reindex(columns=close.columns)
        for name, series in factor_series.items()
    }
    return factors, close, adv_dollar


def _load_real_inputs(
    project_root: Path,
    allow_network: bool,
    universe: str | SizeBucket | None = None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame, str, dict, pd.DataFrame]:
    report_path = project_root / "data" / "reports" / "composite_alpha_latest.csv"
    report, _ = _load_factor_report(report_path)
    dates = report.dropna(how="all").index
    classification = _load_classification_snapshot(
        project_root / "data" / "metadata" / "sp500_constituents.csv",
        allow_network=allow_network,
    )
    tickers = [
        ticker
        for ticker in report.columns
        if pd.notna(classification.get(ticker))
        and (project_root / "data" / "cleaned" / f"{ticker}_cleaned.csv").exists()
    ]
    bucket = None if universe is None else resolve_bucket(universe)
    cache_key = (
        str(project_root.resolve()),
        report_path.stat().st_mtime_ns,
        len(dates),
        tuple(tickers),
        None if bucket is None else bucket.name,
    )
    if cache_key in _INPUT_CACHE:
        return _INPUT_CACHE[cache_key]
    factors, close, adv_dollar = _causal_factor_inputs(project_root, dates, tickers)
    industry, member_mask, pit_meta = load_pit_context(
        project_root, close.index, close.columns, allow_network=allow_network
    )
    # Universe axis: optionally restrict PIT members to a size bucket (e.g. the
    # smaller half of the index), ranking by dollar ADV each date.
    universe_name = "sp500_all"
    if bucket is not None:
        member_mask = apply_size_bucket(member_mask, adv_dollar, bucket)
        industry = industry.where(member_mask)
        universe_name = bucket.name
    pit_meta = {
        **pit_meta,
        "universe": universe_name,
        "universe_effective_size_end": int(member_mask.iloc[-1].sum())
        if len(member_mask)
        else 0,
    }
    # Point-in-time universe: mask factor cells for names not yet in the index so
    # IC, selection, scoring, and portfolio construction all exclude non-members.
    factors = {name: panel.where(member_mask) for name, panel in factors.items()}
    digest = sha256()
    digest.update(dataframe_fingerprint(close).encode())
    for name in sorted(factors):
        digest.update(name.encode())
        digest.update(dataframe_fingerprint(factors[name]).encode())
    fingerprint = digest.hexdigest()
    _INPUT_CACHE[cache_key] = (factors, close, industry, fingerprint, pit_meta, adv_dollar)
    return _INPUT_CACHE[cache_key]


def hash_outputs(output_dir: str | Path) -> str:
    digest = sha256()
    for path in sorted(Path(output_dir).glob("*")):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def run_real_oos_validation(
    output_dir: str | Path,
    config_path: str | Path = PROJECT_ROOT / "configs" / "oos_alpha_improvement.yaml",
    project_root: Path = PROJECT_ROOT,
    allow_network: bool = False,
    universe: str | SizeBucket | None = None,
) -> RealOOSValidationResult:
    config = OOSConfig.from_yaml(config_path)
    factors, close, industry, fingerprint, pit_meta, adv_dollar = _load_real_inputs(
        project_root, allow_network=allow_network, universe=universe
    )
    experiment = run_oos_experiment(factors, close, industry, config=config)
    raw_daily = build_buffered_targets(
        experiment.scores["raw"],
        rebalance_interval=1,
        entry_quantile=config.entry_quantile,
        exit_quantile=config.exit_quantile,
        max_weight=config.name_weight_cap,
    )
    asset_returns = close.pct_change(fill_method=None).reindex(
        index=experiment.scores["raw"].index
    )
    daily_baseline = simulate_portfolio(
        raw_daily.targets, asset_returns, cost_bps=config.cost_bps
    )
    baseline_annual_cost = float(
        daily_baseline.turnover.mean() * 252 * config.cost_bps / 10_000
    )
    forward_returns = close.shift(-config.horizon).div(close).sub(1.0)
    tables, quality = build_oos_report_tables(
        experiment,
        forward_returns=forward_returns,
        asset_returns=asset_returns,
        industry=industry,
        baseline_annual_cost=baseline_annual_cost,
        cost_stress_bps=config.cost_stress_bps,
        min_names=config.min_names,
        adv_dollar=adv_dollar,
    )
    metadata = {
        "data_fingerprint": fingerprint,
        "config": config.to_dict(),
        "factor_names": sorted(factors),
        "classification_taxonomy": "current GICS snapshot",
        **pit_meta,
        "factor_winsorization": "disabled to avoid full-sample leakage",
        "oos_start": experiment.scores["raw"].index.min(),
        "oos_end": experiment.scores["raw"].index.max(),
    }
    write_oos_report(tables, quality, metadata, output_dir)

    factor_tbl = build_factor_scorecard(
        factors, forward_returns, experiment.scores["raw"].index,
        n_groups=5, min_names=config.min_names, horizon=config.horizon,
    )
    portfolio_tbl = build_portfolio_scorecard(
        experiment, forward_returns, industry, min_names=config.min_names
    )
    group_tbl = build_group_backtest(
        {"composite": experiment.scores["raw"]}, forward_returns,
        min_names=config.min_names, horizon=config.horizon,
    )
    group_summary = group_stratification_table(
        experiment.scores, forward_returns, n_groups=5, min_names=config.min_names
    )
    corr = build_correlation_views(
        factors, forward_returns, experiment.scores["raw"].index, min_names=config.min_names
    )
    scorecard_tables = {
        "factor_scorecard": factor_tbl,
        "portfolio_scorecard": portfolio_tbl,
        "group_backtest": group_tbl,
        "group_summary": group_summary,
        "value_matrix": corr["value_matrix"],
        "ic_matrix": corr["ic_matrix"],
        "clusters": corr["clusters"],
    }
    markdown = render_scorecard_markdown(scorecard_tables)
    write_scorecard(scorecard_tables, markdown, output_dir)

    return RealOOSValidationResult(experiment, tables, quality, metadata)


def main() -> int:
    result = run_real_oos_validation(
        config_path=PROJECT_ROOT / "configs" / "oos_alpha_improvement.yaml",
        output_dir=PROJECT_ROOT / "outputs" / "oos_alpha_improvement",
        allow_network=False,
    )
    failed = [name for name, passed in result.quality["gates"].items() if not passed]
    print(
        json.dumps(
            {"failed_gates": failed, "quality": result.quality},
            indent=2,
            default=lambda value: value.item() if isinstance(value, np.generic) else value,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
