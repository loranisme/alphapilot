"""Run raw-versus-neutralized validation on the project's real data."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research_platform.config import ExperimentConfig
from research_platform.contracts import dataframe_fingerprint
from research_platform.evaluation import generate_purged_folds
from research_platform.experiment import ExperimentInputs, run_experiment
from research_platform.preprocessing import neutralize_panel, standardize_panel, winsorize_panel
from research_platform.providers import (
    build_pit_universe,
    parse_constituents_snapshot,
    pit_industry_panel,
)
from research_platform.reporting import write_result


CONSTITUENTS_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
    "main/data/constituents.csv"
)


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _load_classification_snapshot(
    cache_path: Path,
    allow_network: bool,
) -> pd.Series:
    if not cache_path.exists():
        if not allow_network:
            raise FileNotFoundError(
                f"classification cache missing: {cache_path}; run validation once with network enabled"
            )
        request = Request(
            CONSTITUENTS_URL,
            headers={"User-Agent": "factor-research-platform/0.1 research@example.com"},
        )
        with urlopen(request, timeout=30) as response:
            _atomic_bytes(cache_path, response.read())
    table = pd.read_csv(cache_path)
    required = {"Symbol", "GICS Sector"}
    if not required.issubset(table.columns):
        raise ValueError(f"classification snapshot missing columns: {sorted(required)}")
    table["Symbol"] = table["Symbol"].astype(str).str.replace(".", "-", regex=False)
    return table.drop_duplicates("Symbol").set_index("Symbol")["GICS Sector"]


def _load_membership_changes(project_root: Path) -> pd.DataFrame | None:
    """Load an effective-dated S&P membership changes file if one is cached.

    Expected columns: ``effective_date, added, removed``. Absent by default (the
    free Wikipedia snapshot ships only current members); when present it upgrades
    point-in-time membership from additions-only to full, survivorship-free
    reconstruction via :func:`research_platform.providers.rebuild_membership`.
    """
    path = project_root / "data" / "metadata" / "sp500_changes.csv"
    return pd.read_csv(path) if path.exists() else None


def load_pit_context(
    project_root: Path,
    dates: pd.DatetimeIndex,
    columns,
    allow_network: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Build the point-in-time industry panel and membership mask for a run.

    Returns ``(industry, member_mask, pit_meta)`` where ``industry`` is a
    ``dates x columns`` sector frame with ``NaN`` for non-member cells,
    ``member_mask`` is the aligned boolean membership matrix, and ``pit_meta``
    records the achieved PIT level plus the member-cell classification coverage.
    """
    constituents_path = project_root / "data" / "metadata" / "sp500_constituents.csv"
    # Ensures the snapshot exists (fetching once when network is allowed) and
    # validates the required columns before we parse it for PIT membership.
    _load_classification_snapshot(constituents_path, allow_network=allow_network)
    constituents = parse_constituents_snapshot(pd.read_csv(constituents_path))
    changes = _load_membership_changes(project_root)
    universe, classification_panel, pit_meta = build_pit_universe(
        constituents, pd.DatetimeIndex(dates), changes=changes
    )
    industry, coverage = pit_industry_panel(
        classification_panel, universe, pd.DatetimeIndex(dates), columns
    )
    member_mask = universe.membership.reindex(
        index=pd.DatetimeIndex(dates), columns=list(columns), fill_value=False
    ).astype(bool)
    return industry, member_mask, {**pit_meta, "classification_coverage": coverage}


def _normalized_index(values) -> pd.DatetimeIndex:
    return pd.to_datetime(values, utc=True, errors="coerce").tz_convert(None).normalize()


def _load_factor_report(path: Path) -> tuple[pd.DataFrame, int]:
    raw = pd.read_csv(path, index_col=0)
    parsed = _normalized_index(raw.index)
    invalid_dates = int(parsed.isna().sum())
    raw.index = parsed
    raw = raw.loc[raw.index.notna()]
    raw = raw.loc[~raw.index.duplicated(keep="last")].sort_index()
    return raw.apply(pd.to_numeric, errors="coerce"), invalid_dates


def _load_close_matrix(cleaned_dir: Path, tickers: list[str], dates: pd.DatetimeIndex) -> pd.DataFrame:
    series = {}
    for ticker in tickers:
        path = cleaned_dir / f"{ticker}_cleaned.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path, index_col=0)
        if "Close" not in frame.columns:
            continue
        frame.index = _normalized_index(frame.index)
        frame = frame.loc[~frame.index.duplicated(keep="last")]
        series[ticker] = pd.to_numeric(frame["Close"], errors="coerce")
    if not series:
        raise FileNotFoundError(f"no cleaned close data found under {cleaned_dir}")
    return pd.DataFrame(series).reindex(index=dates, columns=tickers)


def _max_industry_exposure(values: pd.DataFrame, industries: pd.DataFrame) -> float:
    maxima = []
    for date in values.index:
        pair = pd.concat(
            [values.loc[date].rename("value"), industries.loc[date].rename("industry")],
            axis=1,
        ).dropna()
        if pair.empty:
            continue
        maxima.append(float(pair.groupby("industry")["value"].mean().abs().max()))
    return max(maxima, default=np.nan)


