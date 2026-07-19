"""Command-line interface for the factor research platform."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ExperimentConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factor-research")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-data", help="validate experiment configuration")
    validate.add_argument("--config", required=True)

    run = commands.add_parser("run-experiment", help="run the configured local experiment")
    run.add_argument("--config", required=True)
    run.add_argument("--output-dir", default="outputs/research_platform_validation")

    report = commands.add_parser("generate-report", help="inspect an existing result directory")
    report.add_argument("--result-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-data":
        config = ExperimentConfig.from_yaml(args.config)
        print(json.dumps({"valid": True, "config": config.to_dict()}, indent=2))
        return 0
    if args.command == "run-experiment":
        from scripts.run_research_platform_validation import run_validation

        return run_validation(Path(args.config), Path(args.output_dir))
    result_dir = Path(args.result_dir)
    required = {
        "experiment_summary.json",
        "factor_diagnostics.csv",
        "portfolio_metrics.csv",
        "quality_report.json",
        "report.md",
    }
    missing = sorted(name for name in required if not (result_dir / name).exists())
    print(json.dumps({"valid": not missing, "missing": missing}, indent=2))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())

