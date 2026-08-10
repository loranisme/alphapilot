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

    ledger = commands.add_parser(
        "ledger", help="re-correct logged experiments across the whole program"
    )
    ledger.add_argument("--ledger", required=True, help="path to the JSONL ledger")
    ledger.add_argument("--alpha", type=float, default=0.05)
    ledger.add_argument(
        "--method", choices=["bonferroni", "benjamini_hochberg"], default="bonferroni"
    )

    record = commands.add_parser(
        "ledger-record", help="append one experiment result to the ledger"
    )
    record.add_argument("--ledger", required=True)
    record.add_argument("--name", required=True)
    record.add_argument("--p", type=float, required=True, help="family-wise global p-value")
    record.add_argument("--n-hypotheses", type=int, required=True)
    record.add_argument("--family", default="default")
    record.add_argument("--passed", action="store_true", help="passed the local gate")

    vf = commands.add_parser(
        "validate-factor", help="validate one DSL factor formula end-to-end"
    )
    vf.add_argument(
        "--formula", required=True,
        help="a DSL factor formula string; for formulas starting with '-' use --formula=...",
    )
    vf.add_argument("--name", default="candidate")
    vf.add_argument("--output-dir", default="outputs/factor_validation")
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
    if args.command == "ledger":
        from .registry import load_records, summarize_ledger

        summary = summarize_ledger(
            load_records(args.ledger), alpha=args.alpha, method=args.method
        )
        print(json.dumps(summary, indent=2))
        return 0
    if args.command == "ledger-record":
        from .registry import ExperimentRecord, append_record

        append_record(
            args.ledger,
            ExperimentRecord(
                name=args.name,
                family_wise_p=args.p,
                n_hypotheses=args.n_hypotheses,
                passed_local=args.passed,
                family=args.family,
            ),
        )
        print(json.dumps({"recorded": args.name, "ledger": args.ledger}))
        return 0
    if args.command == "validate-factor":
        from scripts.validate_factor import validate_factor

        res = validate_factor(args.formula, name=args.name, output_dir=args.output_dir)
        print(json.dumps(
            {"slug": res.slug, "summary": res.summary, "output_dir": str(res.output_dir)},
            ensure_ascii=False,
        ))
        return 0
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

