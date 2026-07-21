from pathlib import Path

from research_platform.ablation import run_alpha101_correlation_ablation
from research_platform.ablation_reporting import (
    build_ablation_report,
    write_ablation_report,
)
from tester.test_alpha101_ablation import make_ablation_fixture


EXPECTED_TABLES = {
    "factor_value_correlation",
    "ic_correlation",
    "correlation_clusters",
    "factor_selection_by_fold",
    "candidate_coverage",
    "ablation_metrics",
    "fold_metrics",
    "year_metrics",
    "cost_stress",
    "industry_exposure",
}


def build_fixture_report():
    existing, alpha, close, industry, config = make_ablation_fixture()
    result = run_alpha101_correlation_ablation(
        existing, alpha, close, industry, config
    )
    forward = close.shift(-config.oos.horizon).div(close).sub(1.0)
    asset_returns = close.pct_change(fill_method=None)
    tables, quality = build_ablation_report(
        result, forward, asset_returns, industry, config
    )
    return tables, quality


def test_report_builds_exact_tables_cost_grid_and_gate_contract():
    tables, quality = build_fixture_report()

    assert set(tables) == EXPECTED_TABLES
    assert set(tables["cost_stress"]["cost_bps"]) == {0.0, 5.0, 10.0, 20.0}
    assert len(tables["cost_stress"]) == 3 * 3 * 4
    assert set(quality["research_gates"]) == {
        "c_raw_ic_not_below_a",
        "c_soft_sharpe_above_a",
        "c_soft_turnover_not_above_a",
        "c_positive_ic_folds_at_least_a",
        "c_soft_industry_exposure_at_most_8pct",
    }
    assert quality["engineering_gates"]["shared_folds"]
    assert quality["engineering_gates"]["zero_label_overlap"]


def test_writer_emits_exact_deterministic_output_contract(tmp_path):
    tables, quality = build_fixture_report()
    metadata = {"source": "fixture", "formula_ids": [2, 7, 12]}
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_paths = write_ablation_report(tables, quality, metadata, first)
    second_paths = write_ablation_report(tables, quality, metadata, second)

    expected_files = {f"{name}.csv" for name in EXPECTED_TABLES} | {
        "quality_report.json",
        "metadata.json",
        "report.md",
    }
    assert {path.name for path in first_paths} == expected_files
    assert {path.name for path in Path(first).iterdir()} == expected_files
    assert {
        path.name: path.read_bytes() for path in Path(first).iterdir()
    } == {path.name: path.read_bytes() for path in Path(second).iterdir()}
