"""Validated data contracts shared by the research pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

import pandas as pd


def _validate_frame(frame: pd.DataFrame, name: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{name} must be a DataFrame")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError(f"{name} index must be a DatetimeIndex")
    if not frame.index.is_unique:
        raise ValueError(f"{name} dates must be unique")
    if not frame.index.is_monotonic_increasing:
        raise ValueError(f"{name} dates must be sorted")


def dataframe_fingerprint(frame: pd.DataFrame) -> str:
    """Return a deterministic content hash including index and columns."""
    _validate_frame(frame, "frame")
    row_hashes = pd.util.hash_pandas_object(frame, index=True).values.tobytes()
    column_hashes = pd.util.hash_pandas_object(
        pd.Index([repr(column) for column in frame.columns])
    ).values.tobytes()
    return sha256(row_hashes + column_hashes).hexdigest()


@dataclass(frozen=True)
class MarketDataBundle:
    ohlcv: pd.DataFrame
    source: str
    tradable: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        _validate_frame(self.ohlcv, "ohlcv")
        if self.tradable is not None:
            _validate_frame(self.tradable, "tradable")
            if not self.tradable.index.equals(self.ohlcv.index):
                raise ValueError("tradable and ohlcv dates must be aligned")

    @property
    def fingerprint(self) -> str:
        return dataframe_fingerprint(self.ohlcv)


@dataclass(frozen=True)
class UniversePanel:
    membership: pd.DataFrame
    source: str
    quality: str

    def __post_init__(self) -> None:
        _validate_frame(self.membership, "membership")

    def asof(self, date: Any) -> pd.Series:
        eligible = self.membership.loc[: pd.Timestamp(date)]
        if eligible.empty:
            raise KeyError(f"no universe snapshot on or before {date}")
        return eligible.iloc[-1].fillna(False).astype(bool)


@dataclass(frozen=True)
class ClassificationPanel:
    classification: pd.DataFrame
    taxonomy: str
    source: str
    quality: str

    def __post_init__(self) -> None:
        _validate_frame(self.classification, "classification")

    def asof(self, date: Any) -> pd.Series:
        eligible = self.classification.loc[: pd.Timestamp(date)]
        if eligible.empty:
            raise KeyError(f"no classification snapshot on or before {date}")
        return eligible.iloc[-1]

    def coverage(self, date: Any, universe: pd.Series | None = None) -> float:
        values = self.asof(date)
        if universe is not None:
            active = universe.reindex(values.index).fillna(False).astype(bool)
            values = values.loc[active]
        return float(values.notna().mean()) if len(values) else 0.0


@dataclass(frozen=True)
class FactorPanel:
    raw: pd.DataFrame
    standardized: pd.DataFrame | None = None
    neutralized: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        _validate_frame(self.raw, "raw")
        for name in ("standardized", "neutralized"):
            frame = getattr(self, name)
            if frame is None:
                continue
            _validate_frame(frame, name)
            if not frame.index.equals(self.raw.index) or not frame.columns.equals(self.raw.columns):
                raise ValueError(f"{name} must be aligned with raw")


@dataclass
class ExperimentResult:
    metrics: dict[str, Any] = field(default_factory=dict)
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
