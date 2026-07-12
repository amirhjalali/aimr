# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## What This Is

AIMR (AI Model Router): a **one-skill pack + machine-readable capability
registry** that teaches a planning agent to route work (image gen, reference
edits, video previz, code recon, web research, delegated implementation,
second-opinion reviews, bulk reading) to the best specialist model/CLI it can
afford — with submodel, effort-level, and cost granularity, and benchmarks
keeping the rankings honest.

There is no application to run. The deliverable is one skill (markdown +
JSON + two stdlib scripts). Design specs:
`docs/superpowers/specs/2026-07-11-aimr-v2-one-skill-pack-design.md` (current)
and `2026-07-11-aimr-skills-pack-design.md` (v1; benchmark methodology still
in force).

Lineage: grew out of `amirhjalali/agent-wrangler` (the tmux orchestrator,
which remains its own project).

## Layout

```
skills/aimr/              THE product — one install unit.
  SKILL.md                routing procedure, task-shape guidance, delegation
                          economics, handoff-failure policy.
  registry.json           core artifact. Top-level `models` (cost catalog:
                          quota weights, $/MTok, efforts) + `capabilities`
                          (ranked providers, four contracts each) +
                          `human_options` (unroutable quality winners).
                          Schema rules enforced by tests/test_registry.py.
  references/             per-lane invocation discipline + gotchas, loaded on
                          demand. models.md = tier/effort heuristics;
                          setup.md = per-CLI auth and limits.
  scripts/                codex_image_gen.py (image runner), codex-task.sh
                          (worktree harness). Stdlib/bash only.
benchmarks/               methodology + rubric + judge prompt. The suite
                          RUNNER is deliberately absent until v2.2 ships a
                          pack-run suite.
tests/                    registry honesty checks (run in CI).
.claude-plugin/           plugin manifest.
```

## Commands

- Tests: `python3 -m pytest tests/` (pytest is the only dependency; Python 3.10+)
- Image lane smoke: `python3 skills/aimr/scripts/codex_image_gen.py --help`

## Non-negotiable conventions

- **Registry honesty**: no score without `suite` + `date`; unbenchmarked
  lanes say `source: "unbenchmarked"` with `overall: null` — never invent
  numbers. Seeded scores name their source study. Same rule for cost data:
  every number carries `source` + `confidence`; unknown is `null`.
- **Quota weights over dollars for subscription pools** — marginal $ is a
  fiction on flat plans; `models[].quota_weight` is the routing currency.
  Effort levels are picked by heuristic (`references/models.md`), never by
  fabricated per-effort scores.
- **Web-UI providers** live in `human_options` (no invocation contract) —
  surfaced to humans, never routed by agents.
- **Artifact contract**: every lane terminates in a verified local file path
  (or reviewed worktree). URL/job-id/stdout providers close the gap inside
  their reference file.
- **No pixel-quality metrics in benchmarks** — empirically r≈0.08 vs human
  preference. Vision-LLM judging only.
- New lanes follow `CONTRIBUTING.md`; references carry gotchas earned from
  real runs (a gotcha-free lane is marked DRAFT).
- Scripts stay stdlib-only, `from __future__ import annotations`, argparse,
  timeouts on all subprocess calls.
- The usage manager (probe-first quota awareness + statusline) is a ROADMAP
  item (v2.1, optional extra) — do not reintroduce budget machinery into the
  core skill.
