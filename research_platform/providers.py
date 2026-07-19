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

import pandas as pd

from .contracts import ClassificationPanel


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
