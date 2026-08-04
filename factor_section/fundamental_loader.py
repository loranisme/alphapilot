"""
fundamental_loader.py
---------------------
Fetch, cache, and align quarterly fundamental data from yfinance for
a universe of tickers.

Methodology
-----------
* Uses yfinance `Ticker.quarterly_income_stmt`, `quarterly_balance_sheet`,
  `quarterly_cash_flow` to obtain point-in-time quarterly figures.
* Applies a **45-business-day earnings announcement lag** before making any
  fundamental value visible to the factor engine, eliminating look-ahead bias.
* Computes trailing-twelve-month (TTM) aggregates for flow items (revenue,
  net income, gross profit, operating cash flow, R&D).
* Persists a single Parquet cache per ticker under `data/fundamental_cache/`
  so repeated runs are fast.
* Thread-pool downloads (default 8 workers) with a retry wrapper.
"""

from __future__ import annotations

import os
import time
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ANNOUNCEMENT_LAG_BD = 45          # business-day lag before data is "known"
_TTM_QUARTERS = 4                  # quarters to sum for TTM flow items
_DEFAULT_WORKERS = 8
_RETRY_ATTEMPTS = 3
_RETRY_SLEEP = 2.0                 # seconds between yfinance retries

# Flow items that should be summed over TTM; all others are taken as-of last Q
# NOTE: these must match yfinance's actual row labels exactly, which are
# Title Case WITH SPACES (e.g. "Net Income", not "NetIncome") — using the
# wrong casing here silently means zero items ever match, so no _TTM
# columns are produced and every downstream fundamental factor is empty.
_TTM_FLOW_ITEMS = {
    "Total Revenue", "Gross Profit", "Net Income",
    "Operating Cash Flow", "Capital Expenditure",
}


# ---------------------------------------------------------------------------
# Low-level fetch helpers
# ---------------------------------------------------------------------------

