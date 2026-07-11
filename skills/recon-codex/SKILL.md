---
name: recon-codex
description: "Delegate read-only reconnaissance, web research, and well-briefed implementation work to the Codex CLI (codex exec) as a headless executor. Use when the routed lane for code-recon or code-implementation is Codex: investigating a codebase before changes, producing cited research reports, or typing out a disjoint implementation package in an isolated git worktree. Covers codex exec invocation, the worktree harness, brief-writing patterns, the review discipline the caller must never delegate, and the known failure modes (literal constants, negative claims, sandbox git restrictions)."
---

# Codex delegation: recon and implementation

**Registry lanes**: `code-recon` and `code-implementation` → `codex/gpt-5.5-*`.
**Cost pool**: `codex` — per-exec overhead is ~10k tokens even for trivia; batch
questions, then `python budget/budget.py log --pool codex --calls 1 --event recon`.

The division of labor: the orchestrating agent architects, briefs, reviews, gates,
and merges. Codex types, recons, and researches. Review is **never** delegated.

## Invocation

- **Headless one-shot**:
  `codex exec -C <dir> -s read-only|workspace-write --ephemeral -o <out.txt> - < brief.md`
- **Implementation in isolation**:
  `scripts/codex-task.sh start <branch> <brief.md> [extra codex args]` — creates a
  worktree off origin/main, installs deps, copies NO secrets. `diff|last|clean`
  subcommands. (`CODEX_SKIP_INSTALL=1` to skip installs.)
- **Iterate same session**: `codex exec resume --last -C <dir> "<correction>"` —
  only safe with ONE active codex session; don't use `--ephemeral` on runs you may
  want to resume.
- **Structured output**: `--output-schema <schema.json>` forces a JSON final message.
- **Web research**: `codex exec -s read-only --ephemeral -c tools.web_search=true`
  (config key, not a flag).
- **Effort throttle**: `-c model_reasoning_effort="medium"` downshifts mechanical
  work for speed; default to the account's configured max for recon.

## Operating pattern

1. **Recon-first**: for anything not fully understood, a read-only pass produces
   the diagnosis; that becomes the implementation brief. Group related questions
   into ONE run (per-exec overhead is real).
2. **Verify the task still exists**: recon must check CURRENT source, not trust a
   bug report or memory — stale backlog items get correctly detected as already
   fixed, saving full implementation cycles.
3. **Briefs are self-contained**: Codex reads no CLAUDE.md or conversation memory.
   Inline the repo's hard rules, verification commands, exact literal values, and
   a SAMPLE of any data file the brief references (it once assumed a flat shape
   for a nested JSON it was given only the path to).
4. **Fan out disjoint packages** to parallel worktrees; keep briefs disjoint by
   file ownership. Serialize the worktree creation + installs, then run the execs
   in parallel.
5. **Network for greenfield**: `-c sandbox_workspace_write.network_access=true`
   plus explicit "verify before you finish" gates makes it self-validate
   (install/typecheck/test/build).

## Review checklist (caller, non-delegable)

- Read the full diff; verify factual claims at file:line; run the merge gates
  yourself.
- Tests actually assert (watch for weak or tautological tests).
- Diff scope matches the brief's "Do NOT touch" list; no drive-by refactors. Big
  hunks may be prettier reflow — confirm before reading line-by-line, but check
  that "adds" are not silent "replaces" of adjacent content.
- Any changed exported signature: grep and audit ALL call sites (it once changed a
  shared default and silently truncated two other callers).
- Gate on the right signal: lint that exits 0 on warnings needs output-grepping;
  check exit codes unpiped (`PIPESTATUS`), not through `| tail`.

## Known failure modes (trust calibration)

- **Structural/causal analysis: high quality.** Root-cause chains with accurate
  file:line refs at high reasoning effort.
- **Literal constants: the weak spot.** It has confidently misreported a constant
  it had just read. Verify every number at file:line.
- **Negative claims: zero trust.** "X does not exist / could not be verified" must
  be actively probed by the caller (name permutations, profile pages, unhyphenated
  queries) before being believed — positive-claim URL checks do not cover absences.
- **Sandbox git**: workspace-write can leave `.git` read-only — plan briefs so
  Codex does file work and the caller does ALL git index/commit/branch work.
- **Worktrees branch from origin/main**: local-only commits are invisible to it;
  push first or copy referenced files into the worktree.
- **Honest reporting**: when blocked (missing inputs, sandbox denials) it reports
  honestly rather than faking — treat "missing required input" as a brief bug.
- Keep `-o` output files OUTSIDE the worktree (or reset before staging); stage by
  directory, never `git add -A`.
