"""Free, cache-first providers for point-in-time research metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .contracts import ClassificationPanel, UniversePanel


def rebuild_membership(
    current: set[str],
    changes: pd.DataFrame,
    dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Reconstruct historical membership by reversing effective-dated changes."""
    required = {"effective_date", "added", "removed"}
    missing = required.difference(changes.columns)
    if missing:
        raise ValueError(f"changes missing columns: {sorted(missing)}")
    events = changes.copy()
    events["effective_date"] = pd.to_datetime(events["effective_date"])
    events = events.sort_values("effective_date", ascending=False)

    snapshots: dict[pd.Timestamp, set[str]] = {}
    for date in sorted(pd.DatetimeIndex(dates).unique()):
        members = set(current)
        later = events.loc[events["effective_date"] > date]
        for row in later.itertuples(index=False):
            if isinstance(row.added, str) and row.added:
                members.discard(row.added)
            if isinstance(row.removed, str) and row.removed:
                members.add(row.removed)
        snapshots[pd.Timestamp(date)] = members

    all_members = sorted(set(current).union(*(members for members in snapshots.values())))
    return pd.DataFrame(
        {
            ticker: [ticker in snapshots[date] for date in sorted(snapshots)]
            for ticker in all_members
        },
        index=pd.DatetimeIndex(sorted(snapshots)),
        dtype=bool,
    )


class CachedJsonProvider:
    """JSON downloader with content-digest verification and source metadata."""

    def __init__(self, cache_dir: str | Path, user_agent: str):
        if not user_agent.strip():
            raise ValueError("user_agent must identify the research client")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent

    def _paths(self, cache_name: str) -> tuple[Path, Path]:
        data_path = self.cache_dir / cache_name
        meta_path = self.cache_dir / f"{cache_name}.meta.json"
        return data_path, meta_path

    @staticmethod
    def _digest(data: bytes) -> str:
        return sha256(data).hexdigest()

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        os.replace(temporary, path)

    def save(self, cache_name: str, payload: dict | list, source_url: str) -> None:
        data_path, meta_path = self._paths(cache_name)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        metadata = {
            "source_url": source_url,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sha256": self._digest(encoded),
        }
        self._atomic_write(data_path, encoded)
        self._atomic_write(
            meta_path,
            json.dumps(metadata, sort_keys=True, indent=2).encode("utf-8"),
        )

    def load(self, cache_name: str):
        data_path, meta_path = self._paths(cache_name)
        if not data_path.exists() or not meta_path.exists():
            raise FileNotFoundError(f"missing verified cache for {cache_name}")
        encoded = data_path.read_bytes()
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if self._digest(encoded) != metadata.get("sha256"):
            raise ValueError(f"cache digest mismatch for {cache_name}")
        return json.loads(encoded)

    def fetch(self, url: str, cache_name: str, timeout: float = 30.0):
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.save(cache_name, payload, source_url=url)
            return payload
        except Exception:
            return self.load(cache_name)


def classification_from_records(
    records: Iterable[dict],
    taxonomy: str,
    source: str,
    quality: str,
) -> ClassificationPanel:
    """Build an effective-dated classification panel with forward-filled values."""
    frame = pd.DataFrame(records)
    required = {"effective_date", "ticker", "classification"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"classification records missing columns: {sorted(missing)}")
    frame["effective_date"] = pd.to_datetime(frame["effective_date"])
    panel = frame.pivot_table(
        index="effective_date",
        columns="ticker",
        values="classification",
        aggfunc="last",
    ).sort_index()
    panel = panel.ffill()
    panel.index.name = None
    panel.columns.name = None
    return ClassificationPanel(panel, taxonomy=taxonomy, source=source, quality=quality)


def parse_constituents_snapshot(table: pd.DataFrame) -> pd.DataFrame:
    """Normalize a Wikipedia-style S&P constituents table.

    Returns a frame indexed by ticker with columns ``sector`` (object) and
    ``date_added`` (tz-naive ``Timestamp``; ``NaT`` when the source omits or
    cannot parse it). Ticker dots are converted to dashes to match the
    price/factor column convention used elsewhere in the platform.
    """
    required = {"Symbol", "GICS Sector"}
    missing = required.difference(table.columns)
    if missing:
        raise ValueError(f"constituents snapshot missing columns: {sorted(required)}")
    frame = table.copy()
    frame["Symbol"] = frame["Symbol"].astype(str).str.replace(".", "-", regex=False)
    frame = frame.drop_duplicates("Symbol").set_index("Symbol")
    if "Date added" in frame.columns:
        date_added = pd.to_datetime(frame["Date added"], errors="coerce")
    else:
        date_added = pd.Series(pd.NaT, index=frame.index)
    return pd.DataFrame(
        {"sector": frame["GICS Sector"].astype(object), "date_added": date_added}
    )


