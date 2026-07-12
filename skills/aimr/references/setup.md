# Per-CLI setup, auth, and limits

What each lane needs before its first dispatch. All checks are cheap — run
them before a batch, not after a failure. Quota notes reflect what is actually
knowable per CLI as of 2026-07 (an optional live usage manager is on the
roadmap; until then these windows + judgment are the affordability check).

## Codex CLI (`image-generation`, `code-recon`, `web-research`, `code-implementation`)

- Install: `npm i -g @openai/codex` (or the current official channel).
- Auth: **ChatGPT plan login, not an API key** — `codex login`, verify with
  `codex login status`. There is no `OPENAI_API_KEY` in this path.
- Config: `~/.codex/config.toml` sets default model + reasoning effort. Pass
  `-m gpt-5.5` explicitly on real briefs (see `recon.md`). Image generation
  selects its model by the phrase "GPT Image 2.0" in the prompt — no flag.
- Limits: subscription quota has **no headless readout** (the TUI `/status`
  screen is interactive-only; an upstream issue tracks exposing it). Treat
  remaining quota as unknown-but-large until calls start failing; the image
  runner's exit code 2 = hard rate limit / credits exhausted.

## Grok CLI (`image-edit`, `image-to-video`)

- Auth: grok.com subscription session; generation is flat-cost (no per-call
  metering observed).
- Headless mode: `grok --always-approve --single "..."` — safe to run from
  subagents.
- Probe before batch: one cheap call, confirm an artifact lands. Attachment
  syntax can shift across CLI versions.
- Limits: no usage introspection exists; moderation blocks (non-deterministic,
  ~1 in 6–8 calls) are the practical constraint, not quota.

## Gemini CLI (`long-context-multimodal`) — draft lane

- Install: `npm i -g @google/gemini-cli` (or current official channel).
- Auth: Google account OAuth on first run, or `GEMINI_API_KEY` for API-key
  mode. Quota differs by auth path.
- Probe: `gemini --version && echo "2+2?" | gemini -p "answer briefly"`.
- Limits: the free personal tier has documented daily/minute request caps
  (on the order of 1,000 requests/day — check
  `google-gemini/gemini-cli` docs/quota-and-pricing.md for current numbers);
  there is no scriptable remaining-quota readout, and quota errors from the
  API are the reliable exhaustion signal.

## Claude Code (the orchestrator + `claude-subagent` lanes)

- This pack assumes a planning agent is driving. Install by adding the repo as
  a plugin (`.claude-plugin/` is provided) or copying `skills/aimr/` into
  `~/.claude/skills/`.
- Subagent lanes need nothing extra: the Agent tool ships with Claude Code;
  from outside, `claude -p --model <tier> --effort <level>` needs only the
  `claude` CLI logged in (`claude auth status`).
- Limits: subscription plans use a 5-hour rolling window plus weekly caps;
  `/usage` shows plan bars in-app (approximate, local-history-based). Local
  session JSONLs under `~/.claude/projects/` are the best per-machine data —
  this is what community usage monitors parse, and what the roadmap usage
  manager will build on.

## Web-UI providers (`human_options` — recorded, not routable)

Magnific/Seedream and Kling 3.0 appear in the registry's `human_options` for
honesty — they win lanes on quality but have no scriptable path. The routing
skill surfaces them to the human instead of driving them. If they ship a
headless path, they get a provider entry and a suite run like any other
candidate.
