# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## What This Is

AIMR (AI Model Router): a **one-skill pack + machine-readable capability
registry** that teaches a planning agent to route work (image gen, video
previz, code recon, web research, delegated implementation, second-opinion
reviews, bulk reading) to the best specialist model/CLI it can afford — with submodel, effort-level, and cost granularity, and benchmarks
keeping the rankings honest.

There is no application to run. The deliverable is one skill (markdown +
JSON + three stdlib scripts). Design specs:
`docs/superpowers/specs/2026-07-13-aimr-v2.1-doctor.md` (current: the
availability/usage probe layer), `2026-07-11-aimr-v2-one-skill-pack-design.md`
(v2 packaging; its "no multi-CLI aggregator exists" research claims were
corrected by the v2.1 spec), and `2026-07-11-aimr-skills-pack-design.md`
(v1; benchmark methodology still in force).

Lineage: grew out of `amirhjalali/agent-wrangler` (the tmux orchestrator,
which remains its own project).

## Layout

```
skills/aimr/              THE product — one install unit.
  SKILL.md                routing procedure (step 0: run the doctor),
                          task-shape guidance, delegation economics,
                          handoff-failure policy.
  registry.json           core artifact. Top-level `models` (cost catalog:
                          quota weights, $/MTok, efforts) + `pools` (the
                          availability layer's probe RECIPES — results are
                          never persisted here) + `capabilities` (ranked
                          providers, four contracts each).
                          `human_options` (unroutable quality winners) is a
                          supported mechanism, currently empty — scope is
                          agent-drivable CLIs (2026-07-13 descope).
                          Schema rules enforced by tests/test_registry.py.
  references/             per-lane invocation discipline + gotchas, loaded on
                          demand. models.md = tier/effort heuristics;
                          setup.md = doctor output + per-CLI auth and limits.
  evals/                  triggers.json: trigger-reliability fixture + run
                          record; a test pins it to SKILL.md's live
                          description (change one -> re-run the other).
  scripts/                aimr_doctor.py (availability/usage probe),
                          codex_image_gen.py (image runner), codex-task.sh
                          (worktree harness). Stdlib/bash only.
benchmarks/               methodology + rubric + judge prompt. The suite
                          RUNNER is deliberately absent until v2.2 ships a
                          pack-run suite.
tests/                    registry honesty checks (run in CI).
.claude-plugin/           plugin manifest.
```

## Commands

- Tests: `python3 -m pytest tests/` (pytest is the only dependency; Python 3.10+)
- Doctor (availability/usage report): `python3 skills/aimr/scripts/aimr_doctor.py`
  (`--json` for agents, `--deep` for live quota)
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
  real runs (a gotcha-free CLI/subagent lane is marked DRAFT via
  `source: "unbenchmarked"`; third-party API entries may be gotcha-free).
- Scripts stay stdlib-only, `from __future__ import annotations`, argparse,
  timeouts on all subprocess calls.
- **Usage awareness shipped 2026-07-13 as the doctor** (probe-first, founder
  decision in-session): `aimr_doctor.py` reports installed/auth/quota and
  never persists results into the registry (two-layer rule: registry =
  slow-moving quality rankings; doctor = live availability). **Budget
  machinery stays banned from core**: no ledgers, no spend accumulation, no
  hard budget gates — the doctor reports, the routing skill decides, and
  substitutions are always stated, never silent. Probe readings carry
  source + as-of + confidence, and are soft signals (probes can lie).
  A statusline remains an optional future extra.
