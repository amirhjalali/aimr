# GPT Image 2 via Codex CLI — Field Lessons

Distilled from ~15 experiments (~700+ generations) driving GPT Image 2 through the
Codex CLI on a ChatGPT plan. Load this before writing prompts or debugging a run.

## Mental model

- Codex CLI drives GPT Image 2 as **pure text-to-image**. The `codex exec` agent
  calls its built-in image tool; the image model is selected by the phrase
  **"GPT Image 2.0"** in the prompt, not a CLI flag.
- **Auth is the ChatGPT plan session, not an API key.** No `OPENAI_API_KEY`. The
  `codex` CLI must already be logged in (`codex login status`).
- **First-shot quality is deployment-grade.** Across 336 outputs in one probe,
  "every single one is shippable." Budget for *speed*, not for N-shot-and-pick.

## Prompt craft (the rules that move quality)

1. **Keep prompts ≤ ~150 words.** Long prompts *time out*, they don't just slow
   down: a ~700-word template caused 17 of 24 generations to fail on timeout;
   trimming to ~150 words fixed it. Concision is a correctness issue here.
2. **State a `Format:` line, and for wearables force square.** Aspect-ratio drift
   to poster/portrait is the model's most common silent failure for apparel. Use a
   leading `Format: square 1:1` (or `2:1 wide horizontal`, `vertical 3:4`) and, for
   chest prints/stickers, append `square aspect ratio, centered, isolated graphic`.
