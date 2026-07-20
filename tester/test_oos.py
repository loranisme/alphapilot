import numpy as np
import pandas as pd

from research_platform.oos import OOSConfig, run_oos_experiment


def make_oos_fixture():
    dates = pd.bdate_range("2018-01-01", periods=420)
    names = [f"T{i}" for i in range(60)]
    rank = np.linspace(-1.0, 1.0, len(names))
    one_day = np.tile(rank * 0.0005, (len(dates), 1))
    close = pd.DataFrame(
        100.0 * np.cumprod(1.0 + one_day, axis=0), index=dates, columns=names
    )
    factors = {}
    for number in range(5):
        wave = np.sin(np.arange(len(dates))[:, None] / (11 + number)) * 0.01
        factors[f"factor_{number}"] = pd.DataFrame(
            np.tile(rank, (len(dates), 1)) + wave,
            index=dates,
            columns=names,
        )
    industry = pd.DataFrame(
        np.tile(["A"] * 20 + ["B"] * 20 + ["C"] * 20, (len(dates), 1)),
        index=dates,
        columns=names,
    )
    config = OOSConfig(
        min_train=300,
        test_size=50,
        step=50,
        min_names=30,
        name_weight_cap=0.10,
    )
    return {"factors": factors, "close": close, "industry": industry, "config": config}


def test_changing_test_returns_does_not_change_fold_state():
    inputs = make_oos_fixture()
    original = run_oos_experiment(**inputs)
    changed = inputs["close"].copy()
    test_dates = original.folds[0].test_dates
    changed.loc[test_dates, :] *= np.linspace(1.0, 2.0, len(test_dates))[:, None]

    rerun = run_oos_experiment(**{**inputs, "close": changed})

    assert original.folds[0].selection.directions == rerun.folds[0].selection.directions
    assert original.folds[0].selection.weights == rerun.folds[0].selection.weights


def test_oos_folds_are_purged_unique_and_share_three_paths():
    result = run_oos_experiment(**make_oos_fixture())

    for fold in result.folds:
        assert fold.train_dates[-1] < fold.test_dates[0]
        assert len(pd.bdate_range(fold.train_dates[-1], fold.test_dates[0])) >= 7
    assert result.quality["label_overlap_count"] == 0
    assert result.scores["raw"].index.is_unique
    assert set(result.scores) == {"raw", "soft", "strict"}
    assert set(result.portfolios) == {"raw", "soft", "strict"}
    assert result.scores["raw"].index.equals(result.scores["soft"].index)


def test_invalid_oos_rebalance_date_holds_existing_positions():
    inputs = make_oos_fixture()
    first = run_oos_experiment(**inputs)
    invalid_date = first.scores["raw"].index[5]
    for panel in inputs["factors"].values():
        panel.loc[invalid_date] = np.nan

    result = run_oos_experiment(**inputs)

    targets = result.portfolio_targets["raw"]
    pd.testing.assert_series_equal(
        targets.loc[invalid_date], targets.iloc[4], check_names=False
    )
    assert result.portfolio_diagnostics["raw"].loc[invalid_date, "action"] == "hold_invalid"


def test_oos_config_loads_immutable_yaml(tmp_path):
    path = tmp_path / "oos.yaml"
    path.write_text("horizon: 5\npurge: 5\nrebalance_interval: 5\ncost_bps: 12.0\n")

    config = OOSConfig.from_yaml(path)

    assert config.cost_bps == 12.0
    assert config.to_dict()["soft_strength"] == 0.5
