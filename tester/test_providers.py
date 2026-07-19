from __future__ import annotations

import json

import pandas as pd
import pytest

from research_platform.providers import (
    CachedJsonProvider,
    classification_from_records,
    rebuild_membership,
)


def test_rebuild_membership_reverses_changes_without_lookahead():
    dates = pd.to_datetime(["2024-01-31", "2024-02-29"])
    changes = pd.DataFrame(
        [{"effective_date": "2024-02-01", "added": "NEW", "removed": "OLD"}]
    )
    result = rebuild_membership({"NEW"}, changes, dates)
    assert bool(result.loc[pd.Timestamp("2024-01-31"), "OLD"])
    assert not bool(result.loc[pd.Timestamp("2024-01-31"), "NEW"])
    assert bool(result.loc[pd.Timestamp("2024-02-29"), "NEW"])


def test_rebuild_membership_handles_multiple_events_on_same_date():
    dates = pd.to_datetime(["2024-01-31", "2024-02-29"])
    changes = pd.DataFrame(
        [
            {"effective_date": "2024-02-01", "added": "N1", "removed": "O1"},
            {"effective_date": "2024-02-01", "added": "N2", "removed": "O2"},
        ]
    )
    result = rebuild_membership({"N1", "N2"}, changes, dates)
    assert set(result.columns[result.loc[dates[0]]]) == {"O1", "O2"}


def test_cached_provider_rejects_tampered_cache(tmp_path):
    provider = CachedJsonProvider(tmp_path, user_agent="research test@example.com")
    provider.save("sec.json", {"ok": True}, source_url="https://example.test/sec")
    (tmp_path / "sec.json").write_text('{"ok": false}', encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        provider.load("sec.json")


def test_cached_provider_round_trip_preserves_metadata(tmp_path):
    provider = CachedJsonProvider(tmp_path, user_agent="research test@example.com")
    provider.save("sec.json", {"ok": True}, source_url="https://example.test/sec")
    payload = provider.load("sec.json")
    metadata = json.loads((tmp_path / "sec.json.meta.json").read_text())
    assert payload == {"ok": True}
    assert metadata["source_url"] == "https://example.test/sec"
    assert len(metadata["sha256"]) == 64


def test_classification_records_build_effective_dated_panel():
    records = [
        {"effective_date": "2024-01-02", "ticker": "AAPL", "classification": "Tech"},
        {"effective_date": "2024-01-02", "ticker": "XOM", "classification": "Energy"},
        {"effective_date": "2024-02-01", "ticker": "AAPL", "classification": "IT"},
    ]
    panel = classification_from_records(
        records, taxonomy="GICS", source="fixture", quality="verified"
    )
    assert panel.asof("2024-01-31").loc["AAPL"] == "Tech"
    assert panel.asof("2024-02-01").loc["AAPL"] == "IT"
    assert panel.asof("2024-02-01").loc["XOM"] == "Energy"
