---
name: longcontext-gemini
description: "Drive the Gemini CLI headlessly for tasks that need a very large context window or native multimodal input (long transcripts, whole-codebase reads, audio/video/PDF understanding). Use when the routed lane is long-context-multimodal: summarizing or querying material too large for the orchestrating agent's own window, or when the input is a media file the orchestrator cannot ingest natively. DRAFT LANE — invocation verified against docs, not yet benchmarked; probe before batch use."
---

# Gemini CLI: long-context and multimodal (DRAFT)

**Registry lane**: `long-context-multimodal` → `gemini/cli` — **unbenchmarked**.
This is the pack's first candidate for a pack-run suite (`longcontext-v1`). Until
then, treat quality claims as unverified and verify invocation flags against your
installed CLI version (`gemini --help`).
**Cost pool**: `gemini` — log calls:
`python budget/budget.py log --pool gemini --calls 1 --event longcontext`.

## Why this lane exists

The orchestrating agent's context is the scarcest resource in a multi-agent
workflow. Work that is mostly *reading* — digest a 4-hour transcript, answer
questions over an unfamiliar 500-file codebase, describe what happens in a video —
should not pass through it. Gemini models carry 1M+ token windows and accept
audio/video/PDF natively, which makes the CLI a read-and-distill appliance: send
the bulk material there, get back only the distillate.

## Invocation (verify with a probe first)

Headless one-shot:

```bash
gemini -p "Summarize the key decisions in this transcript" < transcript.txt
```

Files in the working directory can be referenced with `@` syntax in the prompt
(`gemini -p "@meeting.mp4 list every action item with a timestamp"`); the CLI
uploads referenced media. Output is text on stdout — redirect to capture:
`gemini -p "..." > out.md 2>err.log`.

Probe pattern before relying on it (flags and @-syntax have shifted across CLI
versions):

```bash
gemini --version && echo "2+2?" | gemini -p "answer briefly"
```

## Routing boundaries

- **Route here**: bulk reading/distillation, media-file understanding, "read
  everything and answer N questions" tasks, second-opinion reviews where a
  different model family's perspective is the point.
- **Do not route here**: implementation (that's `code-implementation` → Codex with
  its worktree harness), image generation (`image-generation` lane), anything
  needing tool use inside your repo's guardrails.

## Known unknowns (to resolve when benchmarking this lane)

- Faithfulness on very long inputs (lost-in-the-middle behavior) — the suite
  should plant needle facts at depth.
- Rate/quota behavior of the free vs paid CLI tiers — until measured, budget
  entries are `confidence: estimated`.
- Exact media-size ceilings per file type on the CLI path (API limits are
  documented; the CLI's wrapping behavior is not).
