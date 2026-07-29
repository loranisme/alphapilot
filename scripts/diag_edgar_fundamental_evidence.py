"""Phase C diagnostic: do EDGAR PIT value/quality fundamentals clear the
Phase B significance gate?

Fetches SEC EDGAR companyfacts for the price universe, builds as-first-reported
PIT fundamental panels, computes the existing FundamentalFactorDesigner value/
quality family, and runs it through evaluate_factor_grid. A small-sample mode
validates field mapping and PIT depth before any full crawl.

    python scripts/diag_edgar_fundamental_evidence.py --sample 15   # validation
    python scripts/diag_edgar_fundamental_evidence.py               # full run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data_section.edgar_collector import fetch_companyfacts
from factor_section.factor_design import FundamentalFactorDesigner
from research_platform.edgar_fundamentals import CONTRACT_COLUMNS, build_pit_fundamentals
from research_platform.significance import evaluate_factor_grid
from scripts.diag_factor_evidence import HORIZONS, build_ic_panel
from scripts.run_alpha101_correlation_oos import _load_real_inputs

USER_AGENT = "factor-research yqcgao@g.ucla.edu"


def load_price_close(project_root: Path) -> pd.DataFrame:
    """Reuse the exact price universe / calendar of the OHLCV experiments."""
    _existing, _alpha101, close, _industry, _meta = _load_real_inputs(
        project_root, allow_network=False
    )
    return close


def coverage_summary(panels: dict[str, pd.DataFrame], close: pd.DataFrame) -> dict:
    spans = []
    per_ticker = {}
    for ticker, panel in panels.items():
        valid = panel.dropna(how="all")
        if not valid.empty:
            spans.append((valid.index.min(), valid.index.max()))
            per_ticker[ticker] = {
                "start": str(valid.index.min().date()),
                "non_null_columns": [c for c in CONTRACT_COLUMNS if panel[c].notna().any()],
            }
    first = min((s for s, _ in spans), default=None)
    # cross-sectional coverage: names with a known value on the last date
    last = close.index[-1]
    names_last = sum(
        1 for p in panels.values() if p.loc[:last].notna().any().any()
    )
    return {
        "tickers_with_fundamentals": len(panels),
        "price_tickers": int(close.shape[1]),
        "earliest_fundamental_date": str(first.date()) if first is not None else None,
        "names_with_any_fundamental_by_end": names_last,
        "contract_columns": list(CONTRACT_COLUMNS),
        "per_ticker": per_ticker,
    }


def build_fundamental_factor_panels(
    panels: dict[str, pd.DataFrame], close: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    designer = FundamentalFactorDesigner()
    factors = designer.build_factor_panels(panels, close)
    # align every factor to the full price grid so the IC panel is rectangular
    return {
        name: panel.reindex(index=close.index, columns=close.columns)
        for name, panel in factors.items()
    }


def run(sample: int | None, output_dir: Path, project_root: Path = PROJECT_ROOT):
    close = load_price_close(project_root)
    tickers = list(close.columns)
    if sample:
        tickers = tickers[:sample]

    fetched = {"n": 0}

    def _progress(i, ticker, status):
        if status == "ok":
            fetched["n"] += 1

    raw = fetch_companyfacts(tickers, user_agent=USER_AGENT, on_progress=_progress)
    panels = build_pit_fundamentals(raw, close.index)
    coverage = coverage_summary(panels, close)
    coverage["tickers_requested"] = len(tickers)
    coverage["companyfacts_returned"] = len(raw)

    factors = build_fundamental_factor_panels(panels, close)
    coverage["factors_built"] = sorted(factors)

    result = {"coverage": coverage, "panels": panels, "factors": factors, "close": close}
    ic_panel = build_ic_panel(factors, close) if factors else pd.DataFrame()
    # The significance gate needs a real cross-section (>=30 names per date); in
    # small-sample validation mode it will be empty, so report coverage only.
    if factors and not ic_panel.dropna(how="all").empty:
        grid = evaluate_factor_grid(ic_panel, n_boot=2000, seed=42, p_threshold=0.05)
        result["grid"] = grid
        write_report(grid, coverage, output_dir)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "coverage.json").write_text(
            json.dumps(coverage, indent=2), encoding="utf-8"
        )
    return result


def write_report(grid: pd.DataFrame, coverage: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    grid.to_csv(output_dir / "evidence.csv", index=False)
    (output_dir / "coverage.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")

    n_pass = int((grid["verdict"] == "PASS").sum())
    n_horizon = int((grid["verdict_horizon"] == "PASS").sum())
    best = (
        grid.sort_values(["p_global", "z_stat"], ascending=[True, False])
        .drop_duplicates("factor")[
            ["factor", "horizon", "ic_bar", "z_stat", "crit_z_5pct", "t_naive",
             "p_horizon", "p_global", "verdict"]
        ]
    )
    lines = [
        "# EDGAR Fundamental Evidence Report (Phase C)",
        "",
        "## Coverage",
        "",
        f"- Tickers with PIT fundamentals: {coverage['tickers_with_fundamentals']} / "
        f"{coverage.get('tickers_requested', coverage['price_tickers'])} requested.",
        f"- Earliest fundamental date: {coverage['earliest_fundamental_date']}.",
        f"- Factors built: {', '.join(coverage['factors_built'])}.",
        "",
        "## Method",
        "",
        f"- Value/quality family x {len(HORIZONS)} horizons through the Phase B "
        "studentized block-bootstrap family-wise gate (horizon-matched blocks, "
        "Bonferroni across horizons); PASS if global p < 0.05.",
        "",
        "## Conclusion",
        "",
        f"- **PASS (global, p < 0.05): {n_pass} / {len(grid)} cells.**",
        f"- PASS per-horizon only (before across-horizon Bonferroni): {n_horizon}.",
    ]
    if n_pass == 0:
        lines.append(
            "- No EDGAR value/quality factor clears the gate. Free PIT fundamentals "
            "add no significant cross-sectional signal on this universe either."
        )
    else:
        survs = grid[grid["verdict"] == "PASS"]
        lines.append(
            "- Survivors: "
            + ", ".join(f"{r.factor}@h{r.horizon} (p={r.p_global:.3f})" for r in survs.itertuples())
            + "."
        )
    lines += [
        "",
        "## Best horizon per factor (sorted by global p)",
        "",
        best.round(4).to_markdown(index=False),
        "",
        "## Caveats",
        "",
        "- **As-first-reported PIT** via EDGAR calendar-quarter frames and filing "
        "dates; later amendments are not chased.",
        "- **Survivorship bias:** current S&P constituents only; delisted names "
        "absent, which flatters quality/value.",
        "- IC is rank predictiveness, not tradeable P&L; costs/turnover apply "
        "downstream and are out of scope here.",
        "",
    ]
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=None,
                        help="use only the first N tickers (validation mode)")
    args = parser.parse_args()
    out = PROJECT_ROOT / "outputs" / (
        "edgar_fundamental_validation" if args.sample else "edgar_fundamental_evidence"
    )
    result = run(args.sample, out)
    cov = {k: v for k, v in result["coverage"].items() if k != "per_ticker"}
    print(json.dumps(cov, indent=2))
    if "grid" in result:
        grid = result["grid"]
        print(f"\nPASS {int((grid['verdict'] == 'PASS').sum())} / {len(grid)} "
              f"(global p<0.05). See {out}/report.md")
    else:
        print(f"\nBuilt {len(result['factors'])} factors but skipped the "
              "significance gate: a cross-section of >=30 names is required "
              "(validation sample too small). Coverage written to coverage.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
