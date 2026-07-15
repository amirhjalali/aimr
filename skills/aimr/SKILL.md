---
name: aimr
description: "Use BEFORE picking a tool, model, or effort level for generation or delegation work — generating an image, animating a frame, reconning a codebase, researching the web, delegating implementation, getting a second-opinion review, digesting bulk material — and whenever asked which AI model/CLI to use, what it costs, whether a lane (codex, gemini, grok, claude subagents) is installed/authenticated/alive, or how much quota remains."
---

# AIMR — aim agent work at the best model you can afford

This skill turns `registry.json` into a decision procedure. The registry has
three parts: `models` (the cost catalog: quota-draw weights for subscription
pools, API $/MTok where metered, effort levels), `capabilities` (ranked
providers, each with invocation/artifact/cost/score contracts), and `pools`
(how the doctor probes each account pool). `<skill-dir>` in any command
template means this skill's own directory (the one containing this file),
wherever it is installed — resolve it before dispatching.

## The procedure

0. **Probe first.** Run
   `python3 <skill-dir>/scripts/aimr_doctor.py --json`
   (local-only, <2s, zero network, zero quota). It reports, per pool:
   installed / authenticated / plan, any locally-readable quota snapshot,
   and a per-capability rollup of what is routable right now. Three speeds:
   default (local-only), `--usage` (the quota gauge: free readouts only —
   no liveness probes, no quota drawn), `--deep` (everything, incl.
   gemini/grok liveness; note: the grok probe draws real quota). Route only
   among lanes the report marks `ready` —
   **and when the top-ranked provider is unavailable, SAY which lane you
   substituted and why; never silently downgrade.** If the script itself
   can't run, fall back to the manual probes in `references/setup.md`.

1. **Name the capability.** Map the request onto a registry key:
   `image-generation`, `image-to-video`, `code-recon`, `web-research`,
   `code-implementation`, `review-second-opinion`, `long-context-multimodal`.
   One distinction matters: codebase recon vs web research (different failure
   modes: literal constants vs citation accuracy).

2. **Decide whether to delegate at all.** Delegation has a roughly fixed
   coordination cost per handoff: every boundary token is billed at least twice
   (you write the brief, the worker reads it; the worker writes the report, you
   read it), plus parallel workers partially overlap. Providers carry
   `handoff_overhead_tokens` where measured (Codex: ~10k per exec). Rule:
   delegate only when the tokens the worker will absorb clearly exceed the
   handoff overhead — batch small questions into one dispatch, and do trivial
   work yourself. Field anchor: on a small research task the frontier model
   solo was *cheaper* than orchestrating cheap workers; on a bulk-reading task
   orchestration reached ~96% of solo quality at ~46% of the cost.

3. **Read the ranked providers**: `capabilities[<key>].providers`, ordered
   best-first. `human_options` entries are quality-winning paths an agent
   cannot drive — surface them to the human when stakes warrant; never route
   to them.

4. **Filter by affordability.** Resolve each candidate's `model` in the
   `models` table. For subscription pools the currency is `quota_weight`
   (relative draw per token — marginal dollars are a fiction on flat plans);
   for metered pools it is `api_per_mtok` or per-call billing. Route to the
   LOWEST-weight lane that clears the quality bar, and prefer a lower-ranked
   provider you can afford over a top-ranked one you can't. The doctor's
   usage numbers feed this step: a pool at ≥90% of a window whose reset is
   far off means prefer the next lane — and say that's why. Mid-session,
   re-check runway cheaply before any sizable delegation:
   `aimr_doctor.py --usage --json` (free, zero quota; readouts are
   politeness-cached ~3min — the first check on a machine with no live
   codex session file may spawn a brief local app-server probe). Per window it
   derives, from a single reading: `left_percent`, `resets_in_seconds`,
   `headroom` (percent-only: ≥70 tight, ≥90 critical — a critical window
   minutes from reset is self-healing, so always read `resets_in_seconds`
   beside it), and `pace` (fraction used ÷ fraction of window elapsed;
   >1× burns faster than the window replenishes, and
   `time_to_exhaustion_seconds < resets_in_seconds` means the pool empties
   before it refills — shift bulk work to another lane and say so). When
   the endpoint ships its own `severity`, trust it over the derived label.
   The inverse signal routes too: a weekly window well under 50% with its
   reset near is use-it-or-lose-it capacity — prefer that pool for bulk
   work. Probes can lie
   (documented upstream bugs): treat readings as soft signals, not hard
   gates. Weights are defaults, not limits: judge the output, not the price
   tag — if a cheap lane misses the bar, redo one rung up without asking.

