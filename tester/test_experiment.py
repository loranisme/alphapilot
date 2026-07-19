from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from research_platform.config import ExperimentConfig
from research_platform.contracts import ExperimentResult
from research_platform.experiment import ExperimentInputs, run_experiment
from research_platform.reporting import write_result


def test_config_requires_purge_at_least_horizon():
    with pytest.raises(ValueError, match="purge_periods"):
        ExperimentConfig(horizon=5, purge_periods=4)


def test_writer_emits_required_artifacts(tmp_path):
    result = ExperimentResult(
        metrics={"raw": {"ic_mean": 0.01}},
        tables={
            "factor_diagnostics": pd.DataFrame([{"variant": "raw", "ic_mean": 0.01}]),
            "portfolio_metrics": pd.DataFrame([{"variant": "raw", "sharpe": 0.2}]),
        },
        quality={
            "classification_coverage": 1.0,
            "classification_point_in_time": False,
            "gates": {"coverage": True},
        },
        metadata={"data_fingerprint": "fixture-hash", "config": {"horizon": 5}},
    )
    paths = write_result(result, tmp_path)
    assert {path.name for path in paths} == {
        "experiment_summary.json",
        "factor_diagnostics.csv",
        "portfolio_metrics.csv",
        "quality_report.json",
        "report.md",
    }
    summary = json.loads((tmp_path / "experiment_summary.json").read_text())
    assert summary["metadata"]["data_fingerprint"] == "fixture-hash"
    assert "classification_point_in_time: False" in (tmp_path / "report.md").read_text()


def test_run_experiment_compares_raw_and_neutralized_variants():
    dates = pd.bdate_range("2024-01-02", periods=20)
    tickers = [f"T{i}" for i in range(10)]
    values = np.tile(np.arange(10, dtype=float), (20, 1))
    raw = pd.DataFrame(values, index=dates, columns=tickers)
    neutralized = raw.sub(raw.mean(axis=1), axis=0)
    forward = pd.DataFrame(values * 0.001, index=dates, columns=tickers)
    industry = pd.DataFrame(
        np.tile(["A"] * 5 + ["B"] * 5, (20, 1)), index=dates, columns=tickers
    )
    inputs = ExperimentInputs(
        variants={"raw": raw, "neutralized": neutralized},
        forward_returns=forward,
        asset_returns=forward,
        industry=industry,
        data_fingerprint="fixture-hash",
    )
    result = run_experiment(
        ExperimentConfig(
            horizon=1,
            purge_periods=1,
            min_names=10,
            max_weight=0.30,
            classification_coverage=0.90,
        ),
        inputs,
    )
    diagnostics = result.tables["factor_diagnostics"]
    portfolios = result.tables["portfolio_metrics"].set_index("variant")
    assert set(diagnostics["variant"]) == {"raw", "neutralized"}
    assert portfolios.loc["raw", "portfolio_mode"] == "global"
    assert portfolios.loc["neutralized", "portfolio_mode"] == "industry_neutral"
    assert result.metadata["data_fingerprint"] == "fixture-hash"
    assert result.quality["gates"]["classification_coverage"]


def test_portfolio_uses_one_day_asset_returns_not_forward_label_returns():
    dates = pd.bdate_range("2024-01-02", periods=6)
    tickers = [f"T{i}" for i in range(10)]
    factor = pd.DataFrame(
        np.tile(np.arange(10, dtype=float), (6, 1)), index=dates, columns=tickers
    )
    labels = pd.DataFrame(0.10, index=dates, columns=tickers)
    one_day = pd.DataFrame(0.0, index=dates, columns=tickers)
    industry = pd.DataFrame(
        np.tile(["A"] * 5 + ["B"] * 5, (6, 1)), index=dates, columns=tickers
    )
    result = run_experiment(
        ExperimentConfig(
            horizon=1,
            purge_periods=1,
            min_names=10,
            max_weight=0.30,
            cost_bps=0.0,
        ),
        ExperimentInputs(
            variants={"raw": factor, "neutralized": factor},
            forward_returns=labels,
            asset_returns=one_day,
            industry=industry,
            data_fingerprint="fixture-hash",
        ),
    )
    metrics = result.tables["portfolio_metrics"].set_index("variant")
    assert metrics.loc["raw", "annualized_return"] == 0.0
