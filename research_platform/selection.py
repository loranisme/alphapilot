"""In-sample factor stability, direction alignment, and constrained weighting."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SelectionResult:
    selected: tuple[str, ...]
    directions: dict[str, int]
    weights: dict[str, float]
    diagnostics: pd.DataFrame


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
