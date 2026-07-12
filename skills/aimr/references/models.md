# Model tiers and effort levels: when-to-use

The registry's `models` table carries the numbers (quota weights, $/MTok,
effort lists, each with source + confidence). This file carries the judgment:
which tier, which effort, and the delegation economics that decide whether to
dispatch at all.

## The quota-weight system

On subscription pools, marginal dollars are a fiction — the real currency is
**quota pressure** on the shared account. Weights are relative draw per token
(for the Claude family they track the standard $/MTok price ratios — though
not always literally: sonnet-5's intro pricing is $2 in until 2026-08-31
while its weight stays 3; the registry's api_per_mtok fields are the
authoritative numbers):

| Model | Weight | Lane |
|---|---|---|
| anthropic/fable-5 | 10 | architecture, briefs, final review, synthesis, judgment |
| anthropic/opus-4.8 | 5 | diverse second-perspective reviews, judge/verify stages |
| anthropic/sonnet-5 | 3 | workhorse subagents, drafting, bounded recon |
| anthropic/haiku-4.5 | 1 | mechanical contract-gated pulls ONLY |
| openai/gpt-5.5 (codex) | 0* | typing, recon, web research, images |

Conversion rule: one fable turn ≈ 2 opus ≈ 3.3 sonnet ≈ 10 haiku of equal
size. Route work to the LOWEST-weight lane that clears the quality bar.

\* Codex's 0 is a **state, not a property**: it draws a different account
entirely, and holds only while that pool's reset headroom far exceeds draw.
When codex tokens/resets run low, re-weight it (~2–3) and re-balance.

Caveats that keep the table honest:

- **Weights are defaults, not limits.** Judge the output, not the price tag.
  If a cheap lane's output misses the bar, redo it a rung up without asking —
  escalation costs less than shipping mediocre work.
- **The top tier runs hotter than its weight per task**: thinking is always
  on and turns run long, so a hard task is often >2× an opus equivalent.
  Effort level is the throttle.
- **Never route judgment to haiku.** The contract-gated pull lane exists
  because of a real confabulation incident; the contract (fabrication-proof
  brief, verifiable outputs) is the safety mechanism, not a formality.
- **Cache reads are ~0.1×**: repeated-context loops with stable prefixes are
  much cheaper than the weights suggest — another reason to reuse a persistent
  worker instead of respawning.

## Effort heuristics

Claude effort levels (`low`/`medium`/`high`/`xhigh`/`max`; `high` ≈ omitting
the parameter) and Codex `model_reasoning_effort` behave the same way for
routing purposes: they throttle how much the model thinks, and they are the
cheapest lever you have.

- **low / medium** — mechanical or bounded work: formatting, summarization,
  scans, classification, well-briefed implementation with clear gates.
- **high** — normal engineering work: cross-file reasoning, design tradeoffs.
- **xhigh / max** — root-cause chains, migration plans, ambiguous
  investigations, anything where a wrong conclusion is expensive. Codex recon
  quality at xhigh is the benchmarked configuration; downshifting recon to
  medium trades diagnosis quality for speed.

Do NOT invent per-effort scores: benchmarks bind to the model+effort actually
run, and unbenchmarked effort variants stay unscored. Pick effort with these
heuristics, not with fabricated numbers.

## Delegation economics (decide before dispatching)

- **Fixed handoff cost**: every boundary token is billed at least twice (brief
  written + read; report written + read), and parallel workers partially
  overlap. Codex adds ~10k tokens of per-exec overhead even for trivia.
- **Rule**: delegate only when the tokens the worker absorbs clearly exceed
  the handoff overhead. Batch small questions into one dispatch; answer
  one-liners yourself.
- **Task shape** (see SKILL.md): judgment front-loaded → brief/execute/review;
  judgment scattered → cheap executor + expensive advisor at checkpoints
  (checkpoints beat upfront planning empirically); judgment terminal → cheap
  producer + different-model verifier.

## Claude-subagent lanes: invocation notes

- Inside Claude Code: the Agent tool with `model: sonnet|opus|haiku` (and the
  agent definition's effort). Reuse a running agent via follow-up messages
  instead of spawning fresh ones — that's the cache-affinity rule.
- From other CLIs / scripts:
  `claude -p --model sonnet --effort medium "<self-contained brief>" > out.md`
  — same brief discipline as Codex: the subagent reads no conversation memory;
  inline everything it needs and demand citations/UNVERIFIED markers for
  research.
- Trust calibration is identical to every delegated lane: constants and
  negative claims are unverified until the caller checks them.
