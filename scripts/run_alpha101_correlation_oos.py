"""Run the fixed Alpha101 correlation-aware A/B/C OOS experiment."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys
import warnings

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factor_section.alpha101 import (
    ALPHA101_REGISTRY,
    SOURCE_URL,
    build_alpha101_factors,
)
from factor_section.factor_design import FactorDesigner
from research_platform.ablation import (
    AblationConfig,
    AblationResult,
    run_alpha101_correlation_ablation,
)
from research_platform.ablation_reporting import (
    build_ablation_report,
    write_ablation_report,
)
from research_platform.contracts import dataframe_fingerprint
from research_platform.market_data import ResearchOHLCVBundle, load_research_ohlcv
from scripts.run_research_platform_validation import (
    _load_classification_snapshot,
    _load_factor_report,
)


@dataclass(frozen=True)
class RealAblationValidationResult:
    ablation: AblationResult
    tables: dict[str, pd.DataFrame]
    quality: dict[str, object]
    metadata: dict[str, object]


_INPUT_CACHE: dict[
    tuple,
    tuple[
        dict[str, pd.DataFrame],
        dict[str, pd.DataFrame],
        pd.DataFrame,
        pd.DataFrame,
        dict[str, object],
    ],
] = {}


def _build_factor_inputs(
    bundle: ResearchOHLCVBundle,
    dates: pd.DatetimeIndex,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], pd.DataFrame]:
    designer = FactorDesigner()
    designer._winsorize_series = (
        lambda series, lower_q=0.01, upper_q=0.99: series.replace(
            [np.inf, -np.inf], np.nan
        )
    )
    factor_series = {name: {} for name in designer.VOLUME_PRICE_ALPHA_FACTORS}
    close_series = {}
    for ticker, frame in bundle.frames.items():
        valid_frame = frame.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="The default fill_method='pad' in Series.pct_change is deprecated",
                category=FutureWarning,
            )
            table = designer.build_factor_table(valid_frame)
        close_series[ticker] = frame["Close"].reindex(dates)
        for name in factor_series:
            factor_series[name][ticker] = table[name].reindex(dates)
    close = pd.DataFrame(close_series, index=dates)
    existing = {
        name: pd.DataFrame(series, index=dates).reindex(columns=close.columns)
        for name, series in factor_series.items()
    }
    alpha101 = {
        name: panel.reindex(index=dates, columns=close.columns)
        for name, panel in build_alpha101_factors(bundle).items()
    }
    return existing, alpha101, close


def _input_fingerprint(
    close: pd.DataFrame,
    existing: dict[str, pd.DataFrame],
    alpha101: dict[str, pd.DataFrame],
    industry: pd.DataFrame,
) -> str:
    digest = sha256()
    for name, frame in [
        ("close", close),
        ("industry", industry.astype(str)),
        *[(name, existing[name]) for name in sorted(existing)],
        *[(name, alpha101[name]) for name in sorted(alpha101)],
    ]:
        digest.update(name.encode("utf-8"))
        digest.update(dataframe_fingerprint(frame).encode("utf-8"))
    return digest.hexdigest()


def _load_real_inputs(
    project_root: Path,
    allow_network: bool,
) -> tuple[
    dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
    pd.DataFrame,
    pd.DataFrame,
    dict[str, object],
]:
    report_path = project_root / "data" / "reports" / "composite_alpha_latest.csv"
    report, invalid_dates = _load_factor_report(report_path)
    dates = report.dropna(how="all").index
    classification = _load_classification_snapshot(
        project_root / "data" / "metadata" / "sp500_constituents.csv",
        allow_network=allow_network,
    )
    raw_dir = project_root / "data" / "raw"
    tickers = [
        ticker
        for ticker in report.columns
        if pd.notna(classification.get(ticker))
        and (raw_dir / f"{ticker}_20years.csv").exists()
    ]
    cache_key = (
        str(project_root.resolve()),
        report_path.stat().st_mtime_ns,
        len(dates),
        tuple(tickers),
    )
    if cache_key in _INPUT_CACHE:
        return _INPUT_CACHE[cache_key]

    bundle = load_research_ohlcv(
        raw_dir,
        tickers,
        start=dates.min() - pd.offsets.BDay(320),
        end=dates.max(),
    )
    existing, alpha101, close = _build_factor_inputs(bundle, dates)
    industry_row = classification.reindex(close.columns)
    industry = pd.DataFrame(
        np.tile(industry_row.to_numpy(), (len(dates), 1)),
        index=dates,
        columns=close.columns,
    )
    metadata = {
        "data_fingerprint": _input_fingerprint(close, existing, alpha101, industry),
        "report_invalid_dates": invalid_dates,
        "requested_tickers": bundle.metadata["requested_tickers"],
        "loaded_tickers": bundle.metadata["loaded_tickers"],
        "missing_tickers": bundle.metadata["missing_tickers"],
        "date_start": dates.min(),
        "date_end": dates.max(),
    }
    payload = (existing, alpha101, close, industry, metadata)
    _INPUT_CACHE[cache_key] = payload
    return payload


def _load_extended_inputs(
    project_root: Path,
    start_date: str = "2019-01-02",
    allow_network: bool = False,
) -> tuple[
    dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
    pd.DataFrame,
    pd.DataFrame,
    dict[str, object],
]:
    """Same universe as ``_load_real_inputs`` but with the research date index
    derived from the raw price calendar (2018-07 onward) instead of the report
    file that caps the window at 2022-04. Factors are computed on full raw
    history so the extended window has proper lookback warmup.
    """
    report_path = project_root / "data" / "reports" / "composite_alpha_latest.csv"
    report, invalid_dates = _load_factor_report(report_path)
    classification = _load_classification_snapshot(
        project_root / "data" / "metadata" / "sp500_constituents.csv",
        allow_network=allow_network,
    )
    raw_dir = project_root / "data" / "raw"
    tickers = [
        ticker
        for ticker in report.columns
        if pd.notna(classification.get(ticker))
        and (raw_dir / f"{ticker}_20years.csv").exists()
    ]
    # Load the full available history (raw files begin ~2018-07).
    bundle = load_research_ohlcv(raw_dir, tickers, start=None, end=None)
    close_all = pd.DataFrame(
        {ticker: frame["Close"] for ticker, frame in bundle.frames.items()}
    ).sort_index()
    start_ts = pd.Timestamp(start_date)
    coverage = close_all.notna().sum(axis=1)
    # A trading day is any date where at least half the peak cross-section trades.
    threshold = max(30, int(0.5 * coverage.max()))
    dates = coverage.index[(coverage >= threshold) & (coverage.index >= start_ts)]
    dates = pd.DatetimeIndex(dates).sort_values()

    existing, alpha101, close = _build_factor_inputs(bundle, dates)
    industry_row = classification.reindex(close.columns)
    industry = pd.DataFrame(
        np.tile(industry_row.to_numpy(), (len(dates), 1)),
        index=dates,
        columns=close.columns,
    )
    metadata = {
        "data_fingerprint": _input_fingerprint(close, existing, alpha101, industry),
        "report_invalid_dates": invalid_dates,
        "requested_tickers": bundle.metadata["requested_tickers"],
        "loaded_tickers": bundle.metadata["loaded_tickers"],
        "missing_tickers": bundle.metadata["missing_tickers"],
        "date_start": dates.min(),
        "date_end": dates.max(),
        "window": "extended_price_calendar",
    }
    return existing, alpha101, close, industry, metadata


def hash_outputs(output_dir: str | Path) -> str:
    digest = sha256()
    for path in sorted(Path(output_dir).glob("*")):
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def run_alpha101_correlation_validation(
    output_dir: str | Path,
    config_path: str | Path = PROJECT_ROOT
    / "configs"
    / "alpha101_correlation_oos.yaml",
    project_root: Path = PROJECT_ROOT,
    allow_network: bool = False,
) -> RealAblationValidationResult:
    config = AblationConfig.from_yaml(config_path)
    existing, alpha101, close, industry, input_metadata = _load_real_inputs(
        project_root, allow_network=allow_network
    )
    ablation = run_alpha101_correlation_ablation(
        existing, alpha101, close, industry, config
    )
    forward_returns = close.shift(-config.oos.horizon).div(close).sub(1.0)
    asset_returns = close.pct_change(fill_method=None)
    tables, quality = build_ablation_report(
        ablation,
        forward_returns,
        asset_returns,
        industry,
        config,
    )
    metadata = {
        **input_metadata,
        "config": config.to_dict(),
        "existing_factor_names": list(existing),
        "alpha101_factor_names": list(alpha101),
        "alpha101_formula_ids": list(ALPHA101_REGISTRY),
        "candidate_counts": {
            name: len(arm.candidate_names) for name, arm in ablation.arms.items()
        },
        "source": SOURCE_URL,
        "source_attribution": "Zura Kakushadze, 101 Formulaic Alphas, arXiv:1601.00991",
        "usage": "personal research",
        "rights_notice": "Appendix A formulae and code rights are retained by their owner.",
        "classification_taxonomy": "current GICS snapshot",
        "classification_point_in_time": False,
        "price_adjustment_semantics": "source prices used as stored",
        "formula_volume_semantics": "reported shares multiplied by OHLC4 price; adv20 is its rolling mean",
        "factor_winsorization": "disabled to avoid full-sample leakage",
        "execution_timing": "signal at t, target executed at t+1",
    }
    write_ablation_report(tables, quality, metadata, output_dir)
    return RealAblationValidationResult(ablation, tables, quality, metadata)


def main() -> int:
    result = run_alpha101_correlation_validation(
        output_dir=PROJECT_ROOT / "outputs" / "alpha101_correlation_oos",
        config_path=PROJECT_ROOT / "configs" / "alpha101_correlation_oos.yaml",
        allow_network=False,
    )
    payload = {
        "engineering_failures": [
            name
            for name, passed in result.quality["engineering_gates"].items()
            if not passed
        ],
        "research_failures": [
            name
            for name, passed in result.quality["research_gates"].items()
            if not passed
        ],
        "output_hash": hash_outputs(
            PROJECT_ROOT / "outputs" / "alpha101_correlation_oos"
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result.quality["engineering_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
