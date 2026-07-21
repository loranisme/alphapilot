from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research_platform.ablation import AblationConfig
from research_platform.market_data import ResearchOHLCVBundle
from scripts.run_alpha101_correlation_oos import (
    _build_factor_inputs,
    hash_outputs,
    run_alpha101_correlation_validation,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_existing_factor_builder_preserves_invalid_ohlcv_date_as_missing():
    dates = pd.bdate_range("2024-01-01", periods=12)
    frame = pd.DataFrame(
        {
            "Open": np.arange(12, dtype=float) + 10.0,
            "High": np.arange(12, dtype=float) + 11.0,
            "Low": np.arange(12, dtype=float) + 9.0,
            "Close": np.arange(12, dtype=float) + 10.5,
            "Volume": np.arange(12, dtype=float) + 100.0,
        },
        index=dates,
    )
    frame.loc[dates[5], ["Open", "High", "Low", "Close", "Volume"]] = np.nan

    existing, alpha101, close = _build_factor_inputs(
        ResearchOHLCVBundle(frames={"AAA": frame}, metadata={}), dates
    )

    assert close.loc[dates[5], "AAA"] != close.loc[dates[5], "AAA"]
    assert all(panel.loc[dates[5], "AAA"] != panel.loc[dates[5], "AAA"] for panel in existing.values())
    assert all(panel.loc[dates[5], "AAA"] != panel.loc[dates[5], "AAA"] for panel in alpha101.values())


def test_ablation_config_loads_nested_immutable_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "oos:\n"
        "  horizon: 5\n"
        "  purge: 5\n"
        "  rebalance_interval: 5\n"
        "correlation:\n"
        "  hard_threshold: 0.75\n"
        "  min_factors: 5\n"
        "  max_factors: 6\n",
        encoding="utf-8",
    )

    config = AblationConfig.from_yaml(path)

    assert config.oos.horizon == 5
    assert config.hard_threshold == 0.75
    assert (config.min_factors, config.max_factors) == (5, 6)


@pytest.mark.real_data
def test_real_alpha101_correlation_ablation_is_complete_and_auditable(tmp_path):
    output = tmp_path / "alpha101_correlation_oos"

    result = run_alpha101_correlation_validation(
        output_dir=output,
        config_path=PROJECT_ROOT / "configs" / "alpha101_correlation_oos.yaml",
        project_root=PROJECT_ROOT,
        allow_network=False,
    )

    assert len(result.metadata["existing_factor_names"]) == 13
    assert len(result.metadata["alpha101_factor_names"]) == 12
    assert result.metadata["candidate_counts"] == {"A": 13, "B": 13, "C": 25}
    assert result.quality["engineering_pass"]
    assert set(result.tables["cost_stress"]["cost_bps"]) == {0.0, 5.0, 10.0, 20.0}
    assert len(list(output.iterdir())) == 13
    assert len(hash_outputs(output)) == 64
