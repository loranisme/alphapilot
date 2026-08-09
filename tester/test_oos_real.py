from pathlib import Path

import pytest

from scripts.run_oos_alpha_validation import hash_outputs, run_real_oos_validation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HAS_REAL_DATA = (PROJECT_ROOT / "data" / "reports" / "composite_alpha_latest.csv").exists()


@pytest.mark.real_data
@pytest.mark.skipif(not HAS_REAL_DATA, reason="real factor universe is unavailable")
def test_real_oos_pipeline_has_no_leakage_and_reproduces(tmp_path):
    first = run_real_oos_validation(
        output_dir=tmp_path / "first", allow_network=False
    )
    second = run_real_oos_validation(
        output_dir=tmp_path / "second", allow_network=False
    )

    assert first.quality["label_overlap_count"] == 0
    assert first.metadata["data_fingerprint"] == second.metadata["data_fingerprint"]
    assert hash_outputs(tmp_path / "first") == hash_outputs(tmp_path / "second")
    assert (tmp_path / "first" / "fold_metrics.csv").exists()


@pytest.mark.skipif(not HAS_REAL_DATA, reason="real factor universe is unavailable")
def test_real_oos_emits_readable_scorecard(tmp_path):
    result = run_real_oos_validation(output_dir=tmp_path, allow_network=False)
    scorecard = tmp_path / "scorecard.md"
    assert scorecard.exists()
    text = scorecard.read_text(encoding="utf-8")
    assert "因子验证记分卡" in text
    assert "相关性矩阵" in text
    assert (tmp_path / "factor_scorecard.csv").exists()
    assert (tmp_path / "value_matrix.csv").exists()
    assert (tmp_path / "group_summary.csv").exists()


@pytest.mark.skipif(not HAS_REAL_DATA, reason="real factor universe is unavailable")
def test_real_oos_small_universe_fails_with_actionable_message(tmp_path):
    # Exercises the wired size-bucket universe axis end-to-end: the smaller half
    # of the index is too thin for the default position caps, so the runner must
    # fail early with a message naming the knob to adjust (not a cryptic error
    # deep in portfolio construction).
    with pytest.raises(ValueError, match="name_weight_cap"):
        run_real_oos_validation(
            output_dir=tmp_path, allow_network=False, universe="smaller_half"
        )
