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
from research_platform.oos import OOSConfig, OOSExperimentResult, run_oos_experiment
from research_platform.portfolio import build_buffered_targets, simulate_portfolio
from research_platform.reporting import build_oos_report_tables, write_oos_report
from scripts.run_research_platform_validation import (
    _load_classification_snapshot,
    _load_factor_report,
    _normalized_index,
)


@dataclass(frozen=True)
class RealOOSValidationResult:
    experiment: OOSExperimentResult
    tables: dict[str, pd.DataFrame]
    quality: dict
    metadata: dict


_INPUT_CACHE: dict[tuple, tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame, str]] = {}


def _causal_factor_inputs(
    project_root: Path,
    dates: pd.DatetimeIndex,
    tickers: list[str],
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Build current technical factors without full-sample time-series clipping."""
    start = dates.min() - pd.offsets.BDay(300)
    end = dates.max()
    designer = FactorDesigner()
    designer._winsorize_series = lambda series, lower_q=0.01, upper_q=0.99: series.replace(
        [np.inf, -np.inf], np.nan
    )
    factor_series = {name: {} for name in designer.VOLUME_PRICE_ALPHA_FACTORS}
    close_series = {}
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
        for name in factor_series:
            factor_series[name][ticker] = table[name].reindex(dates)
    if not close_series:
        raise FileNotFoundError("no usable cleaned OHLCV files were found")
    close = pd.DataFrame(close_series, index=dates)
    factors = {
        name: pd.DataFrame(series, index=dates).reindex(columns=close.columns)
        for name, series in factor_series.items()
    }
    return factors, close


def _load_real_inputs(
    project_root: Path,
    allow_network: bool,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame, str]:
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
    cache_key = (
        str(project_root.resolve()),
        report_path.stat().st_mtime_ns,
        len(dates),
        tuple(tickers),
    )
    if cache_key in _INPUT_CACHE:
        return _INPUT_CACHE[cache_key]
    factors, close = _causal_factor_inputs(project_root, dates, tickers)
    industry_row = classification.reindex(close.columns)
    industry = pd.DataFrame(
        np.tile(industry_row.to_numpy(), (len(dates), 1)),
        index=dates,
        columns=close.columns,
    )
    digest = sha256()
    digest.update(dataframe_fingerprint(close).encode())
    for name in sorted(factors):
        digest.update(name.encode())
        digest.update(dataframe_fingerprint(factors[name]).encode())
    fingerprint = digest.hexdigest()
    _INPUT_CACHE[cache_key] = (factors, close, industry, fingerprint)
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
) -> RealOOSValidationResult:
    config = OOSConfig.from_yaml(config_path)
    factors, close, industry, fingerprint = _load_real_inputs(
        project_root, allow_network=allow_network
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
    )
    metadata = {
        "data_fingerprint": fingerprint,
        "config": config.to_dict(),
        "factor_names": sorted(factors),
        "classification_taxonomy": "current GICS snapshot",
        "classification_point_in_time": False,
        "factor_winsorization": "disabled to avoid full-sample leakage",
        "oos_start": experiment.scores["raw"].index.min(),
        "oos_end": experiment.scores["raw"].index.max(),
    }
    write_oos_report(tables, quality, metadata, output_dir)
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
