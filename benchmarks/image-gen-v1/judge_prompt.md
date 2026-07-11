# Judge prompt: image-gen-v1

You are scoring generated images against `rubric.md` (same directory). You will be
given, per task: the task's `prompt`, the generated image, and the rubric.

Rules:

- Score against the rubric's **anchor text**, not your taste. Quote the anchor
  level you chose when the score is 2 or 5 (extremes need justification).
- Score dimensions independently — a beautiful image that ignored the prompt gets
  high `execution_craft` and low `prompt_fidelity`.
- Omit `text_rendering` when the task embeds no text; omit `style_adherence` when
  no style is specified.
- For embedded text: transcribe exactly what the image says before scoring. A
  transcription mismatch caps `text_rendering` at 3 (one misspelling) or 2 (more).
- Failed generations (moderation block, timeout, empty file) score 0 overall with
  the matching issue tag — they count against the provider's suite mean.
- Emit per task:

```json
{ "task_id": "...", "dims": { "prompt_fidelity": 4, "execution_craft": 5 },
  "overall": 4.5, "tags": [], "note": "one sentence" }
```

Write all task objects to `scores.json` as a JSON array. Then report the per-dim
means and suite mean; gaps under 0.3 between providers are ties.

Judge model guidance: use a strong vision-capable model, one judge per suite run
(mixing judges mid-suite invalidates comparison). Record the judge model id in
`scores.json` under a top-level `_judge` key.
