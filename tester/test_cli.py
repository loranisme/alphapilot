from __future__ import annotations

import json
import subprocess
import sys


def test_cli_help_lists_commands():
    process = subprocess.run(
        [sys.executable, "-m", "research_platform.cli", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0
    assert "validate-data" in process.stdout
    assert "run-experiment" in process.stdout
    assert "generate-report" in process.stdout


def test_cli_validate_config_accepts_example():
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "research_platform.cli",
            "validate-data",
            "--config",
            "configs/research_platform_example.yaml",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0
    assert '"valid": true' in process.stdout.lower()


def test_cli_ledger_record_then_summarize(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    for name, p in (("phase_b", "0.001"), ("phase_c", "0.9")):
        rec = subprocess.run(
            [
                sys.executable, "-m", "research_platform.cli", "ledger-record",
                "--ledger", str(ledger), "--name", name, "--p", p,
                "--n-hypotheses", "10", "--passed",
            ],
            text=True, capture_output=True, check=False,
        )
        assert rec.returncode == 0, rec.stderr
    summarize = subprocess.run(
        [
            sys.executable, "-m", "research_platform.cli", "ledger",
            "--ledger", str(ledger), "--method", "bonferroni",
        ],
        text=True, capture_output=True, check=False,
    )
    assert summarize.returncode == 0, summarize.stderr
    payload = json.loads(summarize.stdout)
    assert payload["n_experiments"] == 2
    assert payload["n_pass_program"] == 1  # only phase_b survives across the program
