# Benchmarks

Suites keep `registry.json` honest. A ranking in the registry is only as good
as the suite, rubric, and date attached to it — that's what makes it
auditable.

**Current state (v2.0):** this directory holds the *methodology* — the
anchored rubric and judge prompt for `image-gen-v1` — but no suite runner.
Every score in the registry today is `seeded` (imported from a named prior
study) or `unbenchmarked` (`overall: null`). The runner machinery returns in
v2.2 when the first pack-run suite actually executes (`web-research-v1` or
`longcontext-v1` are the leading candidates — the gemini lane is the only
fully unbenchmarked routable lane). Committing scaffolding ahead of a real
run was cut deliberately: the honesty *rules* matter; unexercised machinery
doesn't.

## Methodology (fixed across suites)

- **Fixed tasks**: 10–20 prompts per suite spanning distinct archetypes,
  versioned with the suite (`image-gen-v1`). Changing a task means a new
  suite version — suites are immutable once published.
- **Anchored 0–5 rubrics**: every dimension has named anchors per level (see
  each suite's `rubric.md`). Adapted from two prior calibrated rubric
  programs (POD design eval; fashion/product eval).
- **Vision-LLM judging only, absolute per-dimension scoring.** Pixel-based
  quality metrics were empirically shown to correlate at r≈0.08 with human
  preference — random. Vision-LLM judges calibrated against human scores
  reached r≈0.81. Do not add pixel metrics back.
- **Scores land in the registry** with `suite`, `date`, and `dims` — never a
  bare number.

## Known v1 simplifications (upgrade paths)

- **Absolute scoring, not pairwise.** If providers ceiling at 5/5, pairwise
  preference is the fix (this exact failure was observed in the prior POD
  eval).
- **No judge recalibration in-pack.** The prior calibration machinery (CV
  weight search, bias fit, holdout memorization checks) exists and is the
  path if rankings are contested. Until then: one judge model,
  rubric-anchored, and treat small score gaps (<0.3) as ties.
- **Seeded scores** (`score.source: "seeded"`) predate the pack's suites.
  Each seeded entry names its source study. Replacing seeds with pack-run
  scores is the standing priority.
