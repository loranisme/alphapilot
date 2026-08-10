from __future__ import annotations

import pytest

from research_platform.registry import (
    ExperimentRecord,
    append_record,
    benjamini_hochberg,
    bonferroni,
    load_records,
    summarize_ledger,
)


def test_record_validates_pvalue_and_hypothesis_count():
    ExperimentRecord(name="ok", family_wise_p=0.05, n_hypotheses=1, passed_local=True)
    with pytest.raises(ValueError):
        ExperimentRecord(name="bad_p", family_wise_p=1.5, n_hypotheses=1, passed_local=False)
    with pytest.raises(ValueError):
        ExperimentRecord(name="bad_n", family_wise_p=0.1, n_hypotheses=0, passed_local=False)


def test_bonferroni_scales_by_count_and_clips():
    assert bonferroni([0.01, 0.5]) == [pytest.approx(0.02), pytest.approx(1.0)]
    assert bonferroni([0.4, 0.4]) == [pytest.approx(0.8), pytest.approx(0.8)]


def test_benjamini_hochberg_is_monotone_and_order_preserving():
    adjusted = benjamini_hochberg([0.01, 0.02, 0.03, 0.5])
    assert adjusted == [
        pytest.approx(0.04),
        pytest.approx(0.04),
        pytest.approx(0.04),
        pytest.approx(0.5),
    ]
    # Order preserved even when input is unsorted.
    shuffled = benjamini_hochberg([0.5, 0.01, 0.03, 0.02])
    assert shuffled[0] == pytest.approx(0.5)
    assert shuffled[1] == pytest.approx(0.04)


def test_append_and_load_round_trip_accumulates(tmp_path):
    path = tmp_path / "ledger.jsonl"
    append_record(path, ExperimentRecord("phase_b", 0.2, 175, False, family="factors"))
    append_record(path, ExperimentRecord("phase_c", 1.0, 42, False, family="fundamentals"))
    records = load_records(path)
    assert [r.name for r in records] == ["phase_b", "phase_c"]
    assert records[1].n_hypotheses == 42
    assert load_records(tmp_path / "missing.jsonl") == []


def test_summarize_reapplies_correction_across_the_program():
    records = [
        ExperimentRecord("a", 0.001, 10, True),
        ExperimentRecord("b", 0.03, 10, True),  # passes locally, not program-wide
        ExperimentRecord("c", 0.2, 10, False),
    ]
    summary = summarize_ledger(records, alpha=0.05, method="bonferroni")
    assert summary["n_experiments"] == 3
    assert summary["total_hypotheses"] == 30
    assert summary["n_pass_local"] == 2
    assert summary["n_pass_program"] == 1  # only 'a' survives Bonferroni across 3
    by_name = {row["name"]: row for row in summary["experiments"]}
    assert by_name["a"]["passed_program"] is True
    assert by_name["b"]["passed_program"] is False
    assert by_name["b"]["adjusted_p"] == pytest.approx(0.09)


def test_summarize_rejects_bad_arguments():
    with pytest.raises(ValueError):
        summarize_ledger([], alpha=0.05, method="nope")
    with pytest.raises(ValueError):
        summarize_ledger([], alpha=1.5, method="bonferroni")
