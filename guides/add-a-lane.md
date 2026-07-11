# Adding a lane

A lane = a capability (or provider within one) made routable. The bar: an agent
that has never seen the provider before can dispatch to it, get the artifact, and
account for the spend, using only what's in this repo.

## Checklist

1. **Prove the headless path first.** If the provider is web-UI only, stop — add a
   registry entry with `invocation.type: "web-ui"` and `skill: null` so the
   routing skill can surface it to humans, and you're done. Scriptable means: one
   command, no interactive prompts, artifact lands at a knowable path.

2. **Write the skill** (`skills/<lane>/SKILL.md`):
   - Frontmatter `description` that says *when to route here* — the trigger, not
     just the topic.
   - The exact invocation, copy-pasteable.
   - The budget log incantation for its pool.
   - Gotchas as non-negotiables, each earned from a real failure. A skill with no
     gotchas hasn't been used yet — say so (mark it DRAFT like
     `longcontext-gemini`).
   - Routing boundaries: what should NOT come here, and where it goes instead.
   - Bundle runner scripts under `skills/<lane>/scripts/` — stdlib Python or
     plain bash, no dependencies.

3. **Add the registry entry** with all four contracts (invocation, artifact, cost,
   score). If unbenchmarked: `score: {overall: null, source: "unbenchmarked"}` —
   never invent a number. If seeded from prior findings: `source: "seeded"` and
   name the source study in `suite`.

4. **Honor the interop conventions** (`interop-conventions.md`): terminal artifact
   is a verified local file path; failures map onto the shared taxonomy; spend is
   logged with confidence.

5. **Write or extend a suite** (`benchmarks/`): 10–20 fixed tasks, anchored 0–5
   rubric, vision-LLM judge (never pixel metrics). Wire the provider into the
   suite runner's executor table. Run it; move the score from
   `unbenchmarked`/`seeded` to a pack-run suite id.

6. **Tests**: registry entry passes `tests/test_registry.py` schema checks
   (they run on every entry automatically); add a smoke test if the lane ships a
   runner script.

## Keeping lanes honest over time

- Re-run suites when a provider ships a major version — and remember the standing
  finding: **newest ≠ best** (flux-1.1-pro over flux-2-pro). Rankings change on
  evidence, not release notes.
- Prune gotchas that stop reproducing; date-stamp new ones.
- If a provider's moderation/limits behavior changes character, that's registry
  `gotchas` material even without a re-benchmark.
