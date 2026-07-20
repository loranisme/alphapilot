import numpy as np
import pandas as pd
import pytest

from research_platform.portfolio import (
    buffered_cross_section,
    build_buffered_targets,
    simulate_portfolio,
)


def make_ranked_scores(periods=6, names=300):
    dates = pd.bdate_range("2020-01-01", periods=periods)
    columns = [f"T{i}" for i in range(names)]
    values = np.tile(np.arange(names, dtype=float), (periods, 1))
    return pd.DataFrame(values, index=dates, columns=columns)


def test_invalid_signal_day_holds_previous_target():
    scores = make_ranked_scores()
    scores.iloc[5] = np.nan

    result = build_buffered_targets(scores, rebalance_interval=5, max_weight=0.02)

    pd.testing.assert_series_equal(
        result.targets.iloc[5], result.targets.iloc[4], check_names=False
    )
    assert result.diagnostics.loc[scores.index[5], "action"] == "hold_invalid"


def test_existing_name_stays_until_it_leaves_thirty_percent_buffer():
    previous = pd.Series(0.0, index=[f"T{i}" for i in range(100)])
    previous["T90"] = 0.05
    score = pd.Series(np.arange(100, dtype=float), index=previous.index)
    score["T90"] = 75

    target = buffered_cross_section(
        score,
        previous,
        entry_quantile=0.20,
        exit_quantile=0.30,
        max_weight=0.05,
    )

    assert target["T90"] > 0


def test_targets_rebalance_only_every_five_days_and_are_neutral_capped():
    scores = make_ranked_scores(periods=7)
    scores.iloc[5] = scores.iloc[5, ::-1].to_numpy()

    result = build_buffered_targets(scores, rebalance_interval=5, max_weight=0.02)

    pd.testing.assert_series_equal(
        result.targets.iloc[1], result.targets.iloc[0], check_names=False
    )
    assert result.diagnostics.iloc[1]["action"] == "hold_schedule"
    assert result.targets.iloc[0].sum() == pytest.approx(0.0)
    assert result.targets.iloc[0].clip(lower=0).sum() == pytest.approx(1.0)
    assert -result.targets.iloc[0].clip(upper=0).sum() == pytest.approx(1.0)
    assert result.targets.abs().max().max() <= 0.02 + 1e-12
    assert not result.targets.iloc[5].equals(result.targets.iloc[4])


def test_infeasible_name_cap_raises_clear_error():
    score = pd.Series(np.arange(100, dtype=float))

    with pytest.raises(ValueError, match="infeasible"):
        buffered_cross_section(score, pd.Series(0.0, index=score.index), max_weight=0.02)


def test_signal_at_t_is_executed_at_t_plus_one():
    scores = make_ranked_scores(periods=3)
    targets = build_buffered_targets(scores, max_weight=0.02).targets
    returns = pd.DataFrame(0.0, index=scores.index, columns=scores.columns)
    returns.iloc[0] = np.sign(targets.iloc[0]) * 0.01
    returns.iloc[1] = np.sign(targets.iloc[0]) * 0.01

    result = simulate_portfolio(targets, returns, cost_bps=0)

    assert result.gross_returns.iloc[0] == 0
    assert result.gross_returns.iloc[1] > 0
