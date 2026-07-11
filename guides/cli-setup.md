# Per-CLI setup and auth

What each lane needs before its first dispatch. All checks are cheap — run them
before a batch, not after a failure.

## Codex CLI (`image-generation`, `code-recon`, `code-implementation`)

- Install: `npm i -g @openai/codex` (or the current official channel).
- Auth: **ChatGPT plan login, not an API key** — `codex login`, verify with
  `codex login status`. There is no `OPENAI_API_KEY` in this path.
- Config: `~/.codex/config.toml` sets default model + reasoning effort. Image
  generation selects the model by the phrase "GPT Image 2.0" in the prompt — there
  is no model flag.
- Gotcha: exit code 2 from the bundled image runner = hard rate limit / credits
  exhausted.

## Grok CLI (`image-edit`, `image-to-video`)

- Auth: grok.com subscription session; generation is flat-cost (no per-call
  metering observed).
- Headless mode: `grok --always-approve --single "..."` — safe to run from
  subagents.
- Probe before batch: one cheap call, confirm an artifact lands. Attachment syntax
  can shift across CLI versions.

## Gemini CLI (`long-context-multimodal`) — draft lane

- Install: `npm i -g @google/gemini-cli` (or current official channel).
- Auth: Google account OAuth on first run, or `GEMINI_API_KEY` for API-key mode.
- Probe: `gemini --version && echo "2+2?" | gemini -p "answer briefly"`.
- This lane is unbenchmarked; treat the skill as a draft and verify flags against
  your installed version.

## Claude Code (the orchestrator)

- This pack assumes a planning agent is driving. Install the pack's skills by
  cloning the repo and either adding it as a plugin
  (`.claude-plugin/plugin.json` is provided) or symlinking `skills/*` into
  `~/.claude/skills/`.
- Budget: copy `budget/budget.example.json` → `budget/budget.json`, set real caps.
  Claude usage has the best data of any pool — per-session token counts live in
  Claude Code's transcript JSONLs; a Stop-hook that appends a ledger line is the
  recommended wiring (documented here rather than auto-installed: hooks that write
  files should be opted into knowingly).

## Web-UI providers (recorded, not routable)

Magnific/Seedream and Kling 3.0 appear in the registry for honesty — they win
lanes on quality but have no scriptable path. The routing skill surfaces them to
the human instead of driving them. If they ship a headless path, they get a skill
and a suite run like any other provider.
