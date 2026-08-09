"""Program-level experiment ledger and cross-experiment multiple-testing.

The significance module (:mod:`research_platform.significance`) controls the
family-wise error rate *within* a single IC grid. It cannot see the other
experiments the research program has already run, so the true rate of false
discoveries *across* the program is uncontrolled: run enough independently
"significant" experiments and one will clear a within-grid gate by chance.

This module closes that gap with an append-only ledger. Each experiment logs its
already-family-wise-corrected global p-value (e.g. the Phase B / Phase C studentized
max-T global p); the ledger then re-corrects across every logged experiment so the
verdict ``passed_program`` answers "does this survive once we account for *every*
hypothesis the program has tested?".

Pure statistics plus a small JSONL persistence layer — no network, no heavy deps.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ExperimentRecord:
    """One logged experiment and its already-within-grid-corrected result."""

    name: str
    family_wise_p: float
    n_hypotheses: int
    passed_local: bool
    timestamp: str = ""
    family: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.family_wise_p <= 1.0:
            raise ValueError("family_wise_p must be within [0, 1]")
        if self.n_hypotheses < 1:
            raise ValueError("n_hypotheses must be at least 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def bonferroni(pvalues: list[float]) -> list[float]:
    """Bonferroni-adjusted p-values (clipped to 1)."""
    n = len(pvalues)
    return [min(1.0, p * n) for p in pvalues]


def benjamini_hochberg(pvalues: list[float]) -> list[float]:
    """Benjamini-Hochberg (FDR) adjusted p-values, order preserved."""
    n = len(pvalues)
    if n == 0:
        return []
    order = np.argsort(pvalues)
    ranked = np.asarray(pvalues, dtype=float)[order]
    adjusted = ranked * n / (np.arange(n) + 1)
    # Enforce monotonicity from the largest p-value down.
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    adjusted = np.clip(adjusted, 0.0, 1.0)
    result = np.empty(n, dtype=float)
    result[order] = adjusted
    return result.tolist()


_METHODS = {"bonferroni": bonferroni, "benjamini_hochberg": benjamini_hochberg}


def append_record(ledger_path: str | Path, record: ExperimentRecord) -> None:
    """Append one record to a JSONL ledger, creating it if needed."""
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_bytes() if path.exists() else b""
    line = json.dumps(record.to_dict(), sort_keys=True).encode("utf-8") + b"\n"
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(existing + line)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def load_records(ledger_path: str | Path) -> list[ExperimentRecord]:
    """Load all records from a JSONL ledger (empty list if absent)."""
    path = Path(ledger_path)
    if not path.exists():
        return []
    records = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        records.append(ExperimentRecord(**json.loads(raw)))
    return records


def summarize_ledger(
    records: list[ExperimentRecord],
    alpha: float = 0.05,
    method: str = "bonferroni",
) -> dict[str, Any]:
    """Re-correct every logged family-wise p across the whole program.

    Returns the program-level counts plus a per-experiment table carrying the
    adjusted p-value and the ``passed_program`` verdict — the honest answer once
    all logged hypotheses are accounted for.
    """
    if method not in _METHODS:
        raise ValueError(f"unknown method {method!r}; use one of {sorted(_METHODS)}")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be within (0, 1)")
    pvalues = [record.family_wise_p for record in records]
    adjusted = _METHODS[method](pvalues)
    table = []
    for record, adj in zip(records, adjusted):
        table.append(
            {
                "name": record.name,
                "family": record.family,
                "family_wise_p": record.family_wise_p,
                "adjusted_p": adj,
                "n_hypotheses": record.n_hypotheses,
                "passed_local": record.passed_local,
                "passed_program": bool(adj < alpha),
            }
        )
    return {
        "method": method,
        "alpha": alpha,
        "n_experiments": len(records),
        "total_hypotheses": int(sum(record.n_hypotheses for record in records)),
        "n_pass_local": int(sum(record.passed_local for record in records)),
        "n_pass_program": int(sum(row["passed_program"] for row in table)),
        "experiments": table,
    }
