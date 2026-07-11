---
name: routing
description: "The meta-skill: route any capability request (generate an image, edit a reference shot, animate a frame, recon a codebase, delegate implementation, digest bulk material) to the best specialist CLI you can afford right now. Use BEFORE picking a tool or model for generation/delegation work — read registry.json for the ranked providers, check the budget ledger for pool balances, load the winning lane's skill, and follow the handoff-failure policy when a provider blocks, times out, or rate-limits."
---

# Routing: pick the right lane, every time

This skill turns `registry.json` + `budget/` into a decision procedure. It is the
only skill in this pack that other skills assume you have read.

## The procedure

1. **Name the capability.** Map the request onto a registry key:
   `image-generation` (from-scratch), `image-edit` (reference-driven, same
   identity/product), `image-to-video`, `code-recon`, `code-implementation`,
   `long-context-multimodal`. The from-scratch vs reference distinction matters —
   providers that win one are disqualified from the other (Grok `image_gen` takes
   no reference input at all).

2. **Read the ranked providers**: `registry.json` → `capabilities[<key>].providers`
   (already ordered best-first). Skip entries with `invocation.type: "web-ui"` —
   they are recorded for honesty but you cannot drive them; surface them to the
   human as an option instead.

3. **Filter by budget.** `python budget/budget.py remaining --pool <pool>` for the
   candidate's `cost.pool`. If the pool is exhausted or below its reserve, fall to
   the next provider. A lower-ranked provider you can afford beats a top-ranked
   one you can't.

4. **Load the winning lane's skill** (`provider.skill`) and follow it exactly —
   the skills carry the invocation discipline and the gotchas. Read the provider's
   `gotchas` array before the first call, not after the first failure.

5. **Log the spend.** Every dispatch appends a ledger line (each skill shows its
   exact `budget.py log` incantation). Estimated is fine; unlogged is not.

6. **Verify the artifact contract.** Each provider declares what comes back
   (`artifact`): a file path, a URL to download, or a job-id/worktree to poll and
   review. The handoff is not done until the terminal artifact is a **local file
   path** (or reviewed worktree) you have confirmed exists and is non-empty.

## Score hygiene

- Prefer providers whose `score.date` is recent and whose `score.source` is a
  pack-run suite; `seeded` scores are real findings but predate this pack's
  suites.
- `newest ≠ best` is an empirical rule here (flux-1.1-pro outbenchmarks
  flux-2-pro). Never override the registry ranking because a provider sounds newer.
- A provider with `score.overall: null` and `source: "unbenchmarked"` is a draft
  lane: usable, but probe with one cheap call before batch work, and prefer a
  scored alternative for anything high-stakes.

## Handoff-failure policy (uniform across lanes)

| Failure | Signal | Action |
|---|---|---|
| Moderation block | provider-specific (Grok: actionable refusal text; GPT Image 2: `stream disconnected` on named artists) | Rewrite per guidance. One unchanged retry max (moderation is non-deterministic on Grok/Kling). Then reroute. Never batch-retry unmodified. |
| Hard rate limit / credits | e.g. runner exit code 2 | Stop the lane, log it, fall to next provider. Do not spin. |
| Timeout | runner kill | **Check for a completed artifact before rerunning** — "timed-out" image runs have repeatedly turned out to have finished. |
| Auth | login-status check fails | Surface to the human (`codex login status`, `grok` session, `gemini` auth). Do not retry around auth. |
| Empty/wrong artifact | contract check fails | One retry with the same brief; then treat as a brief/prompt bug, not a transport bug. |

## Escalation

If every scriptable provider for a capability is exhausted, blocked, or
under-scored for the task's stakes: report the ranked options — including web-ui
entries the human could drive manually — with each one's registry `notes`, and
stop. Do not silently substitute a different capability (an unref'd generation is
not an edit; a previz-resolution clip is not a hero clip).
