from __future__ import annotations

import json


def _log(budget, *args):
    return budget.main(["log", *args])


def test_log_appends_jsonl(budget):
    assert _log(budget, "--pool", "codex", "--calls", "2", "--event", "image-gen") == 0
    assert _log(budget, "--pool", "grok", "--tokens", "500", "--confidence", "exact") == 0
    lines = [json.loads(l) for l in budget.LEDGER.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["pool"] == "codex" and lines[0]["calls"] == 2
    assert lines[0]["confidence"] == "estimated"  # default
    assert lines[1]["confidence"] == "exact"


def test_log_requires_a_quantity(budget, capsys):
    assert _log(budget, "--pool", "codex") == 2
    assert not budget.LEDGER.exists()


def test_remaining_unconfigured_pool_is_unlimited(budget, capsys):
    _log(budget, "--pool", "mystery", "--calls", "1")
    assert budget.main(["remaining", "--pool", "mystery"]) == 0
    assert capsys.readouterr().out.strip() == "unlimited"


def test_remaining_subtracts_window_spend(budget, capsys):
    budget.CONFIG.write_text(json.dumps(
        {"pools": {"codex": {"unit": "calls", "cap": 10, "period": "month", "reserve_fraction": 0.2}}}))
    _log(budget, "--pool", "codex", "--calls", "3")
    _log(budget, "--pool", "codex", "--calls", "2")
    _log(budget, "--pool", "grok", "--calls", "99")  # other pool, ignored
    assert budget.main(["remaining", "--pool", "codex"]) == 0
    assert capsys.readouterr().out.strip() == "5"


def test_remaining_exit_3_below_reserve(budget, capsys):
    budget.CONFIG.write_text(json.dumps(
        {"pools": {"codex": {"unit": "calls", "cap": 10, "period": "month", "reserve_fraction": 0.2}}}))
    _log(budget, "--pool", "codex", "--calls", "9")
    assert budget.main(["remaining", "--pool", "codex"]) == 3


def test_remaining_never_negative(budget, capsys):
    budget.CONFIG.write_text(json.dumps(
        {"pools": {"codex": {"unit": "calls", "cap": 5, "period": "month"}}}))
    _log(budget, "--pool", "codex", "--calls", "8")
    budget.main(["remaining", "--pool", "codex"])
    assert capsys.readouterr().out.strip() == "0"


def test_corrupt_ledger_line_is_skipped(budget, capsys):
    budget.CONFIG.write_text(json.dumps(
        {"pools": {"codex": {"unit": "calls", "cap": 10, "period": "month"}}}))
    _log(budget, "--pool", "codex", "--calls", "1")
    with budget.LEDGER.open("a") as f:
        f.write("not json{{{\n")
    _log(budget, "--pool", "codex", "--calls", "1")
    budget.main(["remaining", "--pool", "codex"])
    assert capsys.readouterr().out.strip() == "8"


def test_status_flags_reserve_and_confidence_mix(budget, capsys):
    budget.CONFIG.write_text(json.dumps(
        {"pools": {"codex": {"unit": "calls", "cap": 4, "period": "month", "reserve_fraction": 0.5}}}))
    _log(budget, "--pool", "codex", "--calls", "3", "--confidence", "exact")
    _log(budget, "--pool", "codex", "--calls", "1")
    assert budget.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "BELOW RESERVE" in out
    assert "1 exact / 1 estimated" in out


def test_status_surfaces_unconfigured_ledger_pools(budget, capsys):
    _log(budget, "--pool", "surprise", "--dollars", "2.5")
    budget.main(["status"])
    out = capsys.readouterr().out
    assert "surprise" in out and "unlimited" in out
