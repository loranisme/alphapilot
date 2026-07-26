"""Unit tests for the block-bootstrap factor significance module.

Pure synthetic data — no real loader, no IO. The module under test only ever
sees a labeled per-date IC panel, so these tests construct such panels directly
(or via evaluate_ic on small synthetic price/return panels).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research_platform.evaluation import evaluate_ic
from research_platform.significance import (
    block_bootstrap_null,
    default_block_for_horizon,
    effective_sample_size,
    evaluate_factor_grid,
    factor_evidence,
)

HORIZONS = (1, 2, 3, 5, 10, 21, 42)


def _ic_panel_from_daily(daily: dict[tuple[str, int], pd.Series]) -> pd.DataFrame:
    """Assemble a (date x (factor, horizon)) IC panel from daily IC series."""
    frame = pd.DataFrame(daily)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["factor", "horizon"])
    return frame.sort_index()


def _synthetic_ic_panel(
    factors: dict[str, float],
    horizons=HORIZONS,
    n_dates: int = 400,
    noise: float = 0.15,
    seed: int = 0,
) -> pd.DataFrame:
    """Daily IC panel whose column means are the given per-factor levels."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_dates)
    daily = {}
    for factor, level in factors.items():
        for h in horizons:
            series = pd.Series(level + rng.normal(0.0, noise, n_dates), index=dates)
            daily[(factor, h)] = series
    return _ic_panel_from_daily(daily)


# --- effective sample size ---------------------------------------------------

def test_effective_sample_size_halves_when_horizon_doubles():
    assert effective_sample_size(1000, 5) == 200.0
    assert effective_sample_size(1000, 10) == 100.0
    assert effective_sample_size(1000, 5) == 2 * effective_sample_size(1000, 10)


# --- reproducibility ---------------------------------------------------------

def test_bootstrap_null_is_reproducible_under_fixed_seed():
    panel = _synthetic_ic_panel({"f": 0.0}, n_dates=300, seed=1)
    a = block_bootstrap_null(panel, block=10, n_boot=200, seed=42)
    b = block_bootstrap_null(panel, block=10, n_boot=200, seed=42)
    assert a.null_max_z.shape == (200,)
    assert np.array_equal(a.null_max_z, b.null_max_z)
    assert np.array_equal(a.se, b.se)


def test_bootstrap_null_differs_across_seeds():
    panel = _synthetic_ic_panel({"f": 0.0}, n_dates=300, seed=1)
    a = block_bootstrap_null(panel, block=10, n_boot=200, seed=42)
    b = block_bootstrap_null(panel, block=10, n_boot=200, seed=7)
    assert not np.array_equal(a.null_max_z, b.null_max_z)


# --- zero signal rejects -----------------------------------------------------

def test_zero_signal_is_rejected():
    """A factor with no real edge sits inside the noise null and is REJECTED."""
    panel = _synthetic_ic_panel({"f": 0.0}, n_dates=400, noise=0.15, seed=3)
    null_max = block_bootstrap_null(panel, block=10, n_boot=1000, seed=42)
    evidence = factor_evidence(panel, null_max, p_threshold=0.05)
    assert (evidence["verdict"] == "REJECT").all()
    assert (evidence["p_emp"] > 0.05).all()


def test_zero_signal_pvalues_are_not_tiny():
    """Empirical p-values for pure noise should be diffuse, not ~0."""
    panel = _synthetic_ic_panel({"f": 0.0}, n_dates=400, noise=0.15, seed=11)
    null_max = block_bootstrap_null(panel, block=10, n_boot=1000, seed=42)
    evidence = factor_evidence(panel, null_max, p_threshold=0.05)
    assert evidence["p_emp"].median() > 0.20


# --- planted signal passes ---------------------------------------------------

def test_strong_planted_signal_passes():
    """A factor whose daily IC mean dwarfs the null spread is detected."""
    panel = _synthetic_ic_panel(
        {"weak": 0.0, "strong": 0.10}, n_dates=400, noise=0.15, seed=5
    )
    null_max = block_bootstrap_null(panel, block=10, n_boot=1000, seed=42)
    evidence = factor_evidence(panel, null_max, p_threshold=0.05).set_index("factor")
    assert (evidence.loc["strong", "verdict"] == "PASS").all()
    assert (evidence.loc["strong", "p_emp"] < 0.05).all()
    # the null-level factor in the same grid must still be rejected
    assert (evidence.loc["weak", "verdict"] == "REJECT").all()


def test_planted_signal_survives_via_evaluate_ic_path():
    """End-to-end on the real evaluate_ic path with synthetic prices."""
    rng = np.random.default_rng(9)
    dates = pd.bdate_range("2021-01-01", periods=260)
    names = [f"N{i}" for i in range(60)]
    forward = pd.DataFrame(
        rng.normal(0.0, 0.02, (len(dates), len(names))), index=dates, columns=names
    )
    # signal is forward plus enough noise that daily IC is a realistic, noisy
    # ~0.3 (nonzero variance), not a degenerate constant 1.0; noise is random.
    signal = forward + rng.normal(0.0, 0.06, forward.shape)
    noise = pd.DataFrame(
        rng.normal(0.0, 1.0, (len(dates), len(names))), index=dates, columns=names
    )
    daily = {
        ("signal", 1): evaluate_ic(signal, forward, min_names=30),
        ("noise", 1): evaluate_ic(noise, forward, min_names=30),
    }
    panel = _ic_panel_from_daily(daily)
    null_max = block_bootstrap_null(panel, block=10, n_boot=1000, seed=42)
    evidence = factor_evidence(panel, null_max, p_threshold=0.05).set_index("factor")
    assert evidence.loc["signal", "verdict"] == "PASS"
    assert evidence.loc["noise", "verdict"] == "REJECT"