def _fetch_ticker_fundamentals(ticker: str) -> Optional[pd.DataFrame]:
    """
    Pull quarterly income statement + balance sheet + cash flow for `ticker`.

    Returns a long-format DataFrame with columns:
        [report_date, item, value]

    Returns None on failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance is required: pip install yfinance")

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            t = yf.Ticker(ticker)

            frames = []
            for attr in ("quarterly_income_stmt", "quarterly_balance_sheet", "quarterly_cash_flow"):
                raw = getattr(t, attr, None)
                if raw is None or raw.empty:
                    continue
                # yfinance returns items as rows, dates as columns
                df_long = raw.T.reset_index().rename(columns={"index": "report_date"})
                df_long = df_long.melt(id_vars="report_date", var_name="item", value_name="value")
                frames.append(df_long)

            if not frames:
                log.warning("No fundamental data returned for %s", ticker)
                return None

            combined = pd.concat(frames, ignore_index=True)
            combined["report_date"] = pd.to_datetime(combined["report_date"], utc=False).dt.normalize()
            combined = combined.dropna(subset=["value"])
            combined["value"] = pd.to_numeric(combined["value"], errors="coerce")
            combined = combined.dropna(subset=["value"])
            return combined

        except Exception:
            if attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(_RETRY_SLEEP * (attempt + 1))
            else:
                log.error("Failed to fetch fundamentals for %s:\n%s", ticker, traceback.format_exc())
                return None
    return None


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------

def _cache_path(ticker: str, cache_dir: Path) -> Path:
    return cache_dir / f"{ticker}_fundamental.parquet"


def _load_from_cache(ticker: str, cache_dir: Path, max_age_days: int = 7) -> Optional[pd.DataFrame]:
    p = _cache_path(ticker, cache_dir)
    if not p.exists():
        return None
    age = (time.time() - p.stat().st_mtime) / 86400
    if age > max_age_days:
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        return None


def _save_to_cache(df: pd.DataFrame, ticker: str, cache_dir: Path):
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_cache_path(ticker, cache_dir), index=False)


# ---------------------------------------------------------------------------
# TTM + announcement-lag pivot
# ---------------------------------------------------------------------------

def _build_fundamental_panel(raw_long: pd.DataFrame) -> pd.DataFrame:
    """
    From long-format (report_date, item, value), build a wide DataFrame
    indexed by report_date with TTM flow aggregates where applicable.

    Duplicate (report_date, item) entries are resolved by taking the last value.
    """
    # Pivot to (report_date x item)
    pivot = (
        raw_long
        .sort_values("report_date")
        .drop_duplicates(subset=["report_date", "item"], keep="last")
        .pivot(index="report_date", columns="item", values="value")
        .sort_index()
    )

    ttm_items = [c for c in pivot.columns if c in _TTM_FLOW_ITEMS]
    level_items = [c for c in pivot.columns if c not in _TTM_FLOW_ITEMS]

    result = pivot[level_items].copy()

    for item in ttm_items:
        # Sum the last 4 available quarters (forward-fill gaps)
        result[f"{item}_TTM"] = (
            pivot[item]
            .ffill(limit=2)
            .rolling(window=_TTM_QUARTERS, min_periods=1)
            .sum()
        )

    return result


def _apply_announcement_lag(panel: pd.DataFrame, price_dates: pd.DatetimeIndex) -> pd.DataFrame:
    """
    Shift each report_date by `_ANNOUNCEMENT_LAG_BD` business days to get the
    'available_date', then forward-fill into the price calendar.

    Returns a DataFrame aligned to `price_dates` so that fundamental values
    only appear on the date they are actually known to the market.
    """
    if panel.empty:
        return pd.DataFrame(index=price_dates)

    # Compute available dates
    panel = panel.copy()
    panel.index = panel.index + pd.offsets.BusinessDay(_ANNOUNCEMENT_LAG_BD)

    # Normalize timezone awareness so panel.index and price_dates can be
    # unioned/sorted together (report_date from yfinance is tz-naive; the
    # price calendar is typically tz-aware America/New_York).
    if price_dates.tz is not None:
        if panel.index.tz is None:
            panel.index = panel.index.tz_localize(price_dates.tz)
        else:
            panel.index = panel.index.tz_convert(price_dates.tz)
    elif panel.index.tz is not None:
        panel.index = panel.index.tz_localize(None)

    # Reindex to price calendar, forward-fill (no future leak because we lagged)
    aligned = panel.reindex(panel.index.union(price_dates)).sort_index()
    aligned = aligned.ffill()
    aligned = aligned.reindex(price_dates)
    return aligned


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_fundamental_panel(
    tickers: List[str],
    price_dates: pd.DatetimeIndex,
    cache_dir: Optional[Path] = None,
    max_workers: int = _DEFAULT_WORKERS,
    cache_max_age_days: int = 7,
) -> Dict[str, pd.DataFrame]:
    """
    Download (or load from cache) quarterly fundamental data for each ticker.

    Parameters
    ----------
    tickers : list of str
        Universe of ticker symbols.
    price_dates : DatetimeIndex
        Trading-day calendar to align fundamentals to.
    cache_dir : Path, optional
        Directory for parquet cache files.  Defaults to
        ``<project_root>/data/fundamental_cache/``.
    max_workers : int
        Thread pool size for parallel yfinance downloads.
    cache_max_age_days : int
        Number of days before cached data is refreshed.

    Returns
    -------
    dict  ticker -> DataFrame(index=price_dates, columns=fundamental_items)
    """
    if cache_dir is None:
        # Try to locate project root by climbing to where data/ lives
        here = Path(__file__).resolve().parent
        for _ in range(4):
            if (here / "data").exists():
                cache_dir = here / "data" / "fundamental_cache"
                break
            here = here.parent
        else:
            cache_dir = Path("data") / "fundamental_cache"

    cache_dir.mkdir(parents=True, exist_ok=True)

    def _process_one(ticker: str) -> Tuple[str, Optional[pd.DataFrame]]:
        cached = _load_from_cache(ticker, cache_dir, cache_max_age_days)
        if cached is not None:
            panel = _build_fundamental_panel(cached)
            aligned = _apply_announcement_lag(panel, price_dates)
            return ticker, aligned

        raw = _fetch_ticker_fundamentals(ticker)
        if raw is None:
            return ticker, None

        _save_to_cache(raw, ticker, cache_dir)
        panel = _build_fundamental_panel(raw)
        aligned = _apply_announcement_lag(panel, price_dates)
        return ticker, aligned

    results: Dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process_one, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, aligned = future.result()
            if aligned is not None and not aligned.empty:
                results[ticker] = aligned
            else:
                log.warning("No fundamental data for %s", ticker)

    log.info(
        "Fundamental panel loaded: %d/%d tickers with data",
        len(results), len(tickers),
    )
    return results


def build_fundamental_factor_panel(
    fundamental_data: Dict[str, pd.DataFrame],
    price_matrix: pd.DataFrame,
) -> Dict[str, pd.DataFrame]:
    """
    From per-ticker fundamental DataFrames and a (date x ticker) close price
    matrix, compute cross-sectionally usable factor series for each fundamental
    item.

    Returns a dict: factor_name -> (date x ticker) DataFrame.
    """
    factor_names = set()
    for df in fundamental_data.values():
        factor_names.update(df.columns)

    panels: Dict[str, Dict[str, pd.Series]] = {f: {} for f in factor_names}

    for ticker, df in fundamental_data.items():
        if ticker not in price_matrix.columns:
            continue
        common_idx = df.index.intersection(price_matrix.index)
        for fname in df.columns:
            panels[fname][ticker] = df.loc[common_idx, fname]

    result = {}
    for fname, series_dict in panels.items():
        if len(series_dict) < 10:
            continue
        result[fname] = pd.DataFrame(series_dict, index=price_matrix.index)

    return result
