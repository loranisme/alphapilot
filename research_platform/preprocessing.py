"""Date-local factor preprocessing and exposure neutralization."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class NeutralizationResult:
    values: pd.Series
    diagnostics: dict


@dataclass(frozen=True)
class PanelNeutralizationResult:
    values: pd.DataFrame
    diagnostics: pd.DataFrame


def winsorize_panel(
    panel: pd.DataFrame,
    lower_q: float = 0.01,
    upper_q: float = 0.99,
) -> pd.DataFrame:
    if not 0 <= lower_q < upper_q <= 1:
        raise ValueError("winsorization quantiles must satisfy 0 <= lower < upper <= 1")
    lower = panel.quantile(lower_q, axis=1)
    upper = panel.quantile(upper_q, axis=1)
    return panel.copy().clip(lower=lower, upper=upper, axis=0)


def standardize_panel(panel: pd.DataFrame) -> pd.DataFrame:
    means = panel.mean(axis=1)
    stds = panel.std(axis=1, ddof=0).replace(0.0, np.nan)
    return panel.sub(means, axis=0).div(stds, axis=0)


def neutralize_cross_section(
    signal: pd.Series,
    industry: pd.Series,
    market_cap: pd.Series | None = None,
    min_names: int = 10,
) -> NeutralizationResult:
    if min_names < 2:
        raise ValueError("min_names must be at least 2")
    valid_signal_count = int(pd.to_numeric(signal, errors="coerce").notna().sum())
    frame = pd.concat(
        {
            "signal": pd.to_numeric(signal, errors="coerce"),
            "industry": industry,
        },
        axis=1,
    )
    if market_cap is not None:
        numeric_cap = pd.to_numeric(market_cap, errors="coerce")
        frame["log_size"] = np.log(numeric_cap.where(numeric_cap > 0))
    frame = frame.dropna()
    coverage = len(frame) / valid_signal_count if valid_signal_count else 0.0
    if len(frame) < min_names:
        return NeutralizationResult(
            pd.Series(np.nan, index=signal.index, dtype=float),
            {"valid": False, "reason": "insufficient_names", "coverage": coverage},
        )

    dummies = pd.get_dummies(frame["industry"], drop_first=True, dtype=float)
    design_parts = [pd.Series(1.0, index=frame.index, name="intercept"), dummies]
    if "log_size" in frame:
        design_parts.append(frame[["log_size"]])
    design = pd.concat(design_parts, axis=1).astype(float)
    matrix = design.to_numpy()
    beta, *_ = np.linalg.lstsq(matrix, frame["signal"].to_numpy(), rcond=None)
    residual = frame["signal"] - matrix @ beta
    residual_std = float(residual.std(ddof=0))
    if not np.isfinite(residual_std) or residual_std <= 1e-12:
        return NeutralizationResult(
            pd.Series(np.nan, index=signal.index, dtype=float),
            {"valid": False, "reason": "constant_residual", "coverage": coverage},
        )
    residual = (residual - residual.mean()) / residual_std
    output = pd.Series(np.nan, index=signal.index, dtype=float)
    output.loc[residual.index] = residual
    return NeutralizationResult(
        output,
        {
            "valid": True,
            "reason": None,
            "coverage": coverage,
            "n_names": len(frame),
            "rank": int(np.linalg.matrix_rank(matrix)),
        },
    )


def neutralize_panel(
    signal: pd.DataFrame,
    industry: pd.DataFrame,
    market_cap: pd.DataFrame | None = None,
    min_names: int = 10,
) -> PanelNeutralizationResult:
    if not signal.index.equals(industry.index) or not signal.columns.equals(industry.columns):
        industry = industry.reindex(index=signal.index, columns=signal.columns)
    if market_cap is not None:
        market_cap = market_cap.reindex(index=signal.index, columns=signal.columns)

    values = pd.DataFrame(np.nan, index=signal.index, columns=signal.columns, dtype=float)
    diagnostic_rows = []
    for date in signal.index:
        result = neutralize_cross_section(
            signal.loc[date],
            industry.loc[date],
            None if market_cap is None else market_cap.loc[date],
            min_names=min_names,
        )
        values.loc[date] = result.values
        diagnostic_rows.append({"date": date, **result.diagnostics})
    diagnostics = pd.DataFrame(diagnostic_rows).set_index("date")
    diagnostics.index.name = None
    return PanelNeutralizationResult(values=values, diagnostics=diagnostics)
