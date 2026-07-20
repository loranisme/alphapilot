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


@dataclass(frozen=True)
class IndustryScoreVariants:
    raw: pd.Series
    soft: pd.Series
    strict: pd.Series
    fitted: pd.Series
    diagnostics: dict


@dataclass(frozen=True)
class IndustryScoreVariantsPanel:
    raw: pd.DataFrame
    soft: pd.DataFrame
    strict: pd.DataFrame
    fitted: pd.DataFrame
    diagnostics: pd.DataFrame


def standardize_series(values: pd.Series) -> pd.Series:
    """Cross-sectionally standardize finite values while preserving the index."""
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series(np.nan, index=values.index, dtype=float, name=values.name)
    valid = numeric.dropna()
    scale = float(valid.std(ddof=0))
    if len(valid) >= 2 and np.isfinite(scale) and scale > 1e-12:
        result.loc[valid.index] = (valid - valid.mean()) / scale
    return result


def _invalid_score_variants(index: pd.Index, reason: str, coverage: float):
    invalid = pd.Series(np.nan, index=index, dtype=float)
    return IndustryScoreVariants(
        raw=invalid.copy(),
        soft=invalid.copy(),
        strict=invalid.copy(),
        fitted=invalid.copy(),
        diagnostics={"valid": False, "reason": reason, "coverage": coverage},
    )


def build_industry_score_variants(
    score: pd.Series,
    industry: pd.Series,
    soft_strength: float = 0.5,
    min_names: int = 30,
) -> IndustryScoreVariants:
    """Return raw, partially neutralized, and fully neutralized score variants."""
    if not 0 <= soft_strength <= 1:
        raise ValueError("soft_strength must be within [0, 1]")
    if min_names < 2:
        raise ValueError("min_names must be at least 2")
    numeric = pd.to_numeric(score, errors="coerce")
    valid_signal_count = int(numeric.notna().sum())
    aligned = pd.concat({"score": numeric, "industry": industry}, axis=1).dropna()
    coverage = len(aligned) / valid_signal_count if valid_signal_count else 0.0
    if len(aligned) < min_names:
        return _invalid_score_variants(score.index, "insufficient_names", coverage)

    design = pd.get_dummies(aligned["industry"], drop_first=True, dtype=float)
    design.insert(0, "intercept", 1.0)
    matrix = design.to_numpy()
    beta, *_ = np.linalg.lstsq(matrix, aligned["score"].to_numpy(), rcond=None)
    fitted = pd.Series(np.nan, index=score.index, dtype=float)
    fitted.loc[aligned.index] = matrix @ beta
    raw = standardize_series(numeric.where(industry.notna()))
    soft = standardize_series(numeric - soft_strength * fitted)
    strict = standardize_series(numeric - fitted)
    if strict.notna().sum() < 2:
        return _invalid_score_variants(score.index, "constant_residual", coverage)
    return IndustryScoreVariants(
        raw=raw,
        soft=soft,
        strict=strict,
        fitted=fitted,
        diagnostics={
            "valid": True,
            "reason": None,
            "coverage": coverage,
            "n_names": len(aligned),
            "rank": int(np.linalg.matrix_rank(matrix)),
        },
    )


def build_industry_score_variants_panel(
    score: pd.DataFrame,
    industry: pd.DataFrame,
    soft_strength: float = 0.5,
    min_names: int = 30,
) -> IndustryScoreVariantsPanel:
    """Apply industry decomposition independently to every signal date."""
    aligned_industry = industry.reindex(index=score.index, columns=score.columns)
    outputs = {
        key: pd.DataFrame(np.nan, index=score.index, columns=score.columns, dtype=float)
        for key in ("raw", "soft", "strict", "fitted")
    }
    rows = []
    for date in score.index:
        variants = build_industry_score_variants(
            score.loc[date],
            aligned_industry.loc[date],
            soft_strength=soft_strength,
            min_names=min_names,
        )
        for key in outputs:
            outputs[key].loc[date] = getattr(variants, key)
        rows.append({"date": date, **variants.diagnostics})
    diagnostics = pd.DataFrame(rows).set_index("date")
    diagnostics.index.name = None
    return IndustryScoreVariantsPanel(diagnostics=diagnostics, **outputs)


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
