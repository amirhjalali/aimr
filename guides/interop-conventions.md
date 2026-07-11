# Interop conventions

The hard part of cross-CLI agent work is never the invocation — it's the seams.
These conventions are what every lane in this pack conforms to, and what a new
lane must adopt to be added.

## The artifact contract

Every capability dispatch terminates in a **local file path the caller has
verified exists and is non-empty** (or, for `code-implementation`, a reviewed
worktree). Providers that natively return something else must close the gap inside
their skill:

- **URL** → the skill includes the download step; the URL is an intermediate, not
  a result.
- **Job id / poll** → the skill includes the poll loop and terminal download.
- **stdout** → the skill shows the redirect; the caller captures to a file.
- **Worktree** → the artifact is the diff; the contract completes at review+merge,
  and review is never delegated.

Registry encoding: `artifact: { type: file|url|job-id, format, delivery }`. The
`delivery` field tells the caller what work remains after the provider returns.

## Cost accounting

- Pools are **accounts**, not vendors. Every dispatch logs a ledger line (see each
  skill's incantation); estimated counts are fine, unlogged spend is not.
- Confidence is always recorded (`exact` | `estimated`) and always displayed.
  Never present an estimate as a measurement.

## Failure taxonomy (uniform vocabulary across lanes)

| Class | Meaning | Caller policy |
|---|---|---|
| `moderation-block` | content refused; may be disguised (GPT Image 2 returns `stream disconnected` for named artists) | rewrite per guidance; max one unchanged retry (moderation is non-deterministic on Grok and Kling); then reroute |
| `rate-limit-hard` | credits/quota exhausted (e.g. runner exit 2) | stop the lane, log, fall to next provider |
| `rate-limit-soft` | transient throttle | backoff and retry within the lane |
| `timeout` | runner killed the call | check for a completed artifact BEFORE rerunning — killed runs frequently finished |
| `auth` | CLI not logged in | surface to the human; never retry around auth |
| `contract-miss` | artifact absent/empty/wrong format | one retry; then treat as a brief/prompt bug |

## Trust calibration for delegated results

- Literal constants and numbers from a delegated run are unverified until checked
  at source.
- Negative claims ("X doesn't exist") require active probing by the caller —
  absence can't be spot-checked by URL liveness.
- Honest failure reports ("missing required input") usually indicate a brief bug,
  not a provider bug.

## Versioning

- `registry.json` carries `version` (schema) and `updated` (content). Scores are
  meaningless without `suite` + `date`.
- Suites are immutable once published; changes mean a new suite version.
