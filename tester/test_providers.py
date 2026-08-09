from __future__ import annotations

import json

import pandas as pd
import pytest

from research_platform.providers import (
    CachedJsonProvider,
    build_pit_universe,
    classification_from_records,
    membership_from_date_added,
    parse_constituents_snapshot,
    pit_industry_panel,
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


def test_parse_constituents_snapshot_normalizes_symbols_and_dates():
    table = pd.DataFrame(
        {
            "Symbol": ["BRK.B", "AAPL", "AAPL"],
            "GICS Sector": ["Financials", "IT", "IT"],
            "Date added": ["1957-03-04", "not-a-date", "2015-03-18"],
        }
    )
    parsed = parse_constituents_snapshot(table)
    assert "BRK-B" in parsed.index  # dot -> dash
    assert parsed.index.is_unique  # duplicate AAPL dropped
    assert parsed.loc["BRK-B", "date_added"] == pd.Timestamp("1957-03-04")
    assert pd.isna(parsed.loc["AAPL", "date_added"])  # unparseable -> NaT


def test_membership_from_date_added_has_no_addition_lookahead():
    dates = pd.to_datetime(["2019-06-30", "2020-06-30", "2021-06-30"])
    date_added = pd.Series(
        {"OLD": pd.Timestamp("2005-01-01"), "NEW": pd.Timestamp("2021-01-01")}
    )
    membership = membership_from_date_added(date_added, dates)
    assert membership.loc[dates, "OLD"].all()  # long-standing member throughout
    assert not membership.loc[pd.Timestamp("2019-06-30"), "NEW"]  # not yet added
    assert not membership.loc[pd.Timestamp("2020-06-30"), "NEW"]
    assert membership.loc[pd.Timestamp("2021-06-30"), "NEW"]  # added by then


def test_membership_from_date_added_treats_missing_date_as_always_member():
    dates = pd.to_datetime(["2019-06-30", "2021-06-30"])
    membership = membership_from_date_added(pd.Series({"X": pd.NaT}), dates)
    assert membership["X"].all()


def test_build_pit_universe_additions_only_and_changes_paths():
    dates = pd.to_datetime(["2019-06-30", "2021-06-30"])
    constituents = pd.DataFrame(
        {
            "sector": {"OLD": "Energy", "NEW": "IT"},
            "date_added": {"OLD": pd.Timestamp("2005-01-01"), "NEW": pd.Timestamp("2021-01-01")},
        }
    )
    universe, classification, meta = build_pit_universe(constituents, dates)
    assert meta["universe_membership_basis"] == "date_added_additions_only"
    assert meta["membership_point_in_time"] is False
    assert meta["universe_size_start"] == 1  # only OLD in 2019
    assert meta["universe_size_end"] == 2
    assert classification.asof("2021-06-30").loc["NEW"] == "IT"

    changes = pd.DataFrame(
        [{"effective_date": "2021-01-01", "added": "NEW", "removed": "GONE"}]
    )
    _, _, meta_full = build_pit_universe(constituents, dates, changes=changes)
    assert meta_full["universe_membership_basis"] == "changes_reversed"
    assert meta_full["membership_point_in_time"] is True


def test_pit_industry_panel_masks_non_members_and_reports_member_coverage():
    dates = pd.to_datetime(["2019-06-30", "2021-06-30"])
    constituents = pd.DataFrame(
        {
            "sector": {"OLD": "Energy", "NEW": "IT"},
            "date_added": {"OLD": pd.Timestamp("2005-01-01"), "NEW": pd.Timestamp("2021-01-01")},
        }
    )
    universe, classification, _ = build_pit_universe(constituents, dates)
    industry, coverage = pit_industry_panel(classification, universe, dates, ["OLD", "NEW"])
    assert pd.isna(industry.loc[pd.Timestamp("2019-06-30"), "NEW"])  # not a member yet
    assert industry.loc[pd.Timestamp("2021-06-30"), "NEW"] == "IT"
    assert industry.loc[pd.Timestamp("2019-06-30"), "OLD"] == "Energy"
    assert coverage == 1.0  # every member cell has a known sector
