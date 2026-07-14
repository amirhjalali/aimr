# Grok CLI: image-to-video

**Registry lane**: `image-to-video` → `grok/image_to_video` (previz lane).

Seeding benchmark: `experiments/grok-kling-benchmark-2026-07-10`,
human-scored on the anchored 5-dim rubric. Cost: draws the ONE shared
weekly SuperGrok pool (metered since June 2026 — the original "flat, no
per-call metering" note is stale). This is the only lane routed to the Grok
CLI for now (its `image_edit` tool benchmarked 4.7 in the same study but
was descoped 2026-07-13 with the other non-core lanes).

## Invocation (contract verified against grok 0.2.99, 2026-07-13)

Headless, scriptable, subagent-safe:

```bash
grok --no-auto-update --always-approve --output-format json \
  --single "Animate the image at /abs/path/frame.png: <motion prompt>. 6 seconds."
```

- **image_to_video**: the CLI's tool takes `image=<ABSOLUTE path>`, a motion
  prompt, `duration` 6|10s, `resolution_name` (~"480p") — reference the
  start frame by absolute path in the prompt. Push-in, turn, orbit, and
  walk directions all read correctly in the seeding benchmark. Output is
  6|10s at 448–672px.
- **The artifact lands session-relative at `videos/N.mp4`** and the tool
  result reports the absolute path — capture it from the JSON output and
  verify the file exists and is non-empty (the artifact contract).
- **Always pass `--no-auto-update`** in scripted runs: update chatter goes
  to stderr, JSON stays on stdout.
- **image_gen**: **never substitute it when the start frame carries
  identity.** It takes NO reference image input at all — character-reference
  language in the prompt is silently ignored and every generation is an
  unanchored new person.

Verify auth/setup before a batch: run the doctor
(`python3 <skill-dir>/scripts/aimr_doctor.py --pool grok`), then one cheap
probe call, and confirm an artifact lands. Free/X-Basic accounts: media
tools hard-fail with "Do not retry this tool" — that string means
lane-unavailable (tier gate), never a retryable error.

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
