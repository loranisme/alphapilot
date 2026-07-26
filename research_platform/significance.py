"""Block-bootstrap significance testing for factor IC.

Pure statistics: this module never loads data and does not know what a "factor"
is beyond a labeled per-date IC series. Callers assemble an IC panel (rows are
dates, columns are ``(factor, horizon)`` pairs) and this module answers, with a
grid-wide multiple-testing correction, whether any cell's mean IC is
distinguishable from a resampled no-signal null.

The correction is a **studentized** bootstrap max-statistic (Westfall-Young
max-T). Each resample records the largest ``|studentized mean|`` over the entire
grid, so the null already absorbs the correlation between horizons and between
factors, and comparing an observed cell to that grid-wide-max null is the
family-wise correction — without over-penalising the way Bonferroni over
nominally-independent hypotheses would.

Why studentize. Raw ``|mean IC|`` is the wrong scale for a max across cells with
very different effective sample sizes: a 5-day-overlap statistic on 22 effective
observations (horizon 42) swings far more than one on 975 (horizon 1), so a
raw-effect-size max is dominated by long horizons and can rank a t=1.6 cell above
a t=3.5 cell. Dividing each cell by its own block-bootstrap standard error puts
every cell on a common sampling scale before the max.

Resampling level and null. We resample the time axis of the already-computed
daily IC series (circular block bootstrap), not the raw factor-return alignment —
this avoids ``n_boot x n_cells`` cross-sectional Spearman recomputations. A plain
block bootstrap preserves each series' mean, so we **demean each column** before
resampling to impose H0 (mean IC = 0). The bootstrap of the demeaned series is
then the sampling distribution of the mean under H0, with the column's
autocorrelation and variance structure retained. All grid cells share the same
block-index draw within a resample, preserving cross-cell dependence.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BootstrapNull:
    """Studentized grid-wide null from one block-bootstrap pass.

    ``columns`` orders the grid cells; ``se`` is each cell's block-bootstrap
    standard error of the mean under H0; ``null_max_z`` is the family-wise null
    distribution of ``max |studentized mean|`` across the grid.
    """

    columns: pd.Index
    se: np.ndarray
    null_max_z: np.ndarray
    block: int
    n_boot: int
    seed: int


def effective_sample_size(n_dates: int, horizon: int) -> float:
    """Overlap-adjusted sample size for an ``horizon``-day forward return.

    Overlapping forward returns are autocorrelated, so ``n_dates`` overstates the
    independent information. ``n_dates / horizon`` is the standard IC haircut.
    """
    if n_dates < 0:
        raise ValueError("n_dates must be non-negative")
    if horizon < 1:
        raise ValueError("horizon must be positive")
    return n_dates / horizon


def _circular_block_indices(
    n: int, block: int, rng: np.random.Generator
) -> np.ndarray:
    """Row indices for one circular block-bootstrap resample of length ``n``."""
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=n_blocks)
    offsets = np.arange(block)
    idx = (starts[:, None] + offsets[None, :]).reshape(-1) % n
    return idx[:n]


def block_bootstrap_null(
    ic_panel: pd.DataFrame,
    block: int = 10,
    n_boot: int = 2000,
    seed: int = 42,
) -> BootstrapNull:
    """Studentized family-wise null via a circular block bootstrap.

    Parameters
    ----------
    ic_panel:
        Rows indexed by date, one column per ``(factor, horizon)`` grid cell.
        NaNs (dates without a valid cross-section) are ignored per column.
    block:
        Block length in rows; should exceed half the largest horizon to retain
        overlap autocorrelation and volatility clustering.
    n_boot:
        Number of bootstrap resamples.
    seed:
        Fixed for reproducibility.
    """
    if block < 1:
        raise ValueError("block must be positive")
    if n_boot < 1:
        raise ValueError("n_boot must be positive")
    values = ic_panel.to_numpy(dtype=float)
    n, k = values.shape
    if n == 0:
        raise ValueError("ic_panel must have at least one date")
    valid = ~np.isnan(values)
    with np.errstate(invalid="ignore"):
        col_means = np.nanmean(np.where(valid, values, np.nan), axis=0)
    # Demean per column to impose H0 (mean IC = 0); NaNs stay masked.
    centered = np.where(valid, values - col_means, 0.0)
    valid_f = valid.astype(float)

    rng = np.random.default_rng(seed)
    boot_means = np.empty((n_boot, k), dtype=float)
    for b in range(n_boot):
        idx = _circular_block_indices(n, block, rng)
        summed = centered[idx].sum(axis=0)
        counts = valid_f[idx].sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            boot_means[b] = np.where(counts > 0, summed / counts, np.nan)

    # Bootstrap SE of the H0 mean, per cell. Cells with no variability get inf so
    # their studentized statistic is 0 (indistinguishable from the null).
    se = np.nanstd(boot_means, axis=0, ddof=1)
    safe_se = np.where(se > 0, se, np.inf)
    z = np.abs(boot_means) / safe_se
    null_max_z = np.nanmax(z, axis=1)
    return BootstrapNull(
        columns=ic_panel.columns,
        se=se,
        null_max_z=null_max_z,
        block=block,
        n_boot=n_boot,
        seed=seed,
    )


def _empirical_p_value(observed_z: float, null_max_z: np.ndarray) -> float:
    """Add-one empirical p-value: P(grid-wide null max|z| >= |observed z|)."""
    if not np.isfinite(observed_z):
        return 1.0
    exceed = int(np.sum(null_max_z >= abs(observed_z)))
    return (1 + exceed) / (1 + len(null_max_z))


def factor_evidence(
    ic_panel: pd.DataFrame,
    boot: BootstrapNull,
    p_threshold: float = 0.05,
) -> pd.DataFrame:
    """Per-cell evidence table with studentized bootstrap p-values and verdicts.

    Columns: ``factor, horizon, n_obs, ic_bar, se_boot, z_stat, n_eff, t_naive,
    p_emp, verdict, flag_10pct``. The verdict comes from ``p_emp`` (the
    studentized family-wise bootstrap p-value); ``t_naive`` is an analytic
    cross-check that should now track ``z_stat`` in rank order.
    """
    if not 0 < p_threshold < 1:
        raise ValueError("p_threshold must be within (0, 1)")
    if not ic_panel.columns.equals(boot.columns):
        raise ValueError("ic_panel columns must match the bootstrap null columns")
    se_by_column = dict(zip(boot.columns, boot.se))
    rows: list[dict[str, object]] = []
    for column in ic_panel.columns:
        factor, horizon = column
        horizon = int(horizon)
        series = ic_panel[column].dropna()
        n_obs = int(len(series))
        ic_bar = float(series.mean()) if n_obs else np.nan
        se_boot = float(se_by_column[column])
        if not np.isfinite(ic_bar):
            z_stat = np.nan
        elif se_boot > 0:
            z_stat = ic_bar / se_boot
        elif ic_bar != 0:
            # zero sampling variance with a nonzero mean: certain signal.
            z_stat = np.inf
        else:
            z_stat = 0.0
        n_eff = effective_sample_size(n_obs, horizon)
        std = float(series.std(ddof=1)) if n_obs > 1 else np.nan
        t_naive = (
            ic_bar / std * np.sqrt(n_eff)
            if np.isfinite(ic_bar) and np.isfinite(std) and std > 0 and n_eff > 0
            else np.nan
        )
        p_emp = _empirical_p_value(z_stat, boot.null_max_z)
        rows.append(
            {
                "factor": factor,
                "horizon": horizon,
                "n_obs": n_obs,
                "ic_bar": ic_bar,
                "se_boot": se_boot,
                "z_stat": z_stat,
                "n_eff": n_eff,
                "t_naive": t_naive,
                "p_emp": p_emp,
                "verdict": "PASS" if p_emp < p_threshold else "REJECT",
            }
        )
    evidence = pd.DataFrame(rows)
    evidence["flag_10pct"] = evidence["p_emp"] < 0.10
    return evidence


def default_block_for_horizon(horizon: int) -> int:
    """Block length that captures overlap dependence out to the horizon.

    Overlapping h-day forward returns make the daily IC series autocorrelated to
    roughly lag h, so the block must be at least h — a block shorter than the
    horizon underestimates the standard error and inflates significance. We use
    ``2h`` with a floor of 10 to also retain short-horizon volatility clustering.
    """
    return max(10, 2 * int(horizon))


def evaluate_factor_grid(
    ic_panel: pd.DataFrame,
    block_for_horizon=default_block_for_horizon,
    n_boot: int = 2000,
    seed: int = 42,
    p_threshold: float = 0.05,
) -> pd.DataFrame:
    """Family-wise factor evidence over a ``(factor, horizon)`` IC grid.

    A single global block cannot serve every horizon (short horizons need a small
    block, long horizons need block >= horizon), so each horizon is bootstrapped
    as its own family with a horizon-matched block. Within a horizon the p-value
    is family-wise across factors (studentized max-T); across the ``H`` distinct
    horizons we apply a Bonferroni factor for the horizon search. The headline
    ``verdict`` uses the horizon-corrected ``p_global``.

    Added columns beyond :func:`factor_evidence`: ``block, crit_z_5pct,
    p_horizon, p_global, verdict_horizon``. ``verdict``/``flag_10pct`` are
    recomputed against ``p_global``.
    """
    horizons = sorted({int(h) for _, h in ic_panel.columns})
    n_horizons = len(horizons)
    parts: list[pd.DataFrame] = []
    for horizon in horizons:
        columns = [c for c in ic_panel.columns if int(c[1]) == horizon]
        sub = ic_panel.loc[:, columns]
        block = int(block_for_horizon(horizon))
        boot = block_bootstrap_null(sub, block=block, n_boot=n_boot, seed=seed)
        evidence = factor_evidence(sub, boot, p_threshold=p_threshold)
        evidence["block"] = block
        evidence["crit_z_5pct"] = float(np.quantile(boot.null_max_z, 0.95))
        evidence["p_horizon"] = evidence["p_emp"]
        evidence["p_global"] = np.minimum(1.0, evidence["p_emp"] * n_horizons)
        evidence["verdict_horizon"] = np.where(
            evidence["p_horizon"] < p_threshold, "PASS", "REJECT"
        )
        evidence["verdict"] = np.where(
            evidence["p_global"] < p_threshold, "PASS", "REJECT"
        )
        evidence["flag_10pct"] = evidence["p_global"] < 0.10
        parts.append(evidence)
    grid = pd.concat(parts, ignore_index=True)
    return grid.sort_values(["p_global", "z_stat"], ascending=[True, False]).reset_index(
        drop=True
    )