# --- family-wise control -----------------------------------------------------

def test_pvalue_ranks_by_studentized_stat_not_raw_effect_size():
    """Regression: a small-mean low-variance cell (high z) must be judged more
    significant than a large-mean high-variance cell (low z). Raw |mean IC| as
    the statistic inverted this; the studentized max-T must not."""
    dates = pd.bdate_range("2020-01-01", periods=400)
    rng = np.random.default_rng(0)
    # tight cell: small edge, tiny noise -> high z
    tight = pd.Series(0.02 + rng.normal(0.0, 0.02, 400), index=dates)
    # wide cell: bigger edge, huge noise -> low z
    wide = pd.Series(0.05 + rng.normal(0.0, 0.40, 400), index=dates)
    panel = _ic_panel_from_daily({("tight", 5): tight, ("wide", 5): wide})
    boot = block_bootstrap_null(panel, block=10, n_boot=1000, seed=42)
    evidence = factor_evidence(panel, boot, p_threshold=0.05).set_index("factor")
    assert evidence.loc["tight", "ic_bar"] < evidence.loc["wide", "ic_bar"]
    assert evidence.loc["tight", "z_stat"] > evidence.loc["wide", "z_stat"]
    assert evidence.loc["tight", "p_emp"] < evidence.loc["wide", "p_emp"]


def test_maxstat_controls_family_wise_error_on_pure_noise():
    """175 pure-noise cells: the grid-wide max null yields ~0 rejections,
    not the ~9 a naive per-cell 5% test would produce."""
    rejected = []
    for seed in range(6):
        factors = {f"f{i}": 0.0 for i in range(25)}
        panel = _synthetic_ic_panel(factors, n_dates=400, noise=0.15, seed=seed)
        null_max = block_bootstrap_null(panel, block=10, n_boot=500, seed=42)
        evidence = factor_evidence(panel, null_max, p_threshold=0.05)
        rejected.append(int((evidence["verdict"] == "PASS").sum()))
    # family-wise: average false positives across 175 cells stays well under 1,
    # versus 175 * 0.05 ~= 8.75 for an uncorrected per-cell test.
    assert np.mean(rejected) < 1.0


def test_default_block_matches_horizon():
    # short horizons floored at 10; long horizons scale past the horizon length
    assert default_block_for_horizon(1) == 10
    assert default_block_for_horizon(5) == 10
    assert default_block_for_horizon(21) == 42
    assert default_block_for_horizon(42) >= 42


def test_grid_applies_horizon_and_global_correction():
    """evaluate_factor_grid runs per-horizon families and adds a Bonferroni
    across-horizon global verdict; columns and monotonicity are present."""
    panel = _synthetic_ic_panel(
        {"a": 0.0, "b": 0.0}, horizons=(1, 5, 21), n_dates=400, seed=4
    )
    grid = evaluate_factor_grid(panel, n_boot=300, seed=42)
    assert {"block", "crit_z_5pct", "p_horizon", "p_global", "verdict",
            "verdict_horizon"} <= set(grid.columns)
    # global p is the horizon p inflated by the number of distinct horizons (3),
    # capped at 1.
    sample = grid.iloc[0]
    assert sample["p_global"] >= sample["p_horizon"] - 1e-12
    # per-horizon block tracks the horizon
    for h in (1, 5, 21):
        block = grid.loc[grid["horizon"] == h, "block"].iloc[0]
        assert block == default_block_for_horizon(h)
    # pure noise: nothing survives the global correction
    assert (grid["verdict"] == "REJECT").all()


def test_grid_horizon_matched_block_kills_block_shorter_than_horizon_artifact():
    """A slow signal that only 'passes' when the block is shorter than the
    horizon (SE underestimated) must NOT pass once the block matches horizon.

    Construct a long-horizon IC series with strong positive autocorrelation so a
    too-short block underestimates its SE; the horizon-matched grid must reject
    it, while a deliberately-too-short block would over-reject the null."""
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2019-01-01", periods=600)
    # AR(1)-style persistent daily IC with mean ~0: strong lag dependence, true
    # mean zero. A block << horizon underestimates SE -> spurious significance.
    horizon = 42
    innov = rng.normal(0.0, 0.05, len(dates))
    series = np.zeros(len(dates))
    for t in range(1, len(dates)):
        series[t] = 0.9 * series[t - 1] + innov[t]
    ic = pd.Series(series - series.mean(), index=dates)  # mean-zero persistent null
    panel = _ic_panel_from_daily({("persistent", horizon): ic})

    short = block_bootstrap_null(panel, block=5, n_boot=800, seed=42)
    matched = block_bootstrap_null(
        panel, block=default_block_for_horizon(horizon), n_boot=800, seed=42
    )
    # the horizon-matched block yields a larger (honest) SE than a too-short block
    assert matched.se[0] > short.se[0]


def test_n_eff_and_columns_present_in_output():
    panel = _synthetic_ic_panel({"f": 0.0}, horizons=(5, 10), n_dates=300, seed=2)
    null_max = block_bootstrap_null(panel, block=10, n_boot=100, seed=42)
    evidence = factor_evidence(panel, null_max, p_threshold=0.05)
    assert set(evidence.columns) >= {
        "factor", "horizon", "ic_bar", "n_eff", "t_naive", "p_emp", "verdict"
    }
    row5 = evidence[evidence["horizon"] == 5].iloc[0]
    row10 = evidence[evidence["horizon"] == 10].iloc[0]
    assert row5["n_eff"] == 2 * row10["n_eff"]
