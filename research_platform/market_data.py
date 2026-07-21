"""Causal OHLCV views for factor research."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


@dataclass(frozen=True)
class ResearchOHLCVBundle:
    frames: dict[str, pd.DataFrame]
    metadata: dict[str, object]


def sanitize_research_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a sorted, timezone-naive OHLCV frame without calendar filling."""
    missing = set(OHLCV_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"missing required OHLCV columns: {sorted(missing)}")
    result = frame.loc[:, OHLCV_COLUMNS].copy()
    result.index = pd.to_datetime(result.index, utc=True, errors="coerce")
    result.index = result.index.tz_convert(None).normalize()
    result = result.loc[result.index.notna()]
    result = result.loc[~result.index.duplicated(keep="last")].sort_index()
    result = result.apply(pd.to_numeric, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    prices = result.loc[:, ["Open", "High", "Low", "Close"]]
    invalid_prices = (
        prices.le(0).any(axis=1)
        | result["High"].lt(result[["Open", "Close"]].max(axis=1))
        | result["Low"].gt(result[["Open", "Close"]].min(axis=1))
        | result["Low"].gt(result["High"])
    )
    result.loc[invalid_prices, ["Open", "High", "Low", "Close"]] = np.nan
    result.loc[result["Volume"].le(0), "Volume"] = np.nan
    return result


def load_research_ohlcv(
    raw_dir: str | Path,
    tickers: Iterable[str],
    start=None,
    end=None,
) -> ResearchOHLCVBundle:
    """Load requested raw OHLCV files without time-series imputation."""
    directory = Path(raw_dir)
    requested = tuple(dict.fromkeys(str(ticker) for ticker in tickers))
    frames: dict[str, pd.DataFrame] = {}
    sources: dict[str, dict[str, object]] = {}
    start_ts = pd.Timestamp(start).normalize() if start is not None else None
    end_ts = pd.Timestamp(end).normalize() if end is not None else None

    for ticker in requested:
        path = directory / f"{ticker}_20years.csv"
        if not path.exists():
            continue
        raw = pd.read_csv(path)
        if "Date" not in raw.columns:
            continue
        raw = raw.set_index("Date")
        try:
            clean = sanitize_research_ohlcv(raw)
        except ValueError:
            continue
        if start_ts is not None:
            clean = clean.loc[clean.index >= start_ts]
        if end_ts is not None:
            clean = clean.loc[clean.index <= end_ts]
        if clean.empty:
            continue
        frames[ticker] = clean
        sources[ticker] = {
            "path": str(path),
            "rows": int(len(clean)),
            "start": clean.index.min(),
            "end": clean.index.max(),
            "invalid_price_rows": int(clean[list(OHLCV_COLUMNS[:4])].isna().all(axis=1).sum()),
            "missing_volume_rows": int(clean["Volume"].isna().sum()),
        }

    if not frames:
        raise FileNotFoundError("no usable requested raw OHLCV files were found")
    metadata: dict[str, object] = {
        "requested_tickers": len(requested),
        "loaded_tickers": len(frames),
        "missing_tickers": sorted(set(requested).difference(frames)),
        "adjustment_semantics": "source prices used as stored",
        "calendar_fill": False,
        "future_backfill": False,
        "sources": sources,
    }
    return ResearchOHLCVBundle(frames=frames, metadata=metadata)