5. **Pick effort.** LLM lanes carry an `effort` default in their registry
   entry; `references/models.md` has the heuristics (mechanical/bulk → low or
   medium; root-cause chains, design tradeoffs, migration plans → xhigh).
   Never fabricate a per-effort score — benchmarks bind only to the
   model+effort actually run.

6. **Load the lane's `reference` file and follow it exactly** — references
   carry the invocation discipline and the gotchas. Read the provider's
   `gotchas` array before the first call, not after the first failure.

7. **Verify the artifact contract.** Each provider declares what comes back
   (`artifact`): a file path, a URL to download, stdout to redirect, or a
   worktree to review. The handoff is not done until the terminal artifact is
   a **local file path** (or reviewed worktree) you have confirmed exists and
   is non-empty. Review of delegated implementation is NEVER delegated.

## Task shape: where to spend the expensive model

Match the orchestration pattern to where the task needs judgment
(field-tested 2026-07; see the registry's `review-second-opinion` notes):

- **Judgment front-loaded** (spec is clear, work is typing): expensive model
  writes the brief, cheap lane executes, expensive model reviews. The classic
  delegation shape.
- **Judgment scattered** (exploratory work where each result reshapes the
  plan): cheap executor + expensive **advisor at fixed checkpoints**. Empirical
  anchor: checkpoints beat upfront planning — an upfront ranking by the
  frontier model was anti-correlated with what actually worked, while
  checkpoint steering reached ~90% of frontier-solo quality at ~34% of the
  cost. Cheap executors hill-climb on marginal gains; the advisor's job is
  re-ranking, not doing.
- **Judgment terminal** (correctness is checkable): cheap lanes produce,
  expensive model **verifies** — or better, a *different* model verifies
  (`review-second-opinion`: same-model review has correlated blind spots).

**Cache affinity**: route repeated calls to the same persistent worker so its
prompt cache accumulates (cache reads ≈ 0.1×). A fresh worker per request
re-pays the full context write and can erase the cheap-lane advantage. The
same rule at routing granularity: for follow-up work in the same context, a
cache-warm incumbent beats a marginally higher-ranked cold alternative —
never switch the delegated CLI/model mid-task without a failure reason.

## Score hygiene

- Prefer providers whose `score.date` is recent and whose `score.source` is a
  pack-run suite; `seeded` scores are real findings but predate this pack's
  suites; `overall: null` + `source: "unbenchmarked"` is a draft lane — usable,
  but probe with one cheap call before batch work.
- **Newest ≠ best** is an empirical rule here (gpt-5.5 beat the newer
  CLI-default model in a side-by-side audition; the retired image-lane evals
  showed the same pattern). Never override the ranking because a provider
  sounds newer.
- Trust calibration for anything delegated: literal constants are unverified
  until checked at source; negative claims ("X doesn't exist") require active
  probing by the caller; honest failure reports usually mean a brief bug.

## Handoff-failure policy (uniform across lanes)

| Failure | Signal | Action |
|---|---|---|
| Moderation block | provider-specific (Grok: actionable refusal text; GPT Image 2: `stream disconnected` on named artists) | Rewrite per guidance. One unchanged retry max (moderation is non-deterministic on Grok). Then reroute. Never batch-retry unmodified. |
| Hard rate limit / credits | image runner exit 2 in batch (`--jobs`) mode (exit 1 = batch finished with zero successes); single-image mode exits 1 on every failure — read the error text to distinguish | Stop the lane, fall to next provider. Do not spin. |
| Timeout | runner kill | **Check for a completed artifact before rerunning** — "timed-out" runs have repeatedly turned out to have finished. |
| Auth | doctor verdict `unauthenticated`/`auth-expired`/`blocked`, or a login-status probe fails mid-lane | Surface to the human with the doctor's `fix` command. Do not retry around auth. |
| Empty/wrong artifact | contract check fails | One retry with the same brief; then treat as a brief/prompt bug, not a transport bug. |

## Escalation

If every scriptable provider for a capability is exhausted, blocked, or
under-scored for the task's stakes: report the ranked options — including
`human_options` the human could drive manually — with each one's registry
notes, and stop. Do not silently substitute a different capability (an
unref'd generation is not an edit; a previz-resolution clip is not a hero
clip).

## Reference files

| File | When to load |
|---|---|
| `references/image-gen.md` | routed to `codex/gpt-image-2` (+ `image-gen-lessons.md` for prompt craft) |
| `references/video.md` | routed to Grok `image_to_video` |
| `references/recon.md` | routed to any Codex lane (recon, web research, implementation) |
| `references/longcontext.md` | routed to `gemini/cli` (DRAFT lane) |
| `references/models.md` | picking a Claude tier or an effort level |
| `references/setup.md` | reading doctor output, per-CLI install/auth/limits, manual fallback probes |
