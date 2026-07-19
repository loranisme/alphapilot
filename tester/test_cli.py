from __future__ import annotations

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
