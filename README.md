# AIMR — AI Model Routing

**A skills pack that teaches your AI agent to drive every other AI CLI — routed by
benchmarked quality, filtered by what your accounts can actually afford.**

Your planning agent (Claude Code, or any agent that reads markdown skills) loads
AIMR and gains:

- **Capability routing** — `registry.json` ranks providers per capability:
  image generation → Codex/GPT Image 2, reference edits & video previz → Grok CLI,
  code recon & delegated implementation → Codex, bulk reading/multimodal → Gemini.
  Rankings carry the suite and date that produced them, so they're auditable, not
  vibes.
- **Cross-account token budgets** — an append-only ledger tracks spend per
  *account* (Claude, Codex, Gemini, Grok, API dollars). Routing checks what's left
  before dispatching: the best provider you can afford beats the best provider.
- **The operational knowledge** — each lane's skill carries the invocation and the
  gotchas earned from real runs: the 150-word prompt timeout, the named-artist
  moderation trap disguised as a rate limit, the worktree that can't see your
  local commits, the "timed-out" generation that actually finished.
- **Benchmarks that keep it honest** — fixed task suites, anchored 0–5 rubrics,
  vision-LLM judging (pixel metrics measured at r≈0.08 vs humans — random — and
  banned). Newest ≠ best is an empirical rule here: flux-1.1-pro outscores
  flux-2-pro.

## Why

Every lab ships coordination (their agent orchestrating *tools*) and avoids
capability interoperability (their model being *substitutable*). MCP standardizes
how a model reaches tools; nothing standardizes "route this image job to whichever
provider currently wins." That layer has to come from neutral ground — someone who
can say out loud which CLI wins where. AIMR is that layer, in portable
markdown + JSON any agent can read.

## Install

**Claude Code (plugin):**

```bash
git clone https://github.com/amirhjalali/aimr
# add as a local plugin, or symlink skills/* into ~/.claude/skills/
```

Then `/aimr:route generate a vintage badge logo` or `/aimr:budget`.

**Any other agent:** point it at this repo. Everything load-bearing is markdown
and JSON: start with `skills/routing/SKILL.md`, which explains how to consume
`registry.json` and the budget ledger.

**Budget setup (recommended):**

```bash
cp budget/budget.example.json budget/budget.json  # set your real caps
python budget/budget.py status
```

## Layout

```
registry.json     capability → ranked providers (invocation, artifact, cost, score)
skills/           one lane per dir + the routing meta-skill
budget/           per-account ledger: log / status / remaining
benchmarks/       versioned suites, rubrics, judge prompts, runners
guides/           interop conventions, per-CLI setup, add-a-lane
commands/         /aimr:route and /aimr:budget (Claude Code plugin)
```

## Current lanes

| Capability | Best routable | Score (suite) | Notes |
|---|---|---|---|
| image-generation | Codex / GPT Image 2 | 4.97 (seeded, 20-archetype eval) | text-in-scene, POD, style fusion; ~119s/img |
| image-edit | Grok `image_edit` | 4.7 (seeded, 2026-07-10 benchmark) | wins the hard back-angle class |
| image-to-video | Grok `image_to_video` | 3.5 previz; Kling 3.0 web 4.8 (not scriptable) | previz headless, hero clips via human |
| code-recon | Codex `exec` read-only | 4.5 (seeded, delegation lessons) | verify constants; distrust negative claims |
| code-implementation | Codex + worktree harness | 4.3 (seeded) | review is never delegated |
| long-context-multimodal | Gemini CLI | unbenchmarked (draft) | first candidate for a pack-run suite |

`seeded` = imported from prior benchmark studies; replacing seeds with pack-run
suite scores is the standing priority. See `benchmarks/README.md`.

## Lineage

AIMR grew out of [agent-wrangler](https://github.com/amirhjalali/agent-wrangler),
a tmux-based control layer for running teams of coding agents from one terminal.
The wrangler runs the herd; AIMR packs the knowledge of *which mount to saddle for
which terrain, and what each one costs to feed* into a form any agent can carry.

## License

MIT
