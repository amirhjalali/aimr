"""Schema checks that keep registry.json honest.

Every provider must carry the four contracts (invocation, artifact, cost, score),
scores must be auditable (suite + date) or explicitly unbenchmarked, and routable
entries must point at a real bundled skill.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "registry.json").read_text())


def all_providers():
    for cap_name, cap in REGISTRY["capabilities"].items():
        for p in cap["providers"]:
            yield cap_name, p


PROVIDERS = list(all_providers())
IDS = [f"{c}:{p['id']}" for c, p in PROVIDERS]


def test_top_level_shape():
    assert REGISTRY["version"] == 1
    assert REGISTRY["updated"]
    assert REGISTRY["capabilities"]


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_four_contracts_present(cap, provider):
    for contract in ("invocation", "artifact", "cost", "score"):
        assert contract in provider, f"{provider['id']} missing {contract}"


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_invocation_contract(cap, provider):
    inv = provider["invocation"]
    assert inv["type"] in ("headless-cli", "api", "web-ui")
    assert inv["command_template"]
    if inv["type"] != "web-ui":
        assert inv["timeout_s"], "routable providers need a timeout"


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_cost_contract(cap, provider):
    cost = provider["cost"]
    assert cost["pool"]
    assert cost["confidence"] in ("exact", "estimated")


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_scores_are_auditable(cap, provider):
    score = provider["score"]
    if score.get("overall") is None:
        assert score.get("source") in ("unbenchmarked", "seeded"), \
            "null scores must declare why"
    else:
        assert score.get("suite"), "a number without a suite is not auditable"
        assert score.get("date"), "a number without a date is not auditable"
        assert 0 <= score["overall"] <= 5


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_skill_paths_exist(cap, provider):
    skill = provider.get("skill")
    if skill is not None:
        assert (ROOT / skill / "SKILL.md").exists(), f"{skill}/SKILL.md missing"
    else:
        # skill-less providers must not pretend to be routable lanes
        assert provider["invocation"]["type"] in ("web-ui", "api")


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_gotchas_are_a_list(cap, provider):
    assert isinstance(provider.get("gotchas", []), list)


def test_headless_providers_have_gotchas_or_are_drafts():
    """A routable lane with no recorded gotchas hasn't been used — only the
    explicitly unbenchmarked draft lane gets that pass."""
    for cap, p in PROVIDERS:
        if p["invocation"]["type"] == "headless-cli" and not p.get("gotchas"):
            assert p["score"].get("source") == "unbenchmarked", \
                f"{p['id']} is routable but has no gotchas and isn't marked a draft"


def test_benchmark_suite_files_valid():
    tasks = json.loads((ROOT / "benchmarks" / "image-gen-v1" / "tasks.json").read_text())
    assert tasks["suite"] == "image-gen-v1"
    ids = [t["id"] for t in tasks["tasks"]]
    assert len(ids) == len(set(ids)), "duplicate task ids"
    assert len(ids) >= 10
    for t in tasks["tasks"]:
        assert t["prompt"] and t["archetype"] and t["size"]
        assert len(t["prompt"].split()) <= 150, f"{t['id']} prompt exceeds 150 words"
