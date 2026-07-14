# AIMR v2: the one-skill pack (design)

Date: 2026-07-11
Status: approved direction (this doc supersedes the packaging/scope sections of
`2026-07-11-aimr-skills-pack-design.md`; that doc's benchmark methodology and
honesty rules remain in force)

## Goal

Make AIMR trivially easy to pick up: one skill, one install command, no
required Python, no config. Add the granularity v1 lacked (submodels, effort
levels, real cost data) as *data in the registry*, not machinery. Defer the
usage manager to an optional extra (v2.1). Make the repo look professional.

## Naming

- The project is **AIMR — AI Model Router**. Written "AIMR" (no dots),
  pronounced "aimer". README leads with the tagline
  **"AIMR — aim agent work at the best model you can afford"**;
  "AI Model Router" is the subtitle/expansion.
- Repo slug stays `aimr` (lowercase, tool convention). Plugin name `aimr`.

## What v2.0 ships

### 1. One skill: `skills/aimr/`

```
skills/aimr/
├── SKILL.md            entry point (~150 lines): routing procedure,
│                       task-shape guidance, delegation economics
├── registry.json       THE core artifact (moves here from repo root)
├── references/         loaded on demand when a lane is routed to
│   ├── image-gen.md    (from skills/image-gen-codex + its lessons.md)
│   ├── recon.md        (from skills/recon-codex — covers recon AND
│   │                    delegated implementation / worktree harness)
│   ├── video.md        (from skills/video-gen-grok — image-edit + i2v)
│   ├── longcontext.md  (from skills/longcontext-gemini — still DRAFT)
│   ├── models.md       submodel/effort when-to-use guidance
│   └── setup.md        per-CLI auth/install checks (from guides/cli-setup.md)
└── scripts/
    ├── codex_image_gen.py   kept: timeout/exit-code-2/workdir handling
    └── codex-task.sh        kept: worktree harness for implementation
```

Rationale: single-skill packs are a normal shape (notion, frontend-design);
`references/` + `scripts/` inside a skill is the standard pattern (Anthropic's
document skills bundle scripts; superpowers bundles references). The dividing
line: *prose gotchas live in references, retry/exit-code handling lives in
scripts*. Scripts stay stdlib-only, argparse, subprocess timeouts.

### 2. SKILL.md content (the router)

The v1 routing procedure (name capability → read ranked providers → check
affordability → follow lane reference → verify artifact contract) plus three
additions sourced from the 2026-07-11 research (see Provenance):

- **Task-shape guidance**: use the expensive model as orchestrator, as
  advisor-at-checkpoints, or as verifier depending on whether judgment is
  front-loaded, scattered, or terminal. Empirical anchor: advisory checkpoints
  beat upfront planning (upfront rankings can be anti-correlated with
  outcomes); cheap-executor + frontier-advisor reached ~90% of frontier-solo
  quality at ~34% of token cost (Lance Martin, Parameter Golf, 2026-07-10).
- **Delegation economics**: delegation has a roughly fixed per-handoff
  coordination cost (boundary tokens are billed at least twice: brief written +
  read, report written + read; plus fan-out overlap). Rule: delegate only when
  the tokens the worker absorbs clearly exceed the handoff overhead. Each
  registry provider carries `handoff_overhead_tokens` where known (Codex:
  ~10k/exec). Anchor: on small tasks frontier-solo was *cheaper* than
  orchestrating cheap workers; on large ones orchestration hit 96% of score at
  46% of cost (BrowseComp).
- **Cache affinity**: route repeat calls to the same persistent worker so its
  prompt cache accumulates; a fresh worker per request re-pays the context
  write and can erase the cheap-worker advantage. Cache reads ≈ 0.1×.
- **Affordability without the usage manager**: v2.0 has no required budget
  tool. Step 3 becomes: consult the registry `models` table quota weights +
  the per-CLI reset windows in `references/setup.md`, and prefer the
  lowest-weight lane that clears the quality bar. If the optional usage
  manager (v2.1) is installed, probe it instead. The append-only ledger
  discipline is dropped from core.

### 3. registry.json v2 (single file, two additions, one demotion)

Stays one JSON file inside the skill. `version: 2`. Changes:

a. **New top-level `models` table** — data, not machinery:

