# Grok CLI: image edit and image-to-video

**Registry lanes**: `image-edit` → `grok/image_edit` (ranked #1);
`image-to-video` → `grok/image_to_video` (previz lane; Kling 3.0 web is the
production lane but is not headlessly routable — see the registry's
`human_options`).

Seeding benchmark: `experiments/grok-kling-benchmark-2026-07-10` (vs
Magnific/Seedream baseline and Kling web, human-scored on the anchored 5-dim
rubric). Cost: flat grok.com subscription, no per-call metering observed.

## Invocation

Headless, scriptable, subagent-safe:

```bash
grok --always-approve --single "<instruction>"
```

- **image_edit** (the standout tool): attach a reference image; instruct the
  edit ("same model, same garment, now standing at a black wall, over-shoulder
  angle"). Identity transfer held 4/5 likeness across new scenes; product
  fidelity averaged 4.7/5 **including clean back-detail angles** — the exact
  angle class where Seedream hallucinates construction. 14–39s per call.
- **image_to_video**: attach a start frame; give a motion prompt. Push-in,
  turn, orbit, and walk directions all read correctly — motion-following is
  competitive with Kling. Output is 6s at 448–672px.
- **image_gen**: **do not route identity work here.** It takes NO reference
  image input at all — character-reference language in the prompt is silently
  ignored and every generation is an unanchored new person.

Verify auth/setup before a batch: run one cheap probe call and confirm an
artifact lands. (This lane is seeded from a benchmarked workflow; pin down
your installed CLI version's exact attachment syntax with a probe before
large runs.)

## Ceilings and routing boundaries

- Images cap at ~1MP (864×1152 / 1056×976 / 832×1248 typical); video at
  448–672px. **Anything client-facing needs an upscale pass or a different
  lane.**
- Production i2v when resolution matters → Kling 3.0 web, 4K/5s tier (150cr) —
  scored 5/5 across dimensions in the seeding benchmark and won every matched
  pair vs Grok. But it is web-UI only: an agent can prepare the start frame
  and prompt, a human submits. **Verify the submitted card's start-frame
  thumbnail before generating at a premium tier** — a start-frame upload race
  once burned 900 credits on a pure text-to-video result. The 4K/15s tier is a
  duration-only upgrade, not a quality one.

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
