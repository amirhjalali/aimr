"""Schema checks that keep registry.json honest.

Every provider must carry the four contracts (invocation, artifact, cost,
score); scores must be auditable (suite + date) or explicitly
unbenchmarked/seeded; LLM-backed providers must resolve to a `models` entry;
every cost number must carry provenance; and `human_options` entries must
never masquerade as routable providers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "aimr"
REGISTRY = json.loads((SKILL_DIR / "registry.json").read_text())

ROUTABLE_TYPES = ("headless-cli", "api", "claude-subagent")


def all_providers():
    for cap_name, cap in REGISTRY["capabilities"].items():
        for p in cap["providers"]:
            yield cap_name, p


def all_human_options():
    for cap_name, cap in REGISTRY["capabilities"].items():
        for h in cap.get("human_options", []):
            yield cap_name, h


PROVIDERS = list(all_providers())
IDS = [f"{c}:{p['id']}" for c, p in PROVIDERS]
HUMAN_OPTIONS = list(all_human_options())
HUMAN_IDS = [f"{c}:{h['id']}" for c, h in HUMAN_OPTIONS]
MODELS = REGISTRY["models"]


def test_top_level_shape():
    assert REGISTRY["version"] == 2
    assert REGISTRY["updated"]
    assert REGISTRY["capabilities"]
    assert REGISTRY["models"]


# ---------------------------------------------------------------- providers

@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_four_contracts_present(cap, provider):
    for contract in ("invocation", "artifact", "cost", "score"):
        assert contract in provider, f"{provider['id']} missing {contract}"


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_invocation_contract(cap, provider):
    inv = provider["invocation"]
    assert inv["type"] in ROUTABLE_TYPES, \
        f"{provider['id']}: web-ui entries belong in human_options, not providers"
    assert inv["command_template"]
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
    # dims carrying numbers are auditable too
    if score.get("dims"):
        assert score.get("suite") and score.get("date"), \
            f"{provider['id']}: scored dims need suite + date"


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_model_refs_resolve(cap, provider):
    model = provider.get("model")
    if model is not None:
        assert model in MODELS, f"{provider['id']} references unknown model {model}"
    else:
        # model-less providers must be third-party API lanes, not CLI/subagent lanes
        assert provider["invocation"]["type"] == "api", \
            f"{provider['id']}: CLI/subagent providers must reference a models entry"


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_effort_is_declared_by_model(cap, provider):
    effort = provider.get("effort")
    if effort is not None:
        model = MODELS[provider["model"]]
        assert model.get("efforts"), \
            f"{provider['id']} sets effort but model declares none"
        assert effort in model["efforts"], \
            f"{provider['id']}: effort {effort} not in {provider['model']}.efforts"


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_reference_paths_exist(cap, provider):
    ref = provider.get("reference")
    if ref is not None:
        assert (SKILL_DIR / ref).exists(), f"{ref} missing"
    else:
        # reference-less providers must not pretend to be first-class lanes
        assert provider["invocation"]["type"] == "api"


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_handoff_overhead_is_sane(cap, provider):
    overhead = provider.get("handoff_overhead_tokens")
    if overhead is not None:
        assert isinstance(overhead, int) and overhead > 0


@pytest.mark.parametrize("cap,provider", PROVIDERS, ids=IDS)
def test_gotchas_are_a_list(cap, provider):
    assert isinstance(provider.get("gotchas", []), list)


def test_routable_providers_have_gotchas_or_are_drafts():
    """A routable lane with no recorded gotchas hasn't been used — only
    explicitly unbenchmarked draft lanes get that pass."""
    for cap, p in PROVIDERS:
        if p["invocation"]["type"] in ("headless-cli", "claude-subagent") \
                and not p.get("gotchas"):
            assert p["score"].get("source") == "unbenchmarked", \
                f"{p['id']} is routable but has no gotchas and isn't marked a draft"


# ------------------------------------------------------------ human options

@pytest.mark.parametrize("cap,option", HUMAN_OPTIONS, ids=HUMAN_IDS)
def test_human_options_are_not_routable(cap, option):
    assert "invocation" not in option, \
        f"{option['id']}: human_options must not carry an invocation contract"
    assert option.get("notes"), "human options need honest notes"


# ----------------------------------------------------------------- models

@pytest.mark.parametrize("model_id", list(MODELS), ids=list(MODELS))
def test_models_entries_are_honest(model_id):
    m = MODELS[model_id]
    assert m.get("pool"), f"{model_id} needs a pool"
    assert m.get("when"), f"{model_id} needs when-to-use guidance"
    # quota_weight: a number, or null with an explanation
    qw = m.get("quota_weight", "MISSING")
    assert qw != "MISSING", f"{model_id} must declare quota_weight (null = unknown)"
    if qw is None:
        assert m.get("cost_note"), \
            f"{model_id}: null quota_weight needs a cost_note explaining why"
    else:
        assert isinstance(qw, (int, float)) and qw >= 0
    # api pricing: present with provenance, or explicitly null
    api = m.get("api_per_mtok", "MISSING")
    assert api != "MISSING", f"{model_id} must declare api_per_mtok (null = unknown)"
    if api is not None:
        assert api["in"] > 0 and api["out"] > 0
        assert api.get("source"), f"{model_id}: pricing without a source"
        assert api.get("confidence") in ("exact", "estimated")
    # efforts: a list with provenance, or explicitly null
    efforts = m.get("efforts", "MISSING")
    assert efforts != "MISSING", f"{model_id} must declare efforts (null = n/a)"
    if efforts is not None:
        assert isinstance(efforts, list) and efforts
        assert m.get("efforts_source"), f"{model_id}: efforts without a source"


def test_provider_pools_match_model_pools():
    for cap, p in PROVIDERS:
        model = p.get("model")
        if model is not None:
            assert p["cost"]["pool"] == MODELS[model]["pool"], \
                f"{p['id']}: cost.pool disagrees with {model}'s pool"


# -------------------------------------------------------------- benchmarks

def test_benchmark_methodology_docs_exist():
    for f in ("README.md", "image-gen-v1/rubric.md", "image-gen-v1/judge_prompt.md"):
        assert (ROOT / "benchmarks" / f).exists(), f"benchmarks/{f} missing"


def test_skill_entrypoint_exists():
    assert (SKILL_DIR / "SKILL.md").exists()
    text = (SKILL_DIR / "SKILL.md").read_text()
    assert text.startswith("---"), "SKILL.md needs frontmatter"
    assert "description:" in text.split("---")[1]
