# Per-CLI setup, auth, and limits

The fast path: run the doctor. Everything below it is per-CLI detail — how
to fix what the doctor flags, and the manual fallback probes for when the
script can't run.

## The doctor (run this first)

```bash
python3 <skill-dir>/scripts/aimr_doctor.py           # human table
python3 <skill-dir>/scripts/aimr_doctor.py --json    # for routing decisions
python3 <skill-dir>/scripts/aimr_doctor.py --usage   # quota gauge (free readouts only)
python3 <skill-dir>/scripts/aimr_doctor.py --deep    # everything: usage + liveness
```

- **Default run is local-only**: <2s, zero network, zero tokens, zero quota.
  It checks binaries + versions, credential files (expiry/plan fields only —
  secrets are never printed), and the codex rollout quota snapshot.
- **`--usage` is the gauge** — "how much used, how much left, when does it
  reset". It adds ONLY the free quota readouts (the Claude OAuth usage
  endpoint; codex rollout scan, falling through to `app-server` when no
  snapshot still describes a live window — both network readouts
  politeness-cached 180s) and skips liveness probes *and* version
  subprocesses, so it stays quick and draws zero quota. `--json` trims to
  pools only — `mode` is the shape discriminator, so consumers must not
  expect `capabilities` in a usage-mode report.
- **`--deep` adds everything**: the usage readouts above plus gemini/grok
  one-prompt liveness checks — **the grok probe draws the shared weekly
  SuperGrok pool**, the report says so when it runs.
- Verdicts: `ready | unauthenticated | auth-expired | blocked | absent |
  unknown`. Only `ready` lanes are routed; every failing pool carries a
  `fix` command. Exit codes: 0 = something routable, 2 = nothing routable,
  1 = the doctor itself broke (`--usage`: 0 = gauge produced, 1 = broke —
  it does not assess routability).

### Reading the gauge

```
  claude-sub  ready · max (default_claude_max_5x) · oauth-endpoint (reading 12s old)
    5h       ████████████░░░░░░░░   62.0% used ·  38.0% left  ·  resets in 1h59m  ·  pace 1.0x
    7d       ██████████████████░░   91.0% used ·   9.0% left  ·  resets in 2d23h  ·  pace 1.6x — empty in ~9h29m at this rate, before the reset  ·  CRITICAL

  headroom: claude-sub CRITICAL (7d at 91.0% used)
```

Every derived field comes from a SINGLE reading — no history, no ledger,
nothing persisted, nothing gated (the no-budget-machinery rule):

- `left_percent` = 100 − used. Percent is the honest ceiling: neither
  Claude nor Codex exposes absolute token counts for subscription pools
  (verified 2026-07-15) — anyone quoting "tokens left" on a flat plan is
  reconstructing from local history and guessing the cap.
- `resets_in_seconds` — countdown to the window reset. A reading whose
  reset already passed is flagged `already_reset` (stale-high; true usage
  is lower) and excluded from headroom.
- `pace` = fraction-used ÷ fraction-of-window-elapsed (window start
  inferred as `resets_at` − window length). >1× burns faster than the
  window replenishes; `time_to_exhaustion_seconds` extrapolates the same
  line, and the renderer warns when it lands before the reset. Linear-burn
  is a stated heuristic (same even-consumption model CodexBar ships;
  suppressed below 5% window elapsed) — treat as `confidence: derived`.
  It is **strongest on the Claude 5h window** (session-anchored; though
  `resets_at` is snapped to the top of the hour, so the inferred start is
  ±1h) and **weakest on weekly windows**: Claude's 7d utilization has been
  observed stepping down ~every 72h mid-window while `resets_at` sat a
  week out (community polling data, 2026-06), and codex windows are
  rolling — there `resets_at` marks when the constraint clears, not when
  the window started. Read weekly pace as order-of-magnitude, not a
  forecast.
- `headroom` — percent-only labels at the field-consensus thresholds
  (≥70 tight, ≥90 critical — Anthropic's statusline docs and ccusage use
  the same pair). Deliberately blind to reset proximity: a critical window
  minutes from reset is self-healing, so read `resets_in_seconds` beside
  it. Pool-level `usage.headroom` names the worst window.
- `severity` — the Claude endpoint's own per-limit classification, passed
  through verbatim when present. Provider ground truth: prefer it over the
  derived label when they disagree.

In-session alternative: Claude Code pipes `rate_limits.five_hour/seven_day
.used_percentage` + `resets_at` (epoch s) to statusline commands — same
account numbers, no extra fetch. The gauge exists for the out-of-session
and cross-pool view.
- **Freshness**: install/auth are probed every run and never persisted;
  claude usage is cached 180s (30min backoff after a failed fetch) in a
  per-user temp dir (`aimr-<uid>`, mode 0700; `AIMR_CACHE_DIR` to
  relocate); codex usage is as fresh as the
  last codex turn on this machine — the report shows the snapshot age —
  and when no snapshot window is still live, usage/deep modes fall through
  to the `app-server` readout (cached 180s in the same dir).
  Probe results are NEVER written into registry.json (the registry is the
  slow-moving quality layer; the doctor is the live availability layer).