```jsonc
"models": {
  "anthropic/fable-5":   { "pool": "claude-sub", "quota_weight": 10,
    "api_per_mtok": { "in": 10.0, "out": 50.0 },
    "efforts": ["low","medium","high","xhigh","max"],
    "when": "architecture, briefs, final review, synthesis; effort is the throttle" },
  "anthropic/opus-4.8":  { "pool": "claude-sub", "quota_weight": 5,
    "api_per_mtok": { "in": 5.0, "out": 25.0 },
    "when": "diverse second-perspective reviews, judge/verify stages" },
  "anthropic/sonnet-5":  { "pool": "claude-sub", "quota_weight": 3,
    "api_per_mtok": { "in": 2.0, "out": 10.0, "note": "intro until 2026-08-31, then 3/15" },
    "when": "workhorse subagents, drafting" },
  "anthropic/haiku-4.5": { "pool": "claude-sub", "quota_weight": 1,
    "api_per_mtok": { "in": 1.0, "out": 5.0 },
    "when": "mechanical contract-gated pulls ONLY — never judgment" },
  "openai/gpt-5.5":      { "pool": "codex-sub", "quota_weight": 0,
    "efforts": ["minimal","low","medium","high","xhigh"],
    "efforts_confidence": "estimated",
    "when": "typing, recon, web research, images; weight 0 is a STATE not a property — re-weight when codex headroom shrinks" }
  // gemini / grok entries: quota_weight null, source: "unbenchmarked-cost"
}
```

Rules: every number carries provenance in a sibling `source` field (Anthropic
pricing page for $/MTok; "founder-set 2026-07-03" for quota weights; OpenAI
effort-level set is `estimated` — unverified from primary sources as of
2026-07-11). Unknown numbers are `null`, never invented — same honesty rule as
scores. Quota weights are the routing currency for subscription pools
(marginal dollars there are a fiction); API $/MTok matters only for metered
pools and for readers.

b. **Provider entries gain `model` + `effort` + optional
`handoff_overhead_tokens`** — e.g. `codex/gpt-5.5-exec` becomes
`{ "model": "openai/gpt-5.5", "effort": "xhigh", "handoff_overhead_tokens": 10000, ... }`.
Scores attach to the provider entry (i.e. model@effort via that provider),
because benchmark results are only valid for the model+effort actually run.
Unbenchmarked effort variants are NOT enumerated as separate entries —
`references/models.md` heuristics pick effort; we do not fabricate per-effort
scores (avoids a model×effort×capability benchmark burden).

c. **New capability `web-research`**, split out of `code-recon` (different
failure modes: citation accuracy and negative-claim probing, not literal
constants). Providers: `codex/gpt-5.5-exec` with `tools.web_search=true`
(seeded score from the 2026-07 delegation lessons: ~1 factual error per 40
citations; negative claims get zero trust) and `claude/subagent-websearch`
(unbenchmarked, `overall: null`).

d. **New capability lane: claude subagents.** `claude/subagent-<tier>`
provider entries (invocation.type `"claude-subagent"`, invoked via the Agent
tool / `claude -p --model <tier> --effort <level>` from other CLIs) are added
where they genuinely compete: `code-recon` (sonnet), `web-research` (sonnet),
plus a new `review-second-opinion` capability (opus — a different model
reviewing the author's work beats same-model review; correlated-blind-spots
rationale). All start `source: "unbenchmarked"`, `overall: null` except where
seeded lessons exist.

e. **Web-UI demotion**: `kling/3.0-web` and `magnific/seedream-4.5` move from
`providers` to a per-capability `human_options` array — same notes/scores
kept, but they no longer share the provider schema (no invocation contract to
honor). Routing skill: "if no scriptable provider clears the bar, surface
`human_options` to the human and stop." This kills the skip-logic special case.

### 4. Cuts (v1 → v2.0)

| v1 | v2.0 |
|---|---|
| `skills/routing/` + 4 lane skills | one `skills/aimr/` |
| `budget/` (ledger CLI) | cut from core; design preserved for v2.1 extra |
| `commands/` (`/aimr:route`, `/aimr:budget`) | cut — the skill is the interface |
| `benchmarks/image-gen-v1/runner.py` + tasks | cut until a pack-run suite is actually executed; `rubric.md` + `judge_prompt.md` + benchmarks/README (methodology + honesty rules) survive as `benchmarks/` docs |
| `guides/cli-setup.md` | `skills/aimr/references/setup.md` |
| `guides/add-a-lane.md` | `CONTRIBUTING.md` (updated for one-skill layout) |
| `guides/interop-conventions.md` | folded into SKILL.md (artifact contract, confidence rule) |
| root `registry.json` | `skills/aimr/registry.json` |

