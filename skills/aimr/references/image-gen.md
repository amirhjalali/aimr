# Image generation with GPT Image 2 (via Codex CLI)

**Registry lane**: `image-generation` → `codex/gpt-image-2` (ranked #1 for
text-in-scene, vintage/retro, art-history fusion, dense conceptual
compositions — 19/20 archetypes in the seeding benchmark).

## Overview

GPT Image 2 is driven as **text-to-image through the Codex CLI**: a
`codex exec` agent calls its built-in image tool, the model is selected by the
phrase "GPT Image 2.0" in the prompt (there is no model flag), and the
resulting PNG is harvested. First-shot output is deployment-grade for
illustrated / POD work — budget for speed (~119s/image), not for
generate-and-pick.

Bundled so none of this has to be re-derived: `../scripts/codex_image_gen.py`
(the runner) and `image-gen-lessons.md` (the full field guide). **Read
`image-gen-lessons.md` before writing prompts or debugging.**

## Prerequisites (verify first)

- The `codex` CLI is installed and **logged in** — auth is the ChatGPT plan,
  not an API key. Check with `codex login status`. There is **no
  `OPENAI_API_KEY`**.
- Python 3.10+ (standard library only; Pillow optional).

## Quick start

Single image:

```bash
python skills/aimr/scripts/codex_image_gen.py \
  --prompt "Vintage badge: a fox in a ranger hat, muted 3-color palette. \
Format: square 1:1, centered, isolated graphic." \
  --out ./fox.png
```

With reference image(s) for style/character anchoring:

```bash
python skills/aimr/scripts/codex_image_gen.py --prompt "..." --out ./out.png \
  --ref ./character_sheet.png --size 1536x1024
```

Batch, parallel, from a JSON job file
(`[{"id","prompt","out","refs","size"}, ...]`):

```bash
python skills/aimr/scripts/codex_image_gen.py --jobs jobs.json --workers 8 --log run_log.json
```

The runner already encodes the operational discipline: correct `codex exec`
argv, per-job workdir isolation, `-o last.txt` result parsing, 8-way-safe
concurrency, process-group timeout kills, and HARD/SOFT rate-limit
classification (exit code 2 when credits are exhausted). Prefer patching it
over rewriting a runner. Run it from a scratch directory or it litters
`codex_img_workdirs/` into the cwd.

## The non-negotiables (full detail in image-gen-lessons.md)

1. **Prompt ≤ ~150 words.** Long prompts *time out*, not just slow down.
2. **Lead with `Format:`; for wearables add "square, centered, isolated
   graphic."** Aspect drift to poster orientation is the #1 silent failure for
   apparel.
3. **Never name a living/contemporary artist.** It returns `stream
   disconnected` (moderation masquerading as a rate limit). Use descriptive
   style language.
4. **Anchor style to a specific tradition + negative constraints.** Vague
   style words summon wrong priors (CJK text, cute mascots, kids-book
   borders).
5. **No transparency — ever.** Output is opaque white-BG RGB. Insert a
   background-removal hop before any cut-line product (sticker, phone case).
6. **~1.5 MP native → 4× Real-ESRGAN** for anything larger than a chest print.
7. **It doesn't remember characters.** Anchor every frame with a reference
   PNG; for multi-panel coherence use one-master-crop or a 3D bake, not
   reference chaining.

## Route elsewhere when (check registry.json for current rankings)

- **Exact typography / logos / wordmarks →** Ideogram (renders kerning
  literally; GPT Image 2 editorializes glyphs).
- **Mascot/character craft, pixel-art →** Flux (note: flux-1.1-pro
  outbenchmarks the newer flux-2-pro — check scores, don't assume newest).
- **Native vector / SVG →** Recraft (GPT Image 2 is raster-only).
- **Reference-driven edit of an existing image →** `image-edit` lane (Grok
  `image_edit` — see `video.md`).

## When something breaks

See the troubleshooting table in `image-gen-lessons.md`. Most common: a run
that "rate limits" but is actually the named-artist moderation trap (`stream
disconnected`) — rewrite the prompt, do not treat it as a capacity cap. Second
most common: a "timed out" run that actually finished — check the workdir for
a completed `out.png` before rerunning.
