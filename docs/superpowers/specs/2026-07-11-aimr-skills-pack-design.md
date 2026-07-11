# AIMR (AI Model Routing) — Skills Pack for Cross-CLI Agent Interoperability

Date: 2026-07-11
Status: Approved (all sections reviewed conversationally; execution authorized "best judgment")
Naming update (2026-07-11, same day): originally designed as "Agent Wrangler v2,
pivot in place" — superseded the same day by a founder decision to ship this as a
NEW repo named AIMR and restore agent-wrangler to its tmux-era state. The
"Name/repo" decision below is preserved as written for the historical record; the
rest of the design carried over unchanged.

## What it is

AIMR is a **skills pack plus a
machine-readable capability registry** that teaches any planning agent (Claude Code,
Codex CLI, Gemini CLI) to route work to the best specialist CLI for each capability
lane — ranked by benchmarked quality and filtered by live token/quota budget across
the user's accounts.

One-sentence pitch: *drop the pack into your agent's skill directory; it reads
`registry.json`, routes any capability to the best CLI it can afford, and each skill
teaches the exact invocation.*

## Decisions log

- **Shape**: skills pack as source of truth + thin Claude Code plugin wrapper.
  No standalone CLI product. (Considered: pure pack, plugin-first, pack+CLI.)
- **Name/repo**: keep `agent-wrangler`, pivot in place. Current tmux orchestrator
  preserved via `v1-tmux` tag + `tmux-legacy` branch; `main` restructured.
  (Considered: new repo `saddlebag`/`agent-switchboard` — rejected; renaming breaks
  GitHub redirects and the wrangler metaphor survives: skills = knowing each mount,
  registry = the string, budget = the feed bill.)
- **Audience**: me-first, public-later. Personal directives stripped from seeded
  skills so the public path stays open.
- **V1 lanes**: image-gen (Codex/GPT Image 2), recon/exec (Codex), video-gen (Grok
  CLI), long-context/multimodal (Gemini) + routing meta-skill.
- **Budget is a headline feature**, not a component: per-account token/quota ledger
  across Claude / Codex / Gemini / Grok / API dollars ("monclaude-style").
- **Benchmarks keep the registry honest** and are the durable/defensible artifact;
  vision-LLM judging only (pixel metrics proven useless at r≈0.08 vs humans in
  gabooja-labs calibration work).

## Layout (repo root = plugin root)

```
registry.json        capability → ranked providers (invocation, artifact, cost, score)
skills/
  image-gen-codex/   seeded from personal gpt-image-2 skill (runner + lessons copied)
  recon-codex/       seeded from codex-delegation (founder directives stripped)
  video-gen-grok/    seeded from ~/experiments PROVIDER_MATRIX Grok CLI benchmark
  longcontext-gemini/ new; honest draft, marked unbenchmarked
  routing/           meta-skill: read registry → check budget → pick lane → handle failure
budget/              ledger.jsonl convention + stdlib budget.py (log/status/remaining)
benchmarks/          per-lane suites (tasks + anchored rubric + judge prompt + runner)
guides/              interop conventions, per-CLI setup, add-a-lane
.claude-plugin/      plugin.json manifest
commands/            /wrangler:route, /wrangler:budget slash commands
tests/               budget + registry-schema tests (old tmux tests live in v1-tmux tag)
docs/                specs + plans (this file)
```

## registry.json schema (the spec — v1)

Top level: `version`, `updated`, `capabilities` (map). Each capability holds ordered
`providers`. Each provider carries four contracts:

- **invocation**: `{type: headless-cli|web-ui|api, command_template, timeout_s}` —
  how to call it. `web-ui` entries exist for honesty (e.g. Kling) but routing skills
  prefer scriptable types.
- **artifact**: `{type: file|url|job-id, format, delivery}` — what comes back; the
  interop seam.
- **cost**: `{pool, est_per_call, confidence: exact|estimated}` — which account the
  spend draws from.
- **score**: `{overall, dims, suite, date}` — benchmarked, dated, tied to a named
  suite so rankings are auditable. Optional `source: seeded` for entries imported
  from prior findings rather than a pack-run suite.

Plus `gotchas: [..]` (operational traps) and `notes`.

Registry launches **seeded, not empty**, from prior benchmark findings:
GPT Image 2 first for text-heavy/POD image gen (19/20 archetypes, 4.97/5, gotchas:
aspect drift, ~150-word prompt timeout, named-artist moderation trap, no
transparency); Grok `image_edit` first for reference-driven edits (product fidelity
4.7/5 on hard angles); Grok `image_to_video` for previz vs Kling 3.0 (web-only) for
hero clips; Grok `image_gen` recorded as disqualified for identity work (no
reference input).

## Budget model

- `budget/ledger.jsonl` — append-only: `{ts, account, pool, event, tokens|calls,
  est_cost, confidence}`.
- `budget/budget.json` — configured caps per account (user-local, gitignored;
  `budget.example.json` committed).
- `budget/budget.py` — stdlib CLI: `log`, `status`, `remaining`. Routing reads
  `remaining`.
- Sourcing honesty per provider: Claude = transcript JSONLs via hook (best data);
  Codex = session logs where present, else per-dispatch estimates; Gemini/Grok =
  invocation counts + estimates; direct API = exact. Every line carries
  `confidence`; displays never pretend estimates are exact.

## Benchmarks

Per-lane suite: 10–20 fixed tasks spanning archetypes, anchored 0–5 rubric (adapted
from `~/experiments/EVALUATION_RUBRIC.md` and gabooja-labs `cje_v2.md`), vision-LLM
judge doing absolute per-dimension scoring. Runner executes tasks headlessly through
the lane's skill, judges artifacts, rewrites the provider's `score` in
`registry.json`. Suites versioned (`image-gen-v1`).

V1 simplifications (upgrade paths named): reuse gabooja's calibrated judging
approach rather than re-deriving weights (full `calibrate.py` rigor = v2 if rankings
are contested); absolute scoring rather than pairwise (pairwise = the fix if
providers ceiling at 5/5).

## Plugin wrapper

`.claude-plugin/plugin.json` names the pack; `skills/` is picked up natively;
`commands/route.md` (pick a lane for a described task) and `commands/budget.md`
(render cross-account status) give one-command ergonomics. Budget hook wiring
documented in `guides/` (kept as documented opt-in, not auto-installed, in v1).

## Error handling & interop conventions (guides/)

- Artifact contract: every lane returns a **local file path** as the terminal
  artifact; URL/job-id providers must include the poll/download step in the skill.
- Handoff failure taxonomy: moderation-block (rewrite, don't retry), rate-limit
  (backoff/exit-2 conventions), timeout (check for completed output before rerun —
  the "timed-out runs actually finished" lesson), auth (per-CLI login checks).
- Non-deterministic moderation is called out explicitly (observed on Grok and
  Kling): a block is not a stable signal; one retry then reroute.

## Testing

Pytest: budget.py (log/status/remaining math, ledger append/rotation), registry
schema validation (every provider has the four contracts; scores carry suite+date),
benchmark task-file validity. CI updated to run the new suite.

## Out of scope (v1)

Leaderboard site; pairwise judging; automated judge recalibration; auto-installed
usage hooks; Magnific/Seedream and Kling as *scriptable* lanes (web-UI-only —
recorded in registry for honesty, not routable).
