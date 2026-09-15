"""Command-line interface for the factor research platform."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ExperimentConfig
from .formula_dsl import describe_vocabulary
from .nl_translator import TranslatorUnavailable, resolve_translator, translate_idea


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

    commands.add_parser("vocabulary", help="print the DSL capability card")

    vi = commands.add_parser(
        "validate-idea", help="translate a natural-language idea, then validate it"
    )
    vi.add_argument("--idea", required=True)
    vi.add_argument("--name", default="candidate")
    vi.add_argument("--output-dir", default="outputs/factor_validation")
    vi.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    return parser


def _run_validate_factor(formula: str, name: str, output_dir: str,
                         provenance: dict) -> dict:
    """Seam so tests can exercise the translation path without a real backtest."""
    from scripts.validate_factor import validate_factor

    res = validate_factor(formula, name=name, output_dir=output_dir,
                          provenance=provenance)
    return {"slug": res.slug, "summary": res.summary, "output_dir": str(res.output_dir)}


def _print_card_with_guidance() -> int:
    print(describe_vocabulary())
    print("没有可用的翻译器。两条路：")
    print("  1. pip install -e '.[llm]' 并设置 ANTHROPIC_API_KEY，然后重跑 validate-idea；")
    print("  2. 把上面这张能力卡连同你的想法交给任意 LLM，拿到公式后用：")
    print("     python -m research_platform.cli validate-factor --formula='<公式>' --name <名字>")
    return 2


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
    if args.command == "vocabulary":
        print(describe_vocabulary())
        return 0
    if args.command == "validate-idea":
        translator = resolve_translator()
        if translator is None:
            return _print_card_with_guidance()
        try:
            result = translate_idea(args.idea, translator)
        except TranslatorUnavailable as exc:
            print(f"翻译器不可用：{exc}\n")
            return _print_card_with_guidance()
        except Exception as exc:   # noqa: BLE001 - surface as a run error, no half output
            print(f"翻译失败：{type(exc).__name__}: {exc}")
            return 1

        if not result.feasible:
            print(f"无法用现有数据字段表达这个想法。\n\n说明：{result.explanation}")
            if result.missing_data:
                print("\n缺少的数据：")
                for item in result.missing_data:
                    print(f"  - {item}")
            if result.nearest_formula:
                print(f"\n最接近的可表达变体：{result.nearest_formula}")
                if result.nearest_caveat:
                    print(f"差别：{result.nearest_caveat}")
                print("\n这个变体不会自动运行。要跑它，请显式执行："
                      f"\n  python -m research_platform.cli validate-factor "
                      f"--formula='{result.nearest_formula}' --name <名字>")
            elif result.nearest_caveat:
                print(f"\n备注：{result.nearest_caveat}")
            return 2

        print(f"公式：{result.formula}")
        print(f"说明：{result.explanation}")
        print(f"(翻译器 {result.translator} / 模型 {result.model} / 尝试 {result.attempts} 次)")
        if not args.yes:
            if input("\n用这个公式跑完整验证？[y/N] ").strip().lower() not in ("y", "yes"):
                print("已取消，未产生任何输出。")
                return 3
        payload = _run_validate_factor(
            formula=result.formula, name=args.name, output_dir=args.output_dir,
            provenance={"idea": args.idea, "explanation": result.explanation,
                        "translator": result.translator, "model": result.model,
                        "attempts": result.attempts},
        )
        print(json.dumps(payload, ensure_ascii=False))
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