def membership_from_date_added(
    date_added: pd.Series, dates: pd.DatetimeIndex
) -> pd.DataFrame:
    """Additions-only point-in-time membership from a ``date_added`` column.

    A ticker is a member on every date on or after its ``date_added``. Tickers
    with a missing ``date_added`` are treated as members throughout: the source
    snapshot lists only *current* members, so this path cannot reconstruct
    historical removals (that requires an effective-dated changes file — see
    :func:`rebuild_membership`). It still removes forward look-ahead on
    additions, which is the dominant bias when applying a current index to the
    past.
    """
    ordered = pd.DatetimeIndex(sorted(pd.DatetimeIndex(dates).unique()))
    columns = list(date_added.index)
    if not columns:
        return pd.DataFrame(index=ordered, dtype=bool)
    filled = pd.to_datetime(date_added).fillna(ordered.min())
    matrix = ordered.values[:, None] >= filled.values[None, :]
    return pd.DataFrame(matrix, index=ordered, columns=columns, dtype=bool)


def build_pit_universe(
    constituents: pd.DataFrame,
    dates: pd.DatetimeIndex,
    changes: pd.DataFrame | None = None,
    source: str = "wikipedia_snapshot",
) -> tuple[UniversePanel, ClassificationPanel, dict]:
    """Assemble a point-in-time :class:`UniversePanel` plus a current-snapshot
    :class:`ClassificationPanel` from a parsed constituents table.

    When an effective-dated ``changes`` frame is supplied (columns
    ``effective_date, added, removed``), membership is reconstructed with full
    add/remove history via :func:`rebuild_membership` (survivorship-free).
    Otherwise membership is additions-only from ``date_added``. GICS sector is
    only ever available as a current snapshot here, so ``classification`` is that
    snapshot; the returned metadata records the honest PIT level achieved.
    """
    ordered = pd.DatetimeIndex(sorted(pd.DatetimeIndex(dates).unique()))
    current = set(constituents.index)
    if changes is not None and len(changes):
        membership = (
            rebuild_membership(current, changes, ordered)
            .reindex(index=ordered, fill_value=False)
            .astype(bool)
        )
        basis = "changes_reversed"
    else:
        membership = membership_from_date_added(constituents["date_added"], ordered)
        basis = "date_added_additions_only"
    universe = UniversePanel(membership=membership, source=source, quality=basis)
    classification = ClassificationPanel(
        classification=pd.DataFrame(
            [constituents["sector"].to_dict()],
            index=pd.DatetimeIndex([ordered.min()]),
        ),
        taxonomy="GICS",
        source=source,
        quality="current_snapshot",
    )
    metadata = {
        "universe_membership_basis": basis,
        "membership_point_in_time": basis == "changes_reversed",
        "classification_point_in_time": False,
        "universe_size_start": int(membership.iloc[0].sum()) if len(membership) else 0,
        "universe_size_end": int(membership.iloc[-1].sum()) if len(membership) else 0,
    }
    return universe, classification, metadata


def pit_industry_panel(
    classification: ClassificationPanel,
    universe: UniversePanel,
    dates: pd.DatetimeIndex,
    columns: Iterable[str],
) -> tuple[pd.DataFrame, float]:
    """Broadcast the classification snapshot over ``dates`` and mask non-members.

    Returns ``(industry, coverage)`` where ``industry`` is a ``dates x columns``
    object frame carrying ``NaN`` wherever the name was not an index member
    as-of the date, and ``coverage`` is the fraction of *member* cells with a
    known sector — the honest denominator for the classification-coverage gate
    once membership is point-in-time.
    """
    ordered = pd.DatetimeIndex(dates)
    cols = list(columns)
    sectors = classification.asof(ordered.max()).reindex(cols)
    broadcast = pd.DataFrame(
        np.tile(sectors.to_numpy(), (len(ordered), 1)), index=ordered, columns=cols
    )
    member = universe.membership.reindex(
        index=ordered, columns=cols, fill_value=False
    ).astype(bool)
    member_cells = int(member.to_numpy().sum())
    known = broadcast.notna() & member
    coverage = float(known.to_numpy().sum() / member_cells) if member_cells else 0.0
    return broadcast.where(member), coverage
