"""As-first-reported point-in-time transform of SEC EDGAR companyfacts.

Pure functions, no network. Input is a parsed ``companyfacts`` JSON dict (one
ticker) as returned by ``data.sec.gov/api/xbrl/companyfacts/CIK##########.json``;
output is a per-ticker ``DataFrame(index=price_dates, columns=CONTRACT_COLUMNS)``
in the exact shape ``FundamentalFactorDesigner`` consumes, so the existing factor
code runs unchanged.

Point-in-time semantics. Every EDGAR fact row carries ``filed`` — the actual SEC
filing date, i.e. the first date the market knew the value. At a price-date ``t``
only facts with ``filed <= t`` are visible (no look-ahead). We use SEC's
calendar-quarter ``frame`` tags (e.g. ``CY2023Q3``) which give one canonical value
per calendar quarter and, crucially, a computed Q4 even though a 10-K carries no
standalone Q4 income statement. This is *as-first-reported* PIT: frame rows carry
the first canonical filing's date, and we deliberately do not chase later
amendments except where a later filing for the same period is the one in force at
``t`` (handled generally by the ``filed <= t`` selection).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

_CALENDAR_QUARTER = re.compile(r"^CY\d{4}Q\d$")
_TTM_QUARTERS = 4


@dataclass(frozen=True)
class ConceptSpec:
    column: str
    kind: str  # "flow" or "instant"
    aliases: tuple[str, ...]
    taxonomy: str = "us-gaap"


# Output columns, in the order FundamentalFactorDesigner expects to find them.
CONCEPT_SPECS: tuple[ConceptSpec, ...] = (
    ConceptSpec("Net Income_TTM", "flow", ("NetIncomeLoss",)),
    ConceptSpec("Gross Profit_TTM", "flow", ("GrossProfit",)),
    ConceptSpec(
        "Stockholders Equity",
        "instant",
        (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    ),
    ConceptSpec("Total Assets", "instant", ("Assets",)),
    ConceptSpec(
        "Share Issued",
        "instant",
        ("CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"),
    ),
)
CONTRACT_COLUMNS: tuple[str, ...] = tuple(spec.column for spec in CONCEPT_SPECS)


def _first_unit_rows(concept: dict) -> list[dict]:
    """Flatten a concept's ``units`` mapping to a single list of fact rows."""
    units = concept.get("units", {}) if concept else {}
    rows: list[dict] = []
    for unit_rows in units.values():
        rows.extend(unit_rows)
    return rows


def _resolve_concept(facts_json: dict, spec: ConceptSpec) -> list[dict]:
    """Return fact rows for the first alias present across us-gaap then dei."""
    facts = facts_json.get("facts", {})
    taxonomies = [facts.get("us-gaap", {}), facts.get("dei", {})]
    for alias in spec.aliases:
        for taxonomy in taxonomies:
            if alias in taxonomy:
                rows = _first_unit_rows(taxonomy[alias])
                if rows:
                    return rows
    return []


def pit_ttm_series(concept_facts: list[dict], price_dates: pd.DatetimeIndex) -> pd.Series:
    """Trailing-4-quarter sum from calendar-quarter frames, as-first-reported PIT.

    A quarter becomes visible on its ``filed`` date; at each price-date the TTM is
    the sum of the four most recent calendar quarters visible by then, or NaN if
    fewer than four are visible. The value is held until a newer quarter is filed.
    """
    quarters = _quarter_table(concept_facts)
    if quarters.empty:
        return pd.Series(np.nan, index=price_dates, dtype=float)
    # TTM only changes on filing dates; compute there and forward-fill. At each
    # event the in-force value per quarter_end is its latest filing so far.
    events = np.sort(quarters["filed"].unique())
    values = []
    for e in events:
        visible = quarters[quarters["filed"] <= e]
        in_force = (
            visible.sort_values("filed").groupby("end", as_index=False).last().sort_values("end")
        )
        values.append(
            float(in_force["val"].iloc[-_TTM_QUARTERS:].sum())
            if len(in_force) >= _TTM_QUARTERS
            else np.nan
        )
    return _forward_fill_events(events, values, price_dates)


def pit_instant_series(concept_facts: list[dict], price_dates: pd.DatetimeIndex) -> pd.Series:
    """Latest balance whose ``end`` is known (``filed <= t``), forward-filled."""
    rows = []
    for row in concept_facts:
        end = row.get("end")
        filed = row.get("filed")
        val = row.get("val")
        if end is None or filed is None or val is None:
            continue
        rows.append((pd.Timestamp(end), pd.Timestamp(filed), float(val)))
    if not rows:
        return pd.Series(np.nan, index=price_dates, dtype=float)
    table = pd.DataFrame(rows, columns=["end", "filed", "val"])
    # The balance in force only changes on filing dates; evaluate there, ffill.
    events = np.sort(table["filed"].unique())
    values = []
    for e in events:
        visible = table[table["filed"] <= e]
        latest_end = visible["end"].max()
        in_force = visible[visible["end"] == latest_end].sort_values("filed")
        values.append(float(in_force["val"].iloc[-1]))
    return _forward_fill_events(events, values, price_dates)


def _forward_fill_events(events, values, price_dates: pd.DatetimeIndex) -> pd.Series:
    """Step function: value at t is the latest event value with event <= t."""
    series = pd.Series(values, index=pd.DatetimeIndex(events), dtype=float).sort_index()
    return series.reindex(series.index.union(price_dates)).ffill().reindex(price_dates)


def _quarter_table(concept_facts: list[dict]) -> pd.DataFrame:
    """Calendar-quarter flow facts as columns [frame, end, filed, val]."""
    rows = []
    for row in concept_facts:
        frame = row.get("frame")
        if not frame or not _CALENDAR_QUARTER.match(frame):
            continue
        end = row.get("end")
        filed = row.get("filed")
        val = row.get("val")
        if end is None or filed is None or val is None:
            continue
        rows.append((frame, pd.Timestamp(end), pd.Timestamp(filed), float(val)))
    if not rows:
        return pd.DataFrame(columns=["frame", "end", "filed", "val"])
    return pd.DataFrame(rows, columns=["frame", "end", "filed", "val"])


def build_pit_fundamental_panel(
    companyfacts_json: dict, price_dates: pd.DatetimeIndex
) -> pd.DataFrame:
    """One ticker's PIT fundamental panel with exactly ``CONTRACT_COLUMNS``."""
    price_dates = pd.DatetimeIndex(price_dates)
    columns = {}
    for spec in CONCEPT_SPECS:
        rows = _resolve_concept(companyfacts_json, spec)
        if spec.kind == "flow":
            columns[spec.column] = pit_ttm_series(rows, price_dates)
        else:
            columns[spec.column] = pit_instant_series(rows, price_dates)
    panel = pd.DataFrame(columns, index=price_dates)
    return panel.loc[:, list(CONTRACT_COLUMNS)]


def build_pit_fundamentals(
    raw_by_ticker: dict[str, dict], price_dates: pd.DatetimeIndex
) -> dict[str, pd.DataFrame]:
    """Map each ticker's companyfacts JSON to a PIT fundamental panel."""
    price_dates = pd.DatetimeIndex(price_dates)
    panels: dict[str, pd.DataFrame] = {}
    for ticker, facts_json in raw_by_ticker.items():
        if not facts_json:
            continue
        panel = build_pit_fundamental_panel(facts_json, price_dates)
        if panel.notna().any().any():
            panels[ticker] = panel
    return panels