def _neutralization_quality(
    standardized: pd.DataFrame,
    diagnostics: pd.DataFrame,
) -> dict[str, float]:
    eligible = standardized.notna().sum(axis=1) > 0
    valid = diagnostics["valid"].reindex(standardized.index).fillna(False).astype(bool)
    eligible_count = int(eligible.sum())
    return {
        "raw_usable_date_ratio": float(eligible.mean()) if len(eligible) else 0.0,
        "neutralization_success_on_eligible_dates": float((valid & eligible).sum() / eligible_count)
        if eligible_count
        else 0.0,
    }


def build_real_inputs(
    config_path: str | Path,
    project_root: Path = PROJECT_ROOT,
    allow_network: bool = True,
) -> tuple[ExperimentConfig, ExperimentInputs, dict]:
    config = ExperimentConfig.from_yaml(config_path)
    raw_path = project_root / "data" / "reports" / "composite_alpha_latest.csv"
    raw, invalid_factor_dates = _load_factor_report(raw_path)

    industries, member_mask, pit_meta = load_pit_context(
        project_root, raw.index, raw.columns, allow_network=allow_network
    )
    coverage = pit_meta["classification_coverage"]
    if coverage < config.classification_coverage:
        raise ValueError(
            f"classification coverage {coverage:.3f} is below required {config.classification_coverage:.3f}"
        )

    close = _load_close_matrix(project_root / "data" / "cleaned", list(raw.columns), raw.index)
    forward_returns = close.pct_change(config.horizon, fill_method=None).shift(-config.horizon)
    asset_returns = close.pct_change(1, fill_method=None)
    # Point-in-time universe: exclude names on dates before they joined the index
    # so cross-sectional standardization and neutralization never see them.
    standardized = standardize_panel(winsorize_panel(raw.where(member_mask)))
    neutralized_result = neutralize_panel(
        standardized,
        industries,
        min_names=config.min_names,
    )
    neutralized = neutralized_result.values

    valid_dates = raw.dropna(how="all").index
    folds = generate_purged_folds(
        valid_dates,
        min_train=min(300, max(30, len(valid_dates) // 3)),
        test_size=min(50, max(10, len(valid_dates) // 10)),
        step=min(50, max(10, len(valid_dates) // 10)),
        horizon=config.horizon,
        purge=config.purge_periods,
        embargo=config.embargo_periods,
    )
    positions = {date: position for position, date in enumerate(valid_dates)}
    overlap_count = sum(
        int(bool(set(train).intersection(test)))
        + int(positions[test[0]] - positions[train[-1]] <= config.horizon)
        for train, test in folds
    )
    neutralization_quality = _neutralization_quality(
        standardized, neutralized_result.diagnostics
    )
    quality = {
        "classification_coverage": coverage,
        "classification_taxonomy": "current GICS snapshot",
        "max_abs_industry_exposure": _max_industry_exposure(neutralized, industries),
        "label_overlap_count": overlap_count,
        "fold_count": len(folds),
        "invalid_factor_date_rows": invalid_factor_dates,
        **pit_meta,
        **neutralization_quality,
    }
    inputs = ExperimentInputs(
        variants={"raw": standardized, "neutralized": neutralized},
        forward_returns=forward_returns,
        asset_returns=asset_returns,
        industry=industries,
        data_fingerprint=dataframe_fingerprint(raw),
    )
    return config, inputs, quality


def execute_validation(
    config: ExperimentConfig,
    inputs: ExperimentInputs,
    engineering_quality: dict,
    output_dir: str | Path,
):
    result = run_experiment(config, inputs)
    result.quality.update(engineering_quality)
    result.quality["gates"].update(
        {
            "industry_exposure": engineering_quality["max_abs_industry_exposure"] <= 1e-8,
            "label_overlap": engineering_quality["label_overlap_count"] == 0,
            "neutralization_dates": engineering_quality[
                "neutralization_success_on_eligible_dates"
            ]
            >= 0.95,
        }
    )
    result.metadata["classification_point_in_time"] = engineering_quality[
        "classification_point_in_time"
    ]
    write_result(result, output_dir)
    return result


def run_validation(
    config_path: str | Path,
    output_dir: str | Path = "outputs/research_platform_validation",
    allow_network: bool = True,
) -> int:
    config, inputs, quality = build_real_inputs(
        config_path, project_root=PROJECT_ROOT, allow_network=allow_network
    )
    result = execute_validation(config, inputs, quality, output_dir)
    failed = [name for name, passed in result.quality["gates"].items() if not passed]
    print(
        json.dumps(
            {
                "output_dir": str(Path(output_dir).resolve()),
                "failed_gates": failed,
                "quality": result.quality,
            },
            indent=2,
            default=lambda value: value.item() if isinstance(value, np.generic) else value,
        )
    )
    return 1 if failed else 0


def main() -> int:
    return run_validation(
        PROJECT_ROOT / "configs" / "research_platform_example.yaml",
        PROJECT_ROOT / "outputs" / "research_platform_validation",
        allow_network=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