- **Probes can lie** (vendors' own endpoints have open bugs): treat usage
  readings as soft signals — prefer a cheaper lane near a limit — never as
  hard gates.

## Claude Code (the orchestrator + `claude-subagent` lanes)

- Install: `npm install -g @anthropic-ai/claude-code`. This pack assumes a
  planning agent is driving — install AIMR by adding the repo as a plugin
  (`.claude-plugin/` is provided) or copying `skills/aimr/` into
  `~/.claude/skills/`.
- Auth: OAuth login on first run. Doctor reads
  `~/.claude/.credentials.json` (`.claudeAiOauth.expiresAt`, plan fields;
  macOS: Keychain "Claude Code-credentials"). Inside a live Claude Code
  session the subagent lanes work even if the `claude` binary isn't
  separately on PATH (the Agent tool ships with the session).
- Limits: 5h rolling window + 7d weekly caps + per-model weekly scoped caps
  (e.g. a separate Fable limit). These are **account-wide, server-metered
  across all machines and surfaces** — local session JSONLs and
  `stats-cache.json` are per-machine *history*, not quota. The only quota
  truths are the OAuth usage endpoint (`--usage` / `--deep`) and the
  statusline's `rate_limits` stdin block during a live session.
- From other CLIs: `claude -p --model <tier> --effort <level>` needs only
  the `claude` CLI logged in.

## Codex CLI (`image-generation`, `code-recon`, `web-research`, `code-implementation`)

- Install: `npm install -g @openai/codex`.
- Auth: **ChatGPT plan login, not an API key** — `codex login`, verify with
  `codex login status` (exit 0/1; mode string on stderr). `codex doctor
  --json` is the machine-readable fallback (`checks["auth.credentials"]`).
  Plan tier lives in the `$CODEX_HOME/auth.json` id_token JWT
  (`chatgpt_plan_type` claim) — the doctor decodes it read-only.
- Config: `~/.codex/config.toml` sets default model + reasoning effort. Pass
  `-m gpt-5.5` explicitly on real briefs (see `recon.md`). Image generation
  selects its model by the phrase "GPT Image 2.0" in the prompt — no flag.
- Limits: primary (~5h) and secondary (weekly) windows. **Headless readout
  exists** (the old "TUI-only" note is stale, corrected 2026-07-13): every
  codex turn persists a `token_count` event with server-header
  `rate_limits` into `$CODEX_HOME/sessions/.../rollout-*.jsonl` — the
  doctor scans it for free; `codex app-server` JSON-RPC
  `account/rateLimits/read` is the live surface (`--deep`). The image
  runner still signals hard rate limits with exit code 2 in batch mode.

## Gemini CLI (`long-context-multimodal`) — draft lane

- Install: `npm install -g @google/gemini-cli`.
- Auth — **the 2026-06-18 reality**: ALL individual Google-account tiers
  (free, AI Pro, AI Ultra) are blocked server-side on every CLI version
  (`IneligibleTierError`, pointing at the closed-source Antigravity
  successor). The lane routes only with `GEMINI_API_KEY` (AI Studio),
  Vertex AI, or an org Code Assist Standard/Enterprise seat
  (+`GOOGLE_CLOUD_PROJECT`). The doctor encodes this as policy knowledge:
  `oauth-personal` without a seat project → verdict `blocked`.
- Manual probe: `gemini -p "reply ok" --output-format json` — exit 0 = live,
  exit 41 = auth-dead (typed exit codes: 41 auth, 42 input, 44 sandbox,
  52 config, 53 turn-limit, 54 tool-exec, 55 untrusted-workspace).
  **Never send `/stats` headlessly** — it reaches the model as a prompt.
- Limits: API-key free tier 250 req/day (Flash-only); Code Assist Standard
  1,500/day, Enterprise 2,000/day (vendor docs, checked 2026-07-13). Quota
  errors are typed: `TerminalQuotaError` = daily cap (stop the lane),
  `RetryableQuotaError` = per-minute (brief backoff). Per-run token stats
  live in the headless JSON `stats` object.

## Grok CLI (`image-to-video`)

- Install: `npm install -g @xai-official/grok` (the official xAI "Grok
  Build" CLI; the x.ai install script is Cloudflare-walled to non-browsers).
- Auth: browser OAuth, or `grok login --device-code` headlessly; state in
  `$GROK_HOME/auth.json` (~7-day tokens, auto-refreshed). `XAI_API_KEY` is
  the metered alternative. **Do not trust `grok models` for auth** — it
  exits 0 signed-out; a real probe prints a JSON error and exits 1.
- Headless mode: `grok --no-auto-update --always-approve --output-format
  json --single "..."` — always pass `--no-auto-update` in scripted runs
  (update chatter on stderr, JSON on stdout). Exit codes: 0/1/130/143.
- Limits — **metered since June 2026** (the old "flat, unmetered" note is
  stale): SuperGrok uses ONE shared weekly pool across Chat/Imagine/Voice/
  Build; pool percent + reset date are human-web-only (grok.com Settings →
  Usage). Headless runs stamp per-run `usage`/`total_cost_usd` in their
  JSON. Free/X-Basic: media tools hard-fail with "Do not retry this tool" —
  lane-unavailable, not a retryable error. Moderation blocks
  (non-deterministic, ~1 in 6–8 calls) remain the other practical
  constraint.

## Web-UI providers (`human_options` — recorded, not routable)

The registry currently lists no `human_options`: web-UI-only tools
(Kling 3.0, Magnific/Seedream) were descoped 2026-07-13 along with
third-party image APIs — AIMR's scope is agent-drivable CLIs. The mechanism
stays: a quality-winning tool with no scriptable path can be recorded as a
`human_option` (never routed), and if it ships a headless path it gets a
provider entry and a suite run like any other candidate.

## Unrouted CLIs the doctor may detect

The doctor lists installed-but-unrouted agent CLIs (opencode, droid, agy,
aider, goose, qwen, amp, copilot, …) as candidate lanes. They are
presence-detected only — no contracts, no scores. Promoting one is a
CONTRIBUTING.md job: prove the headless path, earn gotchas from a real run,
add the four contracts.
