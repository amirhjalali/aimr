# Benchmarks

Suites keep `registry.json` honest. A ranking in the registry is only as good as
the suite, rubric, and date attached to it — that's what makes it auditable.

## Methodology (fixed across suites)

- **Fixed tasks**: 10–20 prompts per suite spanning distinct archetypes, versioned
  with the suite (`image-gen-v1`). Changing a task means a new suite version.
- **Anchored 0–5 rubrics**: every dimension has named anchors per level (see each
  suite's `rubric.md`). Adapted from two prior calibrated rubric programs
  (POD design eval; fashion/product eval).
- **Vision-LLM judging only, absolute per-dimension scoring.** Pixel-based quality
  metrics were empirically shown to correlate at r≈0.08 with human preference —
  random. Vision-LLM judges calibrated against human scores reached r≈0.81. Do not
  add pixel metrics back.
- **Scores land in the registry** with `suite`, `date`, and `dims` — never a bare
  number.

## Running a suite

```bash
python benchmarks/image-gen-v1/runner.py --provider codex/gpt-image-2 --out runs/img-v1-$(date +%Y%m%d)
```

The runner generates all task artifacts headlessly through the lane's skill runner
and writes a `judging_manifest.json`. Judging is a separate pass: a vision-capable
agent scores each artifact against `rubric.md` using `judge_prompt.md`, writes
`scores.json`, and the aggregate (mean per dim, mean overall) is copied into the
provider's `score` block in `registry.json` with the suite id and date.

## Known v1 simplifications (upgrade paths)

- **Absolute scoring, not pairwise.** If providers ceiling at 5/5, pairwise
  preference is the fix (this exact failure was observed in the prior POD eval).
- **No judge recalibration in-pack.** The prior calibration machinery (CV weight
  search, bias fit, holdout memorization checks) exists and is the v2 path if
  rankings are contested. Until then: one judge model, rubric-anchored, and treat
  small score gaps (<0.3) as ties.
- **Seeded scores** (`score.source: "seeded"`) predate the pack's suites. Each
  seeded entry names its source study. Replacing seeds with pack-run scores is the
  standing priority — `longcontext-gemini` first (currently unbenchmarked).
