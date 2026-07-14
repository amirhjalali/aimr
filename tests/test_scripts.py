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


def test_trigger_eval_tracks_live_description():
    """The trigger eval binds to the description it measured: if SKILL.md's
    frontmatter description changes, this fails until the eval is re-run
    and skills/aimr/evals/triggers.json is updated with the new text+result."""
    import json
    evals = json.loads((ROOT / "skills" / "aimr" / "evals" / "triggers.json").read_text())
    skill_text = (ROOT / "skills" / "aimr" / "SKILL.md").read_text()
    frontmatter = skill_text.split("---")[1]
    live_description = frontmatter.split("description:")[1].strip().strip('"')
    assert evals["description_under_test"] == live_description, \
        "SKILL.md description changed — re-run the trigger eval and update triggers.json"
    assert evals["runs"], "triggers.json needs at least one recorded run"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_codex_task_sh_parses():
    proc = subprocess.run(
        ["bash", "-n", str(SCRIPTS / "codex-task.sh")],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