The confidence rule survives the budget cut: any number shown to a human or
used in routing is labeled exact/estimated — now enforced in registry data
(`source`/`confidence` fields) rather than ledger lines.

### 5. Professional repo surface

- **README rewrite**: one-paragraph pitch + tagline; a routing-flow diagram
  (mermaid); the lane table with current scores and score provenance; 60-second
  install (`/plugin marketplace add amirhjalali/aimr` → `/plugin install aimr`,
  plus plain-directory copy for non-plugin users); "how it stays honest"
  section (no invented scores, newest ≠ best, web-UI honesty); visible roadmap.
- **CONTRIBUTING.md**: add-a-lane recipe (registry entry + reference file +
  gotchas-or-DRAFT rule).
- `.claude-plugin/plugin.json` updated (name AIMR, new description, version 2.0.0).
- CLAUDE.md updated to the new layout and conventions.
- Tests keep running in CI (`.github/workflows/tests.yml` already exists).

### 6. Tests (updated, not cut)

`tests/test_registry.py` updated for v2: every provider resolves
`model` → an existing `models` entry; scored entries have suite+date;
unbenchmarked entries have `overall: null`; every `models` number has a
`source`; `human_options` entries never carry an invocation contract;
`handoff_overhead_tokens` is a positive int where present. `test_budget.py`
is deleted with `budget/`.

## Roadmap (in README, promised not built)

- **v2.1 — usage manager (optional extra, `extras/usage-manager/`)**: the
  monclaude-style piece. Probe-first with per-pool adapters, ledger fallback,
  and a `statusline` subcommand emitting a compact cross-pool line for Claude
  Code's `statusLine` setting. Research findings that shape it (2026-07-11):
  Claude Code is genuinely probeable locally (`~/.claude/projects/*.jsonl` +
  `stats-cache.json` — the ccusage / Claude-Code-Usage-Monitor method; no tool
  named "monclaude" exists — GitHub/npm/PyPI probed); Codex subscription quota
  has no headless readout (TUI `/status` only; open upstream issue) so it
  stays session-log/ledger-estimated; Gemini has documented daily caps but no
  scriptable `/stats`; Grok has nothing → pure ledger. Probes can lie (vendors'
  own dashboards/CLIs disagree in open bugs), so `estimated`-confidence
  readings soft-gate (prefer cheaper lane near reserve) rather than hard-block.
  No multi-CLI aggregator exists today; this would be the first.
  **[CORRECTED 2026-07-13 — see `2026-07-13-aimr-v2.1-doctor.md`:** the
  aggregator claim was refuted (ccusage, CodexBar, caut, openusage all
  exist); Codex DOES have headless quota readouts (rollout-file rate-limit
  snapshots + `codex app-server` JSON-RPC); the Claude OAuth usage endpoint
  is the account quota truth (local JSONLs are per-machine history, not
  quota); Gemini's individual tiers died server-side 2026-06-18. The
  shipped v2.1 doctor builds on the corrected facts.**]**
- **v2.2 — first pack-run benchmark suite** (likely `web-research-v1` or
  `longcontext-v1`, since gemini is the unbenchmarked lane), retiring the
  first seeded scores. Runner machinery returns only then.

## Provenance of this design

Inputs, per AIMR's own routing (dogfooded 2026-07-11): Codex web-research run
(usage introspection + model catalogs, cited), Codex repo recon (schema v2 +
usage-manager sketch), Opus 4.8 independent critique (weights-not-dollars,
sparse-scores discipline, probes-lie caveat, cut list), Lance Martin's
"Cost effective harnesses with Fable" (task shape, coordination cost, cache
affinity), and founder decisions in-session: usage manager = both probe-first
(deferred to v2.1 as optional install), Claude tiers = full lanes, scope =
simple one-skill pack first, name = AI Model Router.

## Out of scope for v2.0

Usage manager code, statusline, benchmark runner, three-file registry split
(providers/models/capabilities files — revisit only if the single file gets
unwieldy), any new lane skills, renaming the repo.
