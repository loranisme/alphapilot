from __future__ import annotations

import pandas as pd
import pytest

from research_platform.contracts import (
    ClassificationPanel,
    ExperimentResult,
    FactorPanel,
    MarketDataBundle,
    UniversePanel,
)


def test_market_bundle_rejects_duplicate_dates():
    idx = pd.to_datetime(["2024-01-02", "2024-01-02"])
    frame = pd.DataFrame({("AAPL", "Close"): [100.0, 101.0]}, index=idx)
    with pytest.raises(ValueError, match="unique"):
        MarketDataBundle(frame, source="fixture")


def test_market_bundle_fingerprint_is_deterministic():
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    frame = pd.DataFrame({("AAPL", "Close"): [100.0, 101.0]}, index=idx)
    left = MarketDataBundle(frame, source="fixture")
    right = MarketDataBundle(frame.copy(), source="fixture")
    assert left.fingerprint == right.fingerprint
    assert len(left.fingerprint) == 64


def test_universe_asof_uses_effective_date_without_lookahead():
    membership = pd.DataFrame(
        {"AAPL": [True, False], "MSFT": [False, True]},
        index=pd.to_datetime(["2024-01-02", "2024-02-01"]),
    )
    panel = UniversePanel(membership, source="fixture", quality="verified")
    assert panel.asof("2024-01-31").to_dict() == {"AAPL": True, "MSFT": False}


def test_universe_asof_rejects_dates_before_first_snapshot():
    membership = pd.DataFrame(
        {"AAPL": [True]}, index=pd.to_datetime(["2024-01-02"])
    )
    panel = UniversePanel(membership, source="fixture", quality="verified")
    with pytest.raises(KeyError, match="no universe snapshot"):
        panel.asof("2024-01-01")


def test_classification_asof_and_coverage():
    classification = pd.DataFrame(
        {"AAPL": ["Tech"], "MSFT": [None]},
        index=pd.to_datetime(["2024-01-02"]),
    )
    panel = ClassificationPanel(
        classification, taxonomy="GICS", source="fixture", quality="verified"
    )
    assert panel.asof("2024-01-03").loc["AAPL"] == "Tech"
    assert panel.coverage("2024-01-03") == pytest.approx(0.5)


def test_factor_panel_requires_matching_shapes():
    raw = pd.DataFrame([[1.0]], index=pd.to_datetime(["2024-01-02"]), columns=["A"])
    wrong = pd.DataFrame([[1.0]], index=pd.to_datetime(["2024-01-03"]), columns=["A"])
    with pytest.raises(ValueError, match="aligned"):
        FactorPanel(raw=raw, standardized=wrong)


def test_experiment_result_accepts_structured_outputs():
    result = ExperimentResult(
        metrics={"ic": 0.01},
        quality={"coverage": 1.0},
        metadata={"data_fingerprint": "abc"},
    )
    assert result.metrics["ic"] == 0.01
