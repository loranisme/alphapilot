from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts.run_research_platform_validation import (
    _load_factor_report,
    _neutralization_quality,
    build_real_inputs,
    execute_validation,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HAS_REAL_DATA = (PROJECT_ROOT / "data" / "reports" / "composite_alpha_latest.csv").exists()


def test_factor_report_drops_rows_without_valid_dates(tmp_path):
    path = tmp_path / "factor.csv"
    path.write_text("Date,A,B\n2024-01-02,1,2\n,3,4\n", encoding="utf-8")
    frame, invalid_dates = _load_factor_report(path)
    assert invalid_dates == 1
    assert list(frame.index) == [pd.Timestamp("2024-01-02")]


def test_neutralization_quality_uses_eligible_signal_dates_as_denominator():
    dates = pd.bdate_range("2024-01-02", periods=3)
    standardized = pd.DataFrame(
        [[1.0, -1.0], [float("nan"), float("nan")], [2.0, -2.0]],
        index=dates,
        columns=["A", "B"],
    )
    diagnostics = pd.DataFrame(
        {"valid": [True, False, True]}, index=dates
    )
    quality = _neutralization_quality(standardized, diagnostics)
    assert quality["raw_usable_date_ratio"] == pytest.approx(2 / 3)
    assert quality["neutralization_success_on_eligible_dates"] == 1.0


@pytest.mark.real_data
@pytest.mark.skipif(not HAS_REAL_DATA, reason="real composite alpha report is unavailable")
def test_real_neutralization_quality(tmp_path):
    config, inputs, quality = build_real_inputs(
        PROJECT_ROOT / "configs" / "research_platform_example.yaml",
        project_root=PROJECT_ROOT,
        allow_network=False,
    )
    result = execute_validation(config, inputs, quality, tmp_path)
    assert result.quality["classification_coverage"] >= 0.90
    assert result.quality["max_abs_industry_exposure"] <= 1e-8
    assert result.quality["label_overlap_count"] == 0
    assert result.metadata["data_fingerprint"]
    assert (tmp_path / "experiment_summary.json").exists()
    assert (tmp_path / "report.md").exists()