3. **Never name a living/contemporary artist.** "in the style of <artist>" trips
   moderation and returns `stream disconnected before completion` — which *looks
   like* rate-limiting but is a policy trip. Rewrite to descriptive style language
   ("traditional Persian miniature painting style", "Edo-period ukiyo-e woodblock
   print style"). This alone cleared a 12/12 failing batch.
4. **Anchor style to a SPECIFIC tradition + negative constraints.** Vague style
   words summon wrong priors: "modern children's picture-book" produced CJK text,
   cute mascots, candy palettes, decorative borders. "Classic American/British
   educational cutaway illustration, English labels, thin leader lines, muted
   ink-and-watercolor" recovered the target. Name the publishing tradition; add
   explicit negatives (no CJK text, no cute mascots, no kids-book borders).
5. **The "authentic artifact of [period] depicting [subject]" pattern is a
   superpower.** Faux patents, WPA posters, Byzantine icons, boarding passes,
   museum labels land one-shot — the model understands what the *category means*.
6. **Enumerate freely — high element-count adherence is a real strength.** It will
   render 15+ named elements, a 16-cell distinct-per-cell grid, 100+ individually
   distinguishable figures. If you need N things, list all N.
7. **Structured slot-fill templates give consistency across variants.** A fixed
   template with one style-profile swap produced cross-style consistency 4.60/5 —
   better than trying to chain reference images.
8. **Expect helpful auto-elaboration, and guard exact lettering.** It spontaneously
   adds period-appropriate sub-text and supporting detail (usually elevating), but
   will also editorialize glyphs (replaced the "I" in "SIP" with a coffee-cup).
   Great for hero pieces; risky when a client specified exact text.

## Advanced prompt patterns (general-purpose)

Complementary patterns that hold for GPT Image 2 regardless of how it's called.
Adapted from the MIT-licensed community craft guide + 162-prompt gallery at
https://github.com/wuyoscar/GPT-Image2-Skill — go there for the full style atlas.

- **Allocate the canvas before the subject.** Lead with size/aspect/grid ("Design
  a 3:4 vertical poster", "Create a square 3×3 grid") — otherwise the model spends
  its detail budget on the object and improvises the layout.
- **JSON/config-style prompts for complex renders.** For product/food hero shots
  with many interacting systems, a structured schema beats prose:
  `GLOBAL_SETTINGS` (aspect, style, render_flags like `8K_UHD`/`sharp_foreground`),
  `ENVIRONMENT` (background, lighting, atmosphere), `CORE_ASSETS` (subject,
  materials, composition), `OUTPUT` (mood, `avoid: [...]`). Keys = visual
  subsystems; values = concrete constraints, not vague praise.
- **Fixed-region layout contracts for infographics / educational boards.** Name the
  artifact type ("museum-catalog disassembly infographic", "classroom wall chart"),
  define zones (top title / left diagram / right summary / bottom legend), specify
  annotation behavior (lead lines, numbered labels), set a style boundary, and give
  exact label text. Far stronger than "make an infographic about X".
- **Diagram grammar for research/data figures.** Use structural primitives
  (columns, zones, nodes, ribbons, bars), directed relationships (arrows, feedback
  loops), exact labels, visual semantics (color meaning, thickness ∝ quantity), and
  cleanliness constraints ("large readable labels, white background, uncluttered").
  Name the chart family first ("small-multiples grid", "chord diagram").
- **UI prompts read like product specs.** Fictional product name, device/canvas
  (`1290x2796 smartphone screen`), component system, and *exact* copy/values
  ("top header with text 'AURAE', 'Total balance $12,480.36'"). Avoid bare
  `modern`/`clean`/`beautiful`.
- **Camera/capture context unlocks photorealism.** Name *how* it was shot ("RAW,
  full iPhone camera quality", "amateur iPhone photo", "28mm lens feel"). Pick ONE
  dominant capture frame — stacked camera specs conflict.
- **Scene density beats adjectives.** Name 5–12 concrete nouns + 2–4 material/
  lighting constraints instead of stacking "stunning / professional / high quality".
- **Material, lighting, and palette are separate controls** — don't compress them
  into "premium". Split: materials (brushed steel, condensation, rice paper) /
  lighting (softbox, rim light, cold blue-grey) / palette (muted teal/rust/bone).
- **Editing & multi-reference (API path only — Codex CLI is text-to-image):** state
  the change first, then pin invariants ("chess position still clearly readable");
  index each reference by role ("Image 1: product photo, Image 2: style ref") and
  say exactly how they interact.

## Text rendering

- **Essentially solved.** ~5.0/5 on text-bearing prompts, zero misspellings across
  English, Japanese (kanji+hiragana), and Latin binomials/Roman numerals. Dense
  multi-field layouts (boarding passes, menus with prices, catalog cards) hold.
- **Put literal text in quotes or ALL CAPS** in the prompt; ALL CAPS in → ALL CAPS
  out.
- **Two failure edges:** (a) exact kerning/logo typography — it auto-elaborates
  instead of rendering literally (Ideogram won that niche in the source evals,
  but per-image APIs were descoped 2026-07-13: **no routable alternative
  exists — surface the ceiling to the human instead of forcing the lane**).
  (b) Long foreign-language / dead-language captions read as authentic but
  contain cropped or invented words — treat as *decorative*, not accurate.
- It **decisively beats Flux** on text (Flux silently corrupts, e.g. "FIRST SIP
  P CLUB").

## Backgrounds & transparency

- **No transparency, ever.** Every output is opaque RGB PNG on a white background,
  no alpha — a confirmed regression from gpt-image-1.5. For sticker prompts it
  fakes a white die-cut border instead of real alpha.
- **Fine for DTG** (presses flatten to opaque ink). **Fatal for cut-line products**
  (kiss-cut sticker, phone case, die-cut): insert a background-removal hop between
  the model and the vendor to get RGBA with a clean alpha edge.
- **AOP:** white can't be printed on all-over-print — author full-bleed,
  edge-to-edge, no white background/border.

## Aspect ratio & resolution

- **Native output ~1.5 MP**, typically **1254×1254** square, or 1024×1536 /
  1536×1024 when the prompt implies a poster/scene. The CLI exposes no size flag —
  request size in prompt text (e.g. `Output size: 1536x1024`). Ratios 3:1→1:3.
- **~1.5 MP is below 150 DPI for large-format/AOP.** Upscale 4× with Real-ESRGAN
  (`nightmareai/real-esrgan`) — it preserves linework without inventing detail.
  Avoid `clarity-upscaler` for illustration (it over-synthesizes and drifts).

## Reference images & consistency

- **The "8 coherent images / character persistence" claim does NOT hold for
  multi-pose identity.** Faces drift by frame 2, clearly different by frame 4, and
  drift across sessions (the model doesn't store characters). Anchor *every* frame
  with a canonical reference PNG.
- **Through Codex CLI, reference passing is mediated by the agent's verbal
  re-description unless you attach with `-i`.** Even with `-i`, style survives but
  exact visual identity is weaker than true pixel conditioning (Flux-Redux).
- **For multi-panel coherence, make it mechanical, not model-dependent.** Winning
  patterns: (a) one master panorama → deterministic crop into panels (continuity is
  guaranteed — they're slices of one image); (b) 3D UV bake (motifs cross seams by
  construction). Chaining references does not solve seam/edge continuity.

## Where it wins / loses (routing)

- **Wins:** vintage/retro badges, dark-academia crests, botanical/celestial,
  embroidery/patch texture, art-history fusions, dense conceptual compositions,
  text-in-scene, and — uniquely — **photoreal pet-portrait tees** (photo + frame +
  exact name in one shot; the only single-shot model that does this well).
- **Loses (known ceilings, no routable alternative since the 2026-07-13
  descope — report the limitation, don't force the lane):** exact
  typography/logos (Ideogram won this in the source evals), mascot/character
  craft & pixel-art (Flux won), native vector/SVG (Recraft won; GPT Image 2
  is raster-only).
- **Content filter is narrow.** 0/60 refusals on edgy POD subject matter (skulls,
  daggers, syringe humor, tattoo flash). The filter targets IP/named-artist/real-
  photo, not dark themes.

## Printability

- **DTG:** ship white-BG RGB as-is. But deterministic gates miss real DTG problems
  — route full painted/textured backgrounds, tiny-figure dense scenes (illegible
  below ~16″), and heavy flat-color grounds to poster/wall-art SKUs. Add a
  print-safe clause when targeting DTG (the model loves halftones and full bleed).
- **AOP:** upscale 4× to clear 150 DPI; keep faces/text/hero subjects out of
  seam-adjacent zones (side seam, collar, sleeve cap, bleed); front-to-back
  alignment is not guaranteed on cut-and-sew; white can't print.

## Operational mechanics (encoded in scripts/codex_image_gen.py — don't re-derive)

- **Invocation:** `codex exec --skip-git-repo-check --full-auto --cd <workdir>
  -o <last.txt> [-i <ref> ...] [--] "<prompt>"`. Prompt is the final positional
  arg (not stdin). No `--model` flag. `--` terminates the variadic `-i` before the
  prompt or it gets eaten as another image path.
- **Result channel:** the agent writes its final message to the `-o` file; parse
  the *last* `{...}` JSON line (robust to leading noise). Fall back to stdout, then
  to the workdir's `out.png`. **Never** scan `~/.codex/generated_images/` under
  concurrency — it races between workers.
- **Isolation:** one throwaway workdir per job; write only to it + the destination.
  This is what makes parallelism safe.
- **Concurrency:** **8 parallel workers** is the proven safe ceiling on a $100/mo
  plan. Write per-job log fragments and aggregate afterward — a shared run-log
  written from all workers drops entries.
- **Timeouts:** 600s text-only; **1500s+ when references are attached** (multimodal
  integration is slow — an image-anchored bible took ~1080s). Launch with
  `start_new_session=True` and kill the whole process group with SIGKILL on
  timeout — otherwise a hung `codex exec` won't die (macOS child processes hold
  stdout open past SIGKILL and look like a hang).
- **Failure handling:** classify HARD (rate/quota caps → halt after N consecutive)
  vs SOFT (stream cuts + content-filter trips → record and skip). Exit code 2 on
  hard-abort so orchestrators can stop a wave. See the token lists in the script.

## Fast troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| `stream disconnected before completion`, retries fail identically | Named-artist / real-photo / IP moderation. Rewrite to descriptive style language. |
| Many timeouts on a batch | Prompt too long (>~150 words) or refs attached with a 600s cap. Trim prompt / raise timeout to 1500s+. |
| Output is poster-oriented, not square | Aspect drift. Add `Format: square 1:1` + `centered, isolated graphic`. |
| Wrong aesthetic (CJK text, cute mascots) | Vague style words. Anchor to a specific tradition + negative constraints. |
| Sticker/phone-case cut line broken | No alpha — every output is opaque white-BG RGB. Add a background-removal hop. |
| Soft/blurry large print | ~1.5 MP native. 4× upscale with Real-ESRGAN. |
| Character face changes between frames | Model doesn't remember characters. Anchor each frame with a reference PNG (`-i`). |
| Exact wordmark got editorialized | Auto-elaboration — a known ceiling with no routable lane (descoped 2026-07-13). Surface to the human. |
