"""In-sample factor stability, direction alignment, and constrained weighting."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .correlation import (
    CorrelationEstimate,
    connected_correlation_clusters,
    factor_rank_turnover,
    factor_value_correlation,
    ic_correlation,
)


@dataclass(frozen=True)
class SelectionResult:
    selected: tuple[str, ...]
    directions: dict[str, int]
    weights: dict[str, float]
    diagnostics: pd.DataFrame


@dataclass(frozen=True)
class CorrelationSelectionResult(SelectionResult):
    factor_value_correlation: CorrelationEstimate
    ic_correlation: CorrelationEstimate
    clusters: pd.DataFrame
    valid: bool


def capped_simplex_weights(quality: pd.Series, cap: float = 0.20) -> dict[str, float]:
    """Normalize non-negative quality scores subject to an individual cap."""
    clean = pd.to_numeric(quality, errors="coerce").fillna(0.0).clip(lower=0.0)
    if clean.empty:
        return {}
    if not 0 < cap <= 1:
        raise ValueError("cap must be within (0, 1]")
    if len(clean) * cap < 1.0 - 1e-12:
        raise ValueError("factor weight cap is infeasible for selected factors")
    if float(clean.sum()) <= 0:
        clean[:] = 1.0

    weights = pd.Series(0.0, index=clean.index)
    remaining = list(clean.index)
    remaining_mass = 1.0
    while remaining:
        scores = clean.loc[remaining]
        proposal = remaining_mass * scores / scores.sum()
        over = proposal[proposal > cap + 1e-12]
        if over.empty:
            weights.loc[remaining] = proposal
            break
        weights.loc[over.index] = cap
        remaining_mass -= cap * len(over)
        remaining = [name for name in remaining if name not in over.index]
        if remaining and float(clean.loc[remaining].sum()) <= 0:
            clean.loc[remaining] = 1.0
    weights /= weights.sum()
    return weights.astype(float).to_dict()


def select_stable_factors(
    ic_series: pd.DataFrame,
    min_abs_ic: float = 0.005,
    n_blocks: int = 4,
    min_agreeing_blocks: int = 3,
    max_weight: float = 0.20,
) -> SelectionResult:
    """Select factors using only blockwise in-sample IC evidence."""
    if ic_series.empty:
        raise ValueError("ic_series must not be empty")
    if n_blocks < 2 or not 1 <= min_agreeing_blocks <= n_blocks:
        raise ValueError("invalid block stability requirements")

    ordered = ic_series.sort_index().apply(pd.to_numeric, errors="coerce")
    blocks = [ordered.iloc[positions] for positions in np.array_split(np.arange(len(ordered)), n_blocks)]
    rows: list[dict[str, object]] = []
    for factor in ordered.columns:
        full_ic = float(ordered[factor].mean())
        direction = 1 if full_ic >= 0 else -1
        block_means = [
            float(block[factor].mean())
            for block in blocks
            if block[factor].notna().any()
        ]
        agreeing = sum(np.sign(value) == direction for value in block_means)
        recent_ic = float(ordered[factor].iloc[len(ordered) // 2 :].mean())
        recent_agrees = bool(np.isfinite(recent_ic) and np.sign(recent_ic) == direction)
        robust_ic = float(np.median(block_means)) if block_means else np.nan
        eligible = bool(
            len(block_means) == n_blocks
            and agreeing >= min_agreeing_blocks
            and recent_agrees
            and np.isfinite(robust_ic)
            and abs(robust_ic) >= min_abs_ic
        )
        rows.append(
            {
                "factor": factor,
                "full_ic": full_ic,
                "direction": direction,
                "robust_ic": robust_ic,
                "agreeing_blocks": agreeing,
                "recent_ic": recent_ic,
                "recent_agrees": recent_agrees,
                "eligible": eligible,
            }
        )

    diagnostics = pd.DataFrame(rows).set_index("factor")
    selected = diagnostics.index[diagnostics["eligible"]].tolist()
    quality = (
        diagnostics.loc[selected, "robust_ic"].abs()
        * diagnostics.loc[selected, "agreeing_blocks"]
        / n_blocks
    )
    weights = capped_simplex_weights(quality, cap=max_weight)
    directions = diagnostics.loc[selected, "direction"].astype(int).to_dict()
    return SelectionResult(tuple(selected), directions, weights, diagnostics)


def _empty_correlation(names: list[str] | None = None) -> CorrelationEstimate:
    labels = [] if names is None else names
    values = pd.DataFrame(index=labels, columns=labels, dtype=float)
    verified = pd.DataFrame(False, index=labels, columns=labels, dtype=bool)
    counts = pd.DataFrame(0, index=labels, columns=labels, dtype=int)
    return CorrelationEstimate(values=values, verified=verified, counts=counts)


def select_correlation_aware_factors(
    factors: dict[str, pd.DataFrame],
    ic_series: pd.DataFrame,
    train_dates: pd.Index,
    min_abs_ic: float = 0.005,
    min_coverage: float = 0.80,
    min_names: int = 30,
    n_blocks: int = 4,
    min_agreeing_blocks: int = 3,
    min_block_observations: int = 20,
    hard_threshold: float = 0.75,
    min_pair_dates: int = 60,
    ic_shrinkage: float = 0.5,
    ic_soft_threshold: float = 0.25,
    rebalance_interval: int = 5,
    min_factors: int = 5,
    max_factors: int = 6,
    max_weight: float = 0.20,
) -> CorrelationSelectionResult:
    """Apply fixed IS stability, hard clustering, and soft IC penalties."""
    if not factors:
        raise ValueError("at least one factor is required")
    if not 0 < min_coverage <= 1:
        raise ValueError("min_coverage must be within (0, 1]")
    if min_factors < 1 or max_factors < min_factors:
        raise ValueError("invalid factor-count limits")
    if min_factors * max_weight < 1.0 - 1e-12:
        raise ValueError("minimum factor count is infeasible for the weight cap")

    train_dates = pd.Index(train_dates).sort_values()
    ordered_ic = (
        ic_series.reindex(index=train_dates, columns=sorted(factors))
        .apply(pd.to_numeric, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
    )
    block_positions = np.array_split(np.arange(len(train_dates)), n_blocks)
    rows: list[dict[str, object]] = []
    for name in sorted(factors):
        panel = (
            factors[name]
            .reindex(index=train_dates)
            .apply(pd.to_numeric, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
        )
        cross_section_valid = panel.notna().sum(axis=1).ge(min_names) & panel.nunique(
            axis=1, dropna=True
        ).gt(1)
        coverage = float(cross_section_valid.mean()) if len(panel) else 0.0
        missingness = float(panel.isna().sum().sum() / panel.size) if panel.size else 1.0
        factor_ic = ordered_ic[name]
        full_ic = float(factor_ic.mean())
        direction = 1 if not np.isfinite(full_ic) or full_ic >= 0 else -1
        block_counts = [int(factor_ic.iloc[positions].notna().sum()) for positions in block_positions]
        block_means = [
            float(factor_ic.iloc[positions].mean()) if count else np.nan
            for positions, count in zip(block_positions, block_counts)
        ]
        blocks_sufficient = all(count >= min_block_observations for count in block_counts)
        agreeing = sum(
            np.isfinite(value) and np.sign(value) == direction for value in block_means
        )
        recent_ic = float(factor_ic.iloc[len(factor_ic) // 2 :].mean())
        recent_agrees = bool(
            np.isfinite(recent_ic) and np.sign(recent_ic) == direction
        )
        robust_ic = (
            float(np.median(block_means))
            if all(np.isfinite(block_means))
            else np.nan
        )
        reasons = []
        if coverage < min_coverage:
            reasons.append("coverage_below_80pct")
        if not blocks_sufficient:
            reasons.append("insufficient_ic_block_observations")
        if agreeing < min_agreeing_blocks:
            reasons.append("block_direction_instability")
        if not recent_agrees:
            reasons.append("recent_direction_mismatch")
        if not np.isfinite(robust_ic) or abs(robust_ic) < min_abs_ic:
            reasons.append("robust_ic_below_threshold")
        eligible = not reasons
        base_quality = (
            abs(robust_ic) * agreeing / n_blocks * float(recent_agrees)
            if np.isfinite(robust_ic)
            else 0.0
        )
        rows.append(
            {
                "factor": name,
                "coverage": coverage,
                "missingness": missingness,
                "full_ic": full_ic,
                "direction": direction,
                "robust_ic": robust_ic,
                "agreeing_blocks": agreeing,
                "block_observation_counts": "|".join(map(str, block_counts)),
                "recent_ic": recent_ic,
                "recent_agrees": recent_agrees,
                "eligible": eligible,
                "ineligible_reason": "|".join(reasons),
                "base_quality": base_quality,
                "rank_turnover": np.nan,
                "cost_adjusted_quality": np.nan,
                "redundancy_penalty": np.nan,
                "adjusted_quality": np.nan,
            }
        )

    diagnostics = pd.DataFrame(rows).set_index("factor")
    eligible = diagnostics.index[diagnostics["eligible"]].tolist()
    if not eligible:
        return CorrelationSelectionResult(
            selected=(),
            directions={},
            weights={},
            diagnostics=diagnostics,
            factor_value_correlation=_empty_correlation(),
            ic_correlation=_empty_correlation(),
            clusters=pd.DataFrame(
                columns=["factor", "cluster", "cluster_size", "members", "representative", "selected"]
            ),
            valid=False,
        )

    eligible_directions = diagnostics.loc[eligible, "direction"].astype(int).to_dict()
    value_estimate = factor_value_correlation(
        {name: factors[name] for name in eligible},
        train_dates,
        eligible_directions,
        min_names=min_names,
        min_dates=min_pair_dates,
    )
    clusters = connected_correlation_clusters(
        value_estimate.values,
        value_estimate.verified,
        threshold=hard_threshold,
    )

    representatives = []
    for _, group in clusters.groupby("cluster", sort=True):
        candidates = group["factor"].tolist()
        representative = sorted(
            candidates,
            key=lambda name: (
                -float(diagnostics.loc[name, "base_quality"]),
                float(diagnostics.loc[name, "missingness"]),
                name,
            ),
        )[0]
        representatives.append(representative)

    for name in representatives:
        turnover = factor_rank_turnover(
            factors[name] * eligible_directions[name],
            train_dates,
            rebalance_interval=rebalance_interval,
            min_names=min_names,
        )
        diagnostics.loc[name, "rank_turnover"] = turnover
        diagnostics.loc[name, "cost_adjusted_quality"] = (
            float(diagnostics.loc[name, "base_quality"]) / (1.0 + turnover)
            if np.isfinite(turnover)
            else 0.0
        )

    ic_estimate = ic_correlation(
        ordered_ic.reindex(columns=representatives),
        {name: eligible_directions[name] for name in representatives},
        shrinkage=ic_shrinkage,
    )
    for name in representatives:
        row = ic_estimate.values.loc[name].drop(index=name, errors="ignore").abs()
        redundancy_penalty = 1.0 + float(
            (row.subtract(ic_soft_threshold).clip(lower=0.0)).fillna(0.0).sum()
        )
        diagnostics.loc[name, "redundancy_penalty"] = redundancy_penalty
        diagnostics.loc[name, "adjusted_quality"] = (
            float(diagnostics.loc[name, "cost_adjusted_quality"])
            / redundancy_penalty
        )

    selected = sorted(
        representatives,
        key=lambda name: (
            -float(diagnostics.loc[name, "adjusted_quality"]),
            float(diagnostics.loc[name, "missingness"]),
            name,
        ),
    )[:max_factors]
    valid = len(selected) >= min_factors
    directions = (
        {name: eligible_directions[name] for name in selected} if valid else {}
    )
    quality = diagnostics.loc[selected, "adjusted_quality"] if valid else pd.Series(dtype=float)
    weights = capped_simplex_weights(quality, cap=max_weight) if valid else {}
    representatives_set = set(representatives)
    selected_set = set(selected)
    clusters = clusters.assign(
        representative=clusters["factor"].isin(representatives_set),
        selected=clusters["factor"].isin(selected_set),
    )
    return CorrelationSelectionResult(
        selected=tuple(selected),
        directions=directions,
        weights=weights,
        diagnostics=diagnostics,
        factor_value_correlation=value_estimate,
        ic_correlation=ic_estimate,
        clusters=clusters,
        valid=valid,
    )
