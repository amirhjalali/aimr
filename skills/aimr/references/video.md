# Grok CLI: image-to-video

**Registry lane**: `image-to-video` → `grok/image_to_video` (previz lane).

Seeding benchmark: `experiments/grok-kling-benchmark-2026-07-10`,
human-scored on the anchored 5-dim rubric. Cost: flat grok.com subscription,
no per-call metering observed. This is the only lane routed to the Grok CLI
for now (its `image_edit` tool benchmarked 4.7 in the same study but was
descoped 2026-07-13 with the other non-core lanes).

## Invocation

Headless, scriptable, subagent-safe:

```bash
grok --always-approve --single "<motion prompt, start frame attached>"
```

- **image_to_video**: attach a start frame; give a motion prompt. Push-in,
  turn, orbit, and walk directions all read correctly in the seeding
  benchmark. Output is 6s at 448–672px.
- **image_gen**: **never substitute it when the start frame carries
  identity.** It takes NO reference image input at all — character-reference
  language in the prompt is silently ignored and every generation is an
  unanchored new person.

Verify auth/setup before a batch: run one cheap probe call and confirm an
artifact lands. (This lane is seeded from a benchmarked workflow; pin down
your installed CLI version's exact attachment syntax with a probe before
large runs.)

## Ceilings and routing boundaries

- Video output is 448–672px: **previz and high-volume low-stakes work only.**
  Anything client-facing needs a different path — report the limitation and
  stop rather than silently shipping a previz-resolution clip as a hero clip.

## Moderation: the retry policy

Grok's moderation is **non-deterministic**: an identical prompt/params can
pass on one call and block on the very next (observed on both image_gen and
image_to_video). Roughly 1 in 6–8 calls blocked in the seeding benchmark,
concentrated on domestic/bedroom framing and high-exposure flash +
minimal-garment combinations — not on garment category itself.

Policy: a block is NOT a stable signal. Retry once unchanged; if it blocks
again, apply the refusal's guidance (Grok returns actionable text like "less
intimate framing" rather than a bare error) or reroute. Never burn a batch
retrying a blocked prompt unmodified.
