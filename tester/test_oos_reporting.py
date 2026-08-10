import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from research_platform.reporting import (
    build_oos_report_tables,
    evaluate_oos_gates,
    industry_exposure_table,
    write_oos_report,
)


def test_report_builder_produces_fold_year_cost_and_exposure_tables():
    dates = pd.bdate_range("2020-12-28", periods=10)
    names = list("ABCD")
    signal = pd.DataFrame(
        np.tile(np.arange(4, dtype=float), (10, 1)), index=dates, columns=names
    )
    targets = pd.DataFrame(
        np.tile([-0.5, -0.5, 0.5, 0.5], (10, 1)), index=dates, columns=names
    )
    returns = signal * 0.001
    portfolios = {}
    for path in ("raw", "soft", "strict"):
        portfolios[path] = SimpleNamespace(
            net_returns=pd.Series(0.001, index=dates),
            turnover=pd.Series(0.01, index=dates),
            metrics={"sharpe": 1.0},
            weights=targets,
        )
    result = SimpleNamespace(
        folds=(SimpleNamespace(number=0, test_dates=dates),),
        scores={path: signal for path in portfolios},
        portfolio_targets={path: targets for path in portfolios},
        portfolios=portfolios,
        quality={"label_overlap_count": 0},
    )
    industry = pd.DataFrame(
        np.tile(["x", "x", "y", "y"], (10, 1)), index=dates, columns=names
    )

    tables, quality = build_oos_report_tables(
        result,
        forward_returns=returns,
        asset_returns=returns,
        industry=industry,
        baseline_annual_cost=0.10,
        cost_stress_bps=(0.0, 10.0),
        min_names=4,
    )

    assert set(tables) == {
        "fold_metrics",
        "year_metrics",
        "cost_stress",
        "industry_exposure",
        "regime_stability",
        "group_stratification",
    }
    assert set(tables["regime_stability"]["path"]) == {"raw", "soft", "strict"}
    assert set(tables["fold_metrics"]["path"]) == {"raw", "soft", "strict"}
    assert set(tables["cost_stress"]["cost_bps"]) == {0.0, 10.0}
    assert "zero_label_overlap" in quality["gates"]


def test_acceptance_gates_use_comparable_oos_baseline():
    gates = evaluate_oos_gates(
        raw_sharpe=0.50,
        soft_sharpe=0.60,
        baseline_annual_cost=0.10,
        soft_annual_cost=0.05,
        soft_max_industry_exposure=0.07,
        raw_ic=0.04,
        soft_ic=0.034,
        label_overlap_count=0,
    )

    assert all(gates.values())


def test_acceptance_gates_fail_at_wrong_side_of_fixed_thresholds():
    gates = evaluate_oos_gates(
        raw_sharpe=0.50,
        soft_sharpe=0.50,
        baseline_annual_cost=0.10,
        soft_annual_cost=0.061,
        soft_max_industry_exposure=0.081,
        raw_ic=0.04,
        soft_ic=0.031,
        label_overlap_count=1,
    )

    assert not any(gates.values())


def test_industry_exposure_reports_daily_net_exposure_and_maximum():
    dates = pd.bdate_range("2020-01-01", periods=2)
    weights = pd.DataFrame(
        [[0.2, 0.1, -0.2, -0.1], [0.1, -0.1, 0.2, -0.2]],
        index=dates,
        columns=list("ABCD"),
    )
    industry = pd.DataFrame(
        [["x", "x", "y", "y"]] * 2, index=dates, columns=weights.columns
    )

    exposure = industry_exposure_table(weights, industry)

    assert exposure.loc[dates[0], "x"] == pytest.approx(0.3)
    assert exposure.loc[dates[0], "max_abs_industry"] == pytest.approx(0.3)
    assert exposure.loc[dates[1], "max_abs_industry"] == pytest.approx(0.0)


def test_oos_writer_emits_all_tables_and_gate_details(tmp_path):
    tables = {
        "fold_metrics": pd.DataFrame([{"fold": 0, "path": "raw", "sharpe": 0.5}]),
        "year_metrics": pd.DataFrame([{"year": 2020, "path": "raw", "sharpe": 0.5}]),
        "cost_stress": pd.DataFrame([{"cost_bps": 10, "path": "raw", "sharpe": 0.5}]),
        "industry_exposure": pd.DataFrame([{"path": "soft", "max_abs_industry": 0.07}]),
    }
    quality = {"gates": {"zero_label_overlap": True}, "label_overlap_count": 0}

    paths = write_oos_report(tables, quality, {"data_fingerprint": "abc"}, tmp_path)

    assert {path.name for path in paths} == {
        "fold_metrics.csv",
        "year_metrics.csv",
        "cost_stress.csv",
        "industry_exposure.csv",
        "quality_report.json",
        "metadata.json",
        "report.md",
    }
    assert json.loads((tmp_path / "quality_report.json").read_text())["gates"][
        "zero_label_overlap"
    ]
    assert "PASS: `zero_label_overlap`" in (tmp_path / "report.md").read_text()
