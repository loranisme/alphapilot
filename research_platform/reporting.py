"""Atomic structured output for experiment results."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import pandas as pd

from .contracts import ExperimentResult


def _json_default(value):
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _atomic_text(path: Path, content: str) -> None:
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _markdown_report(result: ExperimentResult) -> str:
    lines = ["# Factor Research Experiment", "", "## Quality gates", ""]
    for name, passed in result.quality.get("gates", {}).items():
        lines.append(f"- {'PASS' if passed else 'FAIL'}: `{name}`")
    lines.extend(["", "## Data quality observations", ""])
    for name, value in result.quality.items():
        if name == "gates":
            continue
        lines.append(f"- {name}: {value}")
    lines.extend(["", "## Factor diagnostics", ""])
    diagnostics = result.tables.get("factor_diagnostics", pd.DataFrame())
    lines.append(diagnostics.to_markdown(index=False) if not diagnostics.empty else "No diagnostics generated.")
    lines.extend(["", "## Portfolio metrics", ""])
    portfolios = result.tables.get("portfolio_metrics", pd.DataFrame())
    lines.append(portfolios.to_markdown(index=False) if not portfolios.empty else "No portfolio metrics generated.")
    return "\n".join(lines) + "\n"


def write_result(result: ExperimentResult, output_dir: str | Path) -> list[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary_path = output / "experiment_summary.json"
    diagnostics_path = output / "factor_diagnostics.csv"
    portfolio_path = output / "portfolio_metrics.csv"
    quality_path = output / "quality_report.json"
    report_path = output / "report.md"

    _atomic_text(
        summary_path,
        json.dumps(
            {"metrics": result.metrics, "metadata": result.metadata},
            indent=2,
            sort_keys=True,
            default=_json_default,
        ),
    )
    _atomic_text(
        diagnostics_path,
        result.tables.get("factor_diagnostics", pd.DataFrame()).to_csv(index=False),
    )
    _atomic_text(
        portfolio_path,
        result.tables.get("portfolio_metrics", pd.DataFrame()).to_csv(index=False),
    )
    _atomic_text(
        quality_path,
        json.dumps(result.quality, indent=2, sort_keys=True, default=_json_default),
    )
    _atomic_text(report_path, _markdown_report(result))
    return [summary_path, diagnostics_path, portfolio_path, quality_path, report_path]
