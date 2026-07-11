# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

AIMR (AI Model Routing): a **skills pack + machine-readable capability registry**
for cross-CLI agent interoperability. It teaches a planning agent to route work
(image gen, reference edits, video previz, code recon, delegated implementation,
bulk reading) to the best specialist CLI it can afford, with benchmarks keeping
the rankings honest and a per-account token-budget ledger gating dispatch.

There is no application to run. The deliverables are markdown skills, JSON data,
and small stdlib-Python scripts. Design spec:
`docs/superpowers/specs/2026-07-11-aimr-skills-pack-design.md`.

Lineage: grew out of `amirhjalali/agent-wrangler` (the tmux orchestrator, which
remains its own project).

## Layout

```
registry.json     THE core artifact — capability → ranked providers. Four contracts
                  per provider: invocation, artifact, cost, score. Schema rules are
                  enforced by tests/test_registry.py.
skills/           one lane per dir (SKILL.md + optional scripts/). skills/routing/
                  is the meta-skill everything else assumes.
budget/           budget.py (stdlib CLI: log/status/remaining) + example config.
                  budget.json and ledger.jsonl are user-local, gitignored.
benchmarks/       versioned suites (tasks.json, rubric.md, judge_prompt.md,
                  runner.py). Suites are immutable once published — changes = new
                  suite version.
guides/           interop-conventions.md (the seams), cli-setup.md, add-a-lane.md
commands/         Claude Code plugin slash commands (/aimr:route, /aimr:budget)
.claude-plugin/   plugin manifest
```

## Commands

- Tests: `python3 -m pytest tests/` (pytest is the only dependency; everything
  else is stdlib, Python 3.10+)
- Budget: `python3 budget/budget.py status|log|remaining`
- Benchmark generation pass:
  `python3 benchmarks/image-gen-v1/runner.py --provider codex/gpt-image-2 --out runs/...`
  (judging is a separate vision-LLM pass per `judge_prompt.md`)

## Non-negotiable conventions

- **Registry honesty**: no score without `suite` + `date`; unbenchmarked lanes say
  `source: "unbenchmarked"` with `overall: null` — never invent numbers. Seeded
  scores name their source study.
- **Web-UI providers** get registry entries (`invocation.type: "web-ui"`,
  `skill: null`) but are never routed by agents — surfaced to humans instead.
- **Artifact contract**: every lane terminates in a verified local file path (or
  reviewed worktree). URL/job-id providers close the gap inside their skill.
- **No pixel-quality metrics in benchmarks** — empirically r≈0.08 vs human
  preference. Vision-LLM judging only.
- **Budget confidence**: every ledger line is `exact` or `estimated` and displays
  must say which. Pools are accounts, not vendors.
- New lanes follow `guides/add-a-lane.md`; skills carry gotchas earned from real
  runs (a gotcha-free skill is marked DRAFT).
- Scripts stay stdlib-only, `from __future__ import annotations`, argparse,
  timeouts on all subprocess calls.
