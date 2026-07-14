# Gemini CLI: long-context and multimodal (DRAFT)

**Registry lane**: `long-context-multimodal` → `gemini/cli` —
**unbenchmarked**. This is the pack's first candidate for a pack-run suite
(`longcontext-v1`). Until then, treat quality claims as unverified and verify
invocation flags against your installed CLI version (`gemini --help`).

**Auth reality (2026-06-18)**: ALL individual Google-account tiers (free,
AI Pro, AI Ultra) are blocked server-side on every CLI version — this lane
routes only with `GEMINI_API_KEY`, Vertex, or an org Code Assist seat
(details + typed exit codes in `setup.md`; the doctor encodes the verdict).
When the lane is `blocked`/`absent`, the registry's fallback is
`claude/subagent-bulkread` — text-only, chunked; state the substitution.

## Why this lane exists

The orchestrating agent's context is the scarcest resource in a multi-agent
workflow. Work that is mostly *reading* — digest a 4-hour transcript, answer
questions over an unfamiliar 500-file codebase, describe what happens in a
video — should not pass through it. Gemini models carry 1M+ token windows and
accept audio/video/PDF natively, which makes the CLI a read-and-distill
appliance: send the bulk material there, get back only the distillate.

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
gemini -p "reply ok" --output-format json   # exit 0 = live, 41 = auth-dead
```

Prefer `--output-format json` on every headless run: the `stats` object
carries per-run token counts per model (the lane's usage meter), and errors
come back typed. **Never send `/stats` as a prompt** — slash commands are
TUI-only and reach the model as literal text.

## Routing boundaries

- **Route here**: bulk reading/distillation, media-file understanding, "read
  everything and answer N questions" tasks.
- **Do not route here**: implementation (that's `code-implementation` → Codex
  with its worktree harness), image generation (`image-generation` lane),
  second-opinion reviews (`review-second-opinion` lane), anything needing tool
  use inside your repo's guardrails.

## Known unknowns (to resolve when benchmarking this lane)

- Faithfulness on very long inputs (lost-in-the-middle behavior) — the suite
  should plant needle facts at depth.
- Real-world exhaustion behavior on the API-key path (caps are documented —
  see `setup.md` — but measured behavior at the cap is not).
- Exact media-size ceilings per file type on the CLI path (API limits are
  documented; the CLI's wrapping behavior is not).
