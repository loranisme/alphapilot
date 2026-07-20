"""Fold-specific out-of-sample composite construction and execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .evaluation import evaluate_ic, generate_purged_folds
from .portfolio import PortfolioResult, build_buffered_targets, simulate_portfolio
from .preprocessing import build_industry_score_variants_panel, standardize_panel
from .selection import SelectionResult, select_stable_factors


@dataclass(frozen=True)
class OOSConfig:
    horizon: int = 5
    min_train: int = 300
    test_size: int = 50
    step: int = 50
    purge: int = 5
    soft_strength: float = 0.5
    rebalance_interval: int = 5
    entry_quantile: float = 0.20
    exit_quantile: float = 0.30
    factor_weight_cap: float = 0.20
    name_weight_cap: float = 0.02
    cost_bps: float = 10.0
    min_names: int = 30
    min_abs_ic: float = 0.005
    cost_stress_bps: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0)
    max_industry_exposure: float = 0.08
    min_soft_ic_retention: float = 0.80
    min_cost_reduction: float = 0.40

    def __post_init__(self):
        if self.purge < self.horizon:
            raise ValueError("purge must be at least horizon")
        if self.rebalance_interval != self.horizon:
            raise ValueError("rebalance_interval must equal prediction horizon")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "OOSConfig":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise TypeError("OOS config must be a YAML mapping")
        if "cost_stress_bps" in payload:
            payload["cost_stress_bps"] = tuple(payload["cost_stress_bps"])
        return cls(**payload)


@dataclass(frozen=True)
class OOSFoldState:
    number: int
    train_dates: pd.Index
    test_dates: pd.Index
    selection: SelectionResult


@dataclass(frozen=True)
class OOSExperimentResult:
    folds: tuple[OOSFoldState, ...]
    scores: dict[str, pd.DataFrame]
    portfolio_targets: dict[str, pd.DataFrame]
    portfolio_diagnostics: dict[str, pd.DataFrame]
    portfolios: dict[str, PortfolioResult]
    diagnostics: dict[str, pd.DataFrame]
    quality: dict


def compose_oos_score(
    factors: dict[str, pd.DataFrame],
    dates: pd.Index,
    selection: SelectionResult,
) -> pd.DataFrame:
    """Compose a frozen-weight score, renormalizing only for missing factors."""
    columns = next(iter(factors.values())).columns
    score = pd.DataFrame(0.0, index=dates, columns=columns)
    valid_weight = pd.DataFrame(0.0, index=dates, columns=columns)
    for factor, weight in selection.weights.items():
        panel = factors[factor].reindex(index=dates, columns=columns)
        aligned = standardize_panel(panel) * selection.directions[factor]
        score = score.add(aligned.fillna(0.0) * weight, fill_value=0.0)
        valid_weight = valid_weight.add(
            aligned.notna().astype(float) * weight, fill_value=0.0
        )
    return score.div(valid_weight.replace(0.0, np.nan))


def _validate_inputs(
    factors: dict[str, pd.DataFrame], close: pd.DataFrame, industry: pd.DataFrame
) -> tuple[pd.DatetimeIndex, pd.Index]:
    if not factors:
        raise ValueError("at least one factor is required")
    dates = close.index
    columns = close.columns
    if not isinstance(dates, pd.DatetimeIndex) or not dates.is_monotonic_increasing:
        raise ValueError("close dates must be a sorted DatetimeIndex")
    for name, panel in factors.items():
        if not panel.index.equals(dates) or not panel.columns.equals(columns):
            raise ValueError(f"factor {name!r} must align with close")
    if not industry.reindex(index=dates, columns=columns).shape == close.shape:
        raise ValueError("industry must align with close")
    return dates, columns


def _label_overlap_count(
    all_dates: pd.Index, train_dates: pd.Index, test_dates: pd.Index, horizon: int
) -> int:
    if not len(train_dates) or not len(test_dates):
        return 0
    positions = pd.Series(np.arange(len(all_dates)), index=all_dates)
    test_start = int(positions.loc[test_dates[0]])
    train_positions = positions.loc[train_dates].to_numpy()
    return int(np.sum(train_positions + horizon >= test_start))


def run_oos_experiment(
    factors: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    industry: pd.DataFrame,
    config: OOSConfig | None = None,
) -> OOSExperimentResult:
    """Run walk-forward selection and three comparable OOS portfolio paths."""
    config = OOSConfig() if config is None else config
    dates, columns = _validate_inputs(factors, close, industry)
    forward = close.shift(-config.horizon).div(close).sub(1.0)
    asset_returns = close.pct_change(fill_method=None)
    fold_slices = generate_purged_folds(
        dates,
        min_train=config.min_train,
        test_size=config.test_size,
        step=config.step,
        horizon=config.horizon,
        purge=config.purge,
    )
    if not fold_slices:
        raise ValueError("not enough dates to create an OOS fold")

    folds = []
    path_parts = {path: [] for path in ("raw", "soft", "strict")}
    variant_diagnostics = []
    overlap_count = 0
    for number, (train_dates, test_dates) in enumerate(fold_slices):
        ic_columns = {}
        for name, panel in factors.items():
            ic_columns[name] = evaluate_ic(
                panel.loc[train_dates],
                forward.loc[train_dates],
                min_names=config.min_names,
            )
        ic_table = pd.DataFrame(ic_columns).reindex(train_dates)
        selection = select_stable_factors(
            ic_table,
            min_abs_ic=config.min_abs_ic,
            max_weight=config.factor_weight_cap,
        )
        folds.append(OOSFoldState(number, train_dates, test_dates, selection))
        overlap_count += _label_overlap_count(
            dates, train_dates, test_dates, config.horizon
        )
        raw_score = compose_oos_score(factors, test_dates, selection)
        variants = build_industry_score_variants_panel(
            raw_score,
            industry.reindex(index=test_dates, columns=columns),
            soft_strength=config.soft_strength,
            min_names=config.min_names,
        )
        for path in path_parts:
            path_parts[path].append(getattr(variants, path))
        diagnostic = variants.diagnostics.copy()
        diagnostic.insert(0, "fold", number)
        variant_diagnostics.append(diagnostic)

    scores = {
        path: pd.concat(parts).sort_index() for path, parts in path_parts.items()
    }
    for path, panel in scores.items():
        if not panel.index.is_unique:
            raise ValueError(f"duplicate OOS dates in {path} path")

    targets = {}
    target_diagnostics = {}
    portfolios = {}
    oos_returns = asset_returns.reindex(index=scores["raw"].index, columns=columns)
    for path, panel in scores.items():
        buffered = build_buffered_targets(
            panel,
            rebalance_interval=config.rebalance_interval,
            entry_quantile=config.entry_quantile,
            exit_quantile=config.exit_quantile,
            max_weight=config.name_weight_cap,
        )
        targets[path] = buffered.targets
        target_diagnostics[path] = buffered.diagnostics
        portfolios[path] = simulate_portfolio(
            buffered.targets, oos_returns, cost_bps=config.cost_bps
        )

    diagnostics = {
        "score_variants": pd.concat(variant_diagnostics).sort_index(),
        "fold_selection": pd.concat(
            [
                fold.selection.diagnostics.assign(fold=fold.number)
                for fold in folds
            ],
            names=["fold_row"],
        ),
    }
    return OOSExperimentResult(
        folds=tuple(folds),
        scores=scores,
        portfolio_targets=targets,
        portfolio_diagnostics=target_diagnostics,
        portfolios=portfolios,
        diagnostics=diagnostics,
        quality={"label_overlap_count": overlap_count},
    )
