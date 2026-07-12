# Contributing: adding a lane

A lane = a capability (or provider within one) made routable. The bar: an
agent that has never seen the provider before can dispatch to it, get the
artifact, and know what it cost, using only what's in this repo.

## Checklist

1. **Prove the headless path first.** Scriptable means: one command, no
   interactive prompts, artifact lands at a knowable path. If the provider is
   web-UI only, stop — add it to the capability's `human_options` array with
   honest notes (and score provenance if benchmarked) so the router can
   surface it to humans, and you're done.

2. **Add the registry entry** (`skills/aimr/registry.json`) with all four
   contracts (invocation, artifact, cost, score):
   - If the provider is LLM-backed, reference a `models` entry via `model`
     (+ `effort`), adding the models entry if new — every cost number needs a
     `source` and a `confidence`; unknown is `null`, never a guess.
   - If unbenchmarked: `score: {overall: null, source: "unbenchmarked"}` —
     never invent a number. If seeded from prior findings:
     `source: "seeded"` and name the source study in `suite`.
   - Record `handoff_overhead_tokens` if you've measured it.

3. **Write the reference file** (`skills/aimr/references/<lane>.md`):
   - The exact invocation, copy-pasteable.
   - Gotchas as non-negotiables, each earned from a real failure. A lane with
     no gotchas hasn't been used yet — say so (mark it DRAFT like
     `longcontext.md`).
   - Routing boundaries: what should NOT come here, and where it goes instead.
   - Bundle any runner script under `skills/aimr/scripts/` — stdlib Python or
     plain bash, `from __future__ import annotations`, argparse, timeouts on
     all subprocess calls, no dependencies.

4. **Honor the interop conventions** (enforced by the router):
   - Terminal artifact is a **verified local file path** (or reviewed
     worktree). URL/job-id/stdout providers close the gap inside their
     reference file.
   - Failures map onto the shared taxonomy (moderation-block,
     rate-limit-hard/soft, timeout, auth, contract-miss) with the caller
     policies in SKILL.md's handoff-failure table.
   - Confidence (`exact` | `estimated`) is recorded on every cost figure and
     never presented as a measurement when it's an estimate.

5. **Update SKILL.md** if you added a capability key (the procedure's step 1
   list and the reference-file table).

6. **Tests**: `python3 -m pytest tests/` — the registry schema checks run on
   every entry automatically. Add a smoke test if the lane ships a runner
   script.

## Benchmarking a lane

Suite methodology lives in `benchmarks/README.md` (anchored 0–5 rubrics,
vision-LLM judging, no pixel metrics — they measured r≈0.08 vs human
preference). Suites are immutable once published; changes mean a new suite
version. The suite runner machinery returns in v2.2 — until then, seeded
scores name their source study and `unbenchmarked` means exactly that.

## Keeping lanes honest over time

- Re-run suites when a provider ships a major version — and remember the
  standing finding: **newest ≠ best** (flux-1.1-pro over flux-2-pro; gpt-5.5
  over the newer CLI default). Rankings change on evidence, not release notes.
- Prune gotchas that stop reproducing; date-stamp new ones.
- If a provider's moderation/limits behavior changes character, that's
  registry `gotchas` material even without a re-benchmark.
