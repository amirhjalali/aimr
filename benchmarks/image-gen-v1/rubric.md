# Rubric: image-gen-v1

Score each dimension 1–5 (0 = unusable/failed generation). Anchors below are
normative — score against the anchor text, not against your taste. If a dimension
does not apply to a task (e.g. `text_rendering` on a no-text task), omit it.

## prompt_fidelity — did it make what was asked?

1. Wrong subject or scene; the prompt is not recognizable in the output.
2. Subject present but key constraints ignored (wrong format/orientation, missing named elements).
3. Most constraints honored; one meaningful drift (style, count, composition).
4. All constraints honored with minor interpretation liberties.
5. Every stated constraint honored, including format, negative constraints, and layout.

Check: format/aspect honored · named elements all present · counts correct ·
negative constraints respected · composition as specified.

## text_rendering — if the prompt embeds text

1. Illegible or garbled glyphs.
2. Legible but multiple misspellings or dropped words.
3. Correct words, unstable kerning/weight, or one misspelling.
4. Clean and correct; minor stylistic drift from the requested treatment.
5. Exact text, exact treatment, print-ready.

## execution_craft — is it well made?

1. Broken rendering: artifacts, smearing, incoherent geometry.
2. Coherent but obviously synthetic where it shouldn't be; muddy detail.
3. Competent; survives a glance, not a close look.
4. Strong craft: clean edges, coherent lighting/palette, intentional composition.
5. Portfolio-grade; a professional could ship it untouched.

Check: edge quality · lighting coherence · palette discipline · anatomy/geometry
integrity · detail density where it matters.

## style_adherence — does it hit the requested style?

1. Wrong style family entirely (wrong priors: unrequested CJK text, mascots, borders).
2. Adjacent style, wrong era/tradition.
3. Recognizably the requested style with impurities.
4. Clearly the requested tradition; minor anachronisms.
5. Could pass as a native artifact of the requested style/tradition.

## usefulness — could you ship it?

1. Reject.
2. Reference or ideation only.
3. Usable after meaningful edit (crop, retouch, background removal).
4. Usable in a candidate set or low-stakes placement as-is.
5. Production candidate as-is for the task's stated purpose.

## Aggregation

- Per-task overall = mean of applicable dimensions.
- Suite score per provider = mean of task overalls; report per-dim means alongside.
- Gaps under 0.3 between providers are ties — say so rather than ranking on noise.

## Issue tags (attach to any task scoring ≤3 on any dim)

`wrong-format` · `garbled-text` · `style-prior-leak` · `bad-anatomy` ·
`moderation-block` · `timeout` · `aspect-drift` · `too-ai`
