from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def budget(tmp_path, monkeypatch):
    """budget.py module with ledger + config redirected to a temp dir."""
    mod = _load_module("aw_budget", ROOT / "budget" / "budget.py")
    monkeypatch.setattr(mod, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(mod, "CONFIG", tmp_path / "budget.json")
    return mod
