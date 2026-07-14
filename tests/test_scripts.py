"""Smoke tests for the bundled scripts — CI must not ship them blind.

CONTRIBUTING.md's own rule: a lane that ships a runner script gets a smoke
test. These catch syntax errors and argparse regressions, not behavior
(behavior lives in test_doctor.py for the doctor; the codex runners need a
live codex CLI and are exercised in the field).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "aimr" / "scripts"


@pytest.mark.parametrize("script", ["codex_image_gen.py", "aimr_doctor.py"])
def test_python_scripts_help(script):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), "--help"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "usage:" in proc.stdout.lower()


@pytest.mark.parametrize("script", ["codex_image_gen.py", "aimr_doctor.py"])
def test_python_scripts_compile(script):
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SCRIPTS / script)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_codex_task_sh_parses():
    proc = subprocess.run(
        ["bash", "-n", str(SCRIPTS / "codex-task.sh")],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
