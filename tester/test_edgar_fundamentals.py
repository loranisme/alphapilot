"""Fixture tests for the EDGAR point-in-time fundamental transform.

No network. Each fixture mimics the shape of SEC companyfacts fact rows
(start/end/val/filed/form/fp/frame) so the PIT logic — as-first-reported
availability, 4-quarter TTM, restatement selection, instant balances, concept
aliasing — is exercised deterministically.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research_platform.edgar_fundamentals import (
    CONTRACT_COLUMNS,
    build_pit_fundamental_panel,
    pit_instant_series,
    pit_ttm_series,
)


def _q(frame, end, val, filed, start=None, form="10-Q"):
    """A calendar-quarter flow fact row."""
    return {
        "start": start or (pd.Timestamp(end) - pd.Timedelta(days=90)).strftime("%Y-%m-%d"),
        "end": end,
        "val": val,
        "filed": filed,
        "form": form,
        "fp": "Q1",
        "frame": frame,
    }


def _instant(end, val, filed, form="10-Q", frame=None):
    """An instant (balance-sheet) fact row."""
    row = {"end": end, "val": val, "filed": filed, "form": form}
    if frame is not None:
        row["frame"] = frame
    return row


PRICE_DATES = pd.bdate_range("2023-01-02", "2024-12-31")


# --- TTM flow ---------------------------------------------------------------

def test_ttm_sums_four_most_recent_calendar_quarters():
    facts = [
        _q("CY2022Q4", "2022-12-31", 10, "2023-02-01"),
        _q("CY2023Q1", "2023-03-31", 20, "2023-05-01"),
        _q("CY2023Q2", "2023-06-30", 30, "2023-08-01"),
        _q("CY2023Q3", "2023-09-30", 40, "2023-11-01"),
    ]
    s = pit_ttm_series(facts, PRICE_DATES)
    # after the Q3 filing (2023-11-01) all four quarters are known -> 10+20+30+40
    assert s.loc["2023-11-02"] == 100
    # before all four are filed, TTM is NaN (needs a full trailing year)
    assert np.isnan(s.loc["2023-06-30"])


def test_ttm_steps_up_only_after_new_quarter_is_filed():
    facts = [
        _q("CY2022Q4", "2022-12-31", 10, "2023-02-01"),
        _q("CY2023Q1", "2023-03-31", 20, "2023-05-01"),
        _q("CY2023Q2", "2023-06-30", 30, "2023-08-01"),
        _q("CY2023Q3", "2023-09-30", 40, "2023-11-01"),
        _q("CY2023Q4", "2023-12-31", 50, "2024-02-01"),
    ]
    s = pit_ttm_series(facts, PRICE_DATES)
    # window Q4'22..Q3'23 in force right up to the Q4'23 filing
    assert s.loc["2024-01-31"] == 100
    # Q4'23 filed 2024-02-01 -> window rolls to Q1..Q4'23 = 20+30+40+50
    assert s.loc["2024-02-02"] == 140


def test_quarter_invisible_before_its_filed_date():
    facts = [
        _q("CY2022Q4", "2022-12-31", 10, "2023-02-01"),
        _q("CY2023Q1", "2023-03-31", 20, "2023-05-01"),
        _q("CY2023Q2", "2023-06-30", 30, "2023-08-01"),
        # Q3 exists in the data but was filed late
        _q("CY2023Q3", "2023-09-30", 40, "2023-12-15"),
    ]
    s = pit_ttm_series(facts, PRICE_DATES)
    # on 2023-12-01 only three quarters are visible -> not a full year -> NaN
    assert np.isnan(s.loc["2023-12-01"])
    # after the late Q3 filing the TTM appears
    assert s.loc["2023-12-18"] == 100


def test_restatement_uses_latest_filing_at_or_before_t():
    facts = [
        _q("CY2022Q4", "2022-12-31", 10, "2023-02-01"),
        _q("CY2023Q1", "2023-03-31", 20, "2023-05-01"),
        _q("CY2023Q2", "2023-06-30", 30, "2023-08-01"),
        _q("CY2023Q3", "2023-09-30", 40, "2023-11-01"),
        # amended Q3 filed later with a corrected value
        _q("CY2023Q3", "2023-09-30", 44, "2024-03-01", form="10-Q/A"),
    ]
    s = pit_ttm_series(facts, PRICE_DATES)
    # before the amendment, the original Q3=40 is in force
    assert s.loc["2024-01-15"] == 100
    # at/after the amendment, Q3=44 -> 10+20+30+44
    assert s.loc["2024-03-05"] == 104


# --- instant ----------------------------------------------------------------

def test_instant_takes_latest_end_known_by_t():
    facts = [
        _instant("2023-03-31", 1000, "2023-05-01"),
        _instant("2023-06-30", 1100, "2023-08-01"),
        _instant("2023-09-30", 1200, "2023-11-01"),
    ]
    s = pit_instant_series(facts, PRICE_DATES)
    assert s.loc["2023-09-01"] == 1100          # Q2 balance, Q3 not yet filed
    assert s.loc["2023-11-15"] == 1200          # Q3 balance now known
    assert np.isnan(s.loc["2023-03-31"])        # nothing filed yet


def test_instant_restatement_prefers_latest_filing():
    facts = [
        _instant("2023-06-30", 1100, "2023-08-01"),
        _instant("2023-06-30", 1150, "2024-01-10", form="10-K/A"),
    ]
    s = pit_instant_series(facts, PRICE_DATES)
    assert s.loc["2023-09-01"] == 1100
    assert s.loc["2024-02-01"] == 1150


# --- panel assembly + aliasing ---------------------------------------------

def _facts_block(concepts: dict) -> dict:
    return {"facts": {"us-gaap": concepts, "dei": {}}}


def test_panel_has_contract_columns_and_price_index():
    concepts = {
        "NetIncomeLoss": {"units": {"USD": [
            _q("CY2022Q4", "2022-12-31", 10, "2023-02-01"),
            _q("CY2023Q1", "2023-03-31", 20, "2023-05-01"),
            _q("CY2023Q2", "2023-06-30", 30, "2023-08-01"),
            _q("CY2023Q3", "2023-09-30", 40, "2023-11-01"),
        ]}},
        "GrossProfit": {"units": {"USD": [
            _q("CY2022Q4", "2022-12-31", 5, "2023-02-01"),
            _q("CY2023Q1", "2023-03-31", 6, "2023-05-01"),
            _q("CY2023Q2", "2023-06-30", 7, "2023-08-01"),
            _q("CY2023Q3", "2023-09-30", 8, "2023-11-01"),
        ]}},
        "Assets": {"units": {"USD": [
            _instant("2023-06-30", 9000, "2023-08-01"),
            _instant("2023-09-30", 9500, "2023-11-01"),
        ]}},
        "StockholdersEquity": {"units": {"USD": [
            _instant("2023-06-30", 4000, "2023-08-01"),
            _instant("2023-09-30", 4200, "2023-11-01"),
        ]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [
            _instant("2023-06-30", 100, "2023-08-01"),
            _instant("2023-09-30", 100, "2023-11-01"),
        ]}},
    }
    panel = build_pit_fundamental_panel(_facts_block(concepts), PRICE_DATES)
    assert list(panel.columns) == list(CONTRACT_COLUMNS)
    assert panel.index.equals(PRICE_DATES)
    assert panel.loc["2023-11-15", "Net Income_TTM"] == 100
    assert panel.loc["2023-11-15", "Gross Profit_TTM"] == 26
    assert panel.loc["2023-11-15", "Total Assets"] == 9500
    assert panel.loc["2023-11-15", "Stockholders Equity"] == 4200


def test_equity_falls_back_to_second_alias():
    concepts = {
        "NetIncomeLoss": {"units": {"USD": []}},
        # primary StockholdersEquity absent; the noncontrolling alias is present
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
            "units": {"USD": [_instant("2023-09-30", 4200, "2023-11-01")]}
        },
    }
    panel = build_pit_fundamental_panel(_facts_block(concepts), PRICE_DATES)
    assert panel.loc["2023-11-15", "Stockholders Equity"] == 4200


def test_shares_falls_back_to_dei_alias():
    block = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": []}}},
                       "dei": {"EntityCommonStockSharesOutstanding": {
                           "units": {"shares": [_instant("2023-09-30", 250, "2023-11-01")]}}}}}
    panel = build_pit_fundamental_panel(block, PRICE_DATES)
    assert panel.loc["2023-11-15", "Share Issued"] == 250


def test_missing_concept_yields_nan_column_not_crash():
    concepts = {"NetIncomeLoss": {"units": {"USD": [
        _q("CY2022Q4", "2022-12-31", 10, "2023-02-01"),
        _q("CY2023Q1", "2023-03-31", 20, "2023-05-01"),
        _q("CY2023Q2", "2023-06-30", 30, "2023-08-01"),
        _q("CY2023Q3", "2023-09-30", 40, "2023-11-01"),
    ]}}}
    panel = build_pit_fundamental_panel(_facts_block(concepts), PRICE_DATES)
    # Gross Profit / Assets / Equity / Shares absent -> all-NaN but present
    assert panel["Gross Profit_TTM"].isna().all()
    assert panel["Total Assets"].isna().all()
    assert list(panel.columns) == list(CONTRACT_COLUMNS)
