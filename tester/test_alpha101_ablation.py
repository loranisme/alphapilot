import numpy as np
import pandas as pd

from research_platform.ablation import (
    AblationConfig,
    run_alpha101_correlation_ablation,
)
from research_platform.oos import OOSConfig, run_oos_experiment


def make_ablation_fixture(existing_count=7, alpha_count=1):
    dates = pd.bdate_range("2019-01-01", periods=420)
    names = [f"T{i}" for i in range(60)]
    rng = np.random.default_rng(11)
    raw = rng.normal(size=(60, max(existing_count, 7) + alpha_count))
    orthogonal, _ = np.linalg.qr(raw)
    exposures = orthogonal.T
    factor_vectors = [exposures[number] for number in range(existing_count)]
    if existing_count >= 2:
        factor_vectors[1] = factor_vectors[0] * 2.0
    alpha_vectors = [
        exposures[existing_count + number] for number in range(alpha_count)
    ]
    combined = np.sum(factor_vectors, axis=0)
    one_day = np.tile(combined * 0.002, (len(dates), 1))
    close = pd.DataFrame(
        100.0 * np.cumprod(1.0 + one_day, axis=0), index=dates, columns=names
    )

    def panel(vector):
        return pd.DataFrame(
            np.tile(vector, (len(dates), 1)), index=dates, columns=names
        )

    existing = {f"existing_{i}": panel(vector) for i, vector in enumerate(factor_vectors)}
    alpha = {f"alpha_{i}": panel(vector) for i, vector in enumerate(alpha_vectors)}
    industry = pd.DataFrame(
        np.tile(["A"] * 20 + ["B"] * 20 + ["C"] * 20, (len(dates), 1)),
        index=dates,
        columns=names,
    )
    oos = OOSConfig(
        min_train=300,
        test_size=50,
        step=50,
        min_names=30,
        name_weight_cap=0.10,
    )
    config = AblationConfig(oos=oos, min_pair_dates=60, min_block_observations=20)
    return existing, alpha, close, industry, config


def test_ablation_uses_shared_folds_and_preserves_arm_a_baseline():
    existing, alpha, close, industry, config = make_ablation_fixture()

    result = run_alpha101_correlation_ablation(
        existing, alpha, close, industry, config
    )
    baseline = run_oos_experiment(existing, close, industry, config=config.oos)

    assert set(result.arms) == {"A", "B", "C"}
    assert result.arms["A"].candidate_names == tuple(existing)
    assert result.arms["B"].candidate_names == tuple(existing)
    assert result.arms["C"].candidate_names == tuple(existing) + tuple(alpha)
    for arm in result.arms.values():
        assert [tuple(fold.test_dates) for fold in arm.experiment.folds] == [
            tuple(fold.test_dates) for fold in baseline.folds
        ]
        assert set(arm.experiment.scores) == {"raw", "soft", "strict"}
    assert result.arms["A"].experiment.folds[0].selection.weights == baseline.folds[0].selection.weights
    pd.testing.assert_series_equal(
        result.arms["A"].experiment.portfolios["soft"].net_returns,
        baseline.portfolios["soft"].net_returns,
    )


def test_invalid_correlation_fold_holds_zero_or_previous_targets():
    existing, alpha, close, industry, config = make_ablation_fixture(
        existing_count=5, alpha_count=0
    )

    result = run_alpha101_correlation_ablation(
        existing, alpha, close, industry, config
    )

    arm_b = result.arms["B"]
    assert not arm_b.fold_diagnostics["valid"].any()
    assert (arm_b.experiment.portfolio_targets["raw"] == 0.0).all().all()
    assert set(arm_b.experiment.portfolio_diagnostics["raw"]["action"]) == {
        "hold_invalid",
        "hold_schedule",
    }
