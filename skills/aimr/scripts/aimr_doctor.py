#!/usr/bin/env python3
"""AIMR doctor — the availability layer: which lanes are routable RIGHT NOW.

Answers, typically in well under 2 seconds on a warm machine (every
subprocess is hard-capped by --timeout, default 5s, so the worst case is
bounded, not fast): which provider CLIs are installed,
which are authenticated (and on what plan), how much quota each pool has
left where that is knowable, and therefore which registry capabilities are
routable right now. The registry (quality layer: rankings, scores, weights)
moves slowly; this probe (availability layer) runs fresh every time and its
results are never written back into the registry.

Three speeds
------------
- Default: LOCAL ONLY. Binary + version checks, credential-file reads, and
  the codex rollout-file quota snapshot (free to read; it mirrors server
  rate-limit headers as of the last codex turn on this machine). Zero
  network calls, zero tokens, zero quota drawn.
- --usage: the GAUGE — "how much have I used, how much is left, when does
  it reset". Adds ONLY the free quota readouts (claude OAuth usage GET,
  codex app-server rate-limits when no rollout snapshot exists) and skips
  liveness probes AND version subprocesses, so it stays quick. No tokens,
  no quota drawn. Renders per-window used/left/reset bars plus derived
  pace and headroom; --json trims to pools only (no capability rollup).
- --deep: adds network probes, each with its own timeout:
    claude  GET api.anthropic.com/api/oauth/usage (politeness-cached 180s,
            30min error backoff — the endpoint has a known stuck-429 bug)
    codex   `codex app-server` account/rateLimits/read JSON-RPC, only when
            no local rollout snapshot exists
    gemini  one tiny liveness prompt (exit 41 = auth-dead)
    grok    one tiny liveness prompt — NOTE: this draws the shared weekly
            SuperGrok pool; the report says so when it runs

What this script reads and calls (consent posture)
--------------------------------------------------
Reads locally: ~/.claude/.credentials.json (expiry/plan fields; under
--deep the accessToken is read and sent as a Bearer header to Anthropic's
usage endpoint), $CODEX_HOME/auth.json (JWT plan claim),
$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl (token_count.rate_limits),
~/.gemini/{settings,oauth_creds,google_accounts}.json (auth method/expiry),
$GROK_HOME/auth.json (expiry only). Secrets are never printed or logged.
Network calls happen ONLY under --deep, listed above. The usage cache lives
in a per-user temp dir (aimr-<uid>; AIMR_CACHE_DIR to relocate), mode 0700.

Freshness contract (one rule per signal)
----------------------------------------
install/version : probed every run, never persisted
auth state      : probed every run (local file reads), never persisted
claude usage    : politeness cache (180s success / 30min error backoff),
                  stored under the system temp dir (AIMR_CACHE_DIR to move)
codex usage     : as fresh as the last codex turn; snapshot timestamp shown.
                  When no snapshot describes a live window, usage/deep modes
                  fall through to `codex app-server` (politeness-cached 180s
                  in the same temp dir)
registry scores : suite+date in registry.json — not this script's business

JSON shape: `mode` is the shape discriminator — usage-mode reports carry
pools only (no capabilities / unrouted_clis_detected keys); local and deep
reports carry all sections. schema_version covers field semantics, not
which sections a mode includes.

Exit codes: 0 = report produced, at least one capability routable;
            2 = report produced, NOTHING routable;
            1 = the doctor itself failed.
            (--usage mode does not assess routability: 0 = gauge produced,
            1 = doctor failure.)

Usage numbers always carry source + as-of + confidence (the registry
honesty rule applied to probe output); unknown is null, never a guess.
Derived window fields (left_percent, resets_in_seconds, pace,
time_to_exhaustion_seconds, headroom) are computed from a SINGLE reading —
no history is kept, nothing is persisted, nothing gates. Pace assumes
linear burn across the window (a stated heuristic, confidence: derived);
headroom labels are percent-only (>=90 critical, >=70 tight) — read
resets_in_seconds beside them: a critical window minutes from reset is
self-healing. The doctor reports; the routing skill decides.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

CLAUDE_USAGE_URL = os.environ.get(
    "AIMR_CLAUDE_USAGE_URL", "https://api.anthropic.com/api/oauth/usage"
)
# The User-Agent is load-bearing on this endpoint: non-claude-code UAs land
# in an aggressively rate-limited bucket (claude-code#30930 ecosystem
# consensus, checked 2026-07-15). Override with AIMR_CLAUDE_UA if it drifts.
CLAUDE_USAGE_UA = os.environ.get("AIMR_CLAUDE_UA", "claude-code/2.1.210")
CLAUDE_CACHE_TTL_S = 180
CLAUDE_ERROR_BACKOFF_S = 1800
LOCK_STALE_S = 30

DERIVED_FIELDS_NOTE = (
    "left/resets_in/pace/headroom are single-reading derivations "
    "(confidence: derived, never gates). Pace assumes even burn with the "
    "window start inferred from resets_at — weakest on weekly windows, "
    "whose upstream mechanics are contested; on rolling windows resets_at "
    "marks when the constraint clears, not when the window started."
)

# Agent CLIs we detect but do not route — candidate lanes, presence-only.
UNROUTED_CLIS = (
    "agy", "aider", "amp", "cline", "copilot", "crush",
    "cursor-agent", "droid", "goose", "kimi", "opencode", "qwen",
)

FIX_HINTS = {
    ("claude", "absent"): "npm install -g @anthropic-ai/claude-code",
    ("claude", "unauthenticated"): "run `claude` once and log in",
    ("claude", "auth-expired"): "run `claude` once to refresh the login",
    ("codex", "absent"): "npm install -g @openai/codex",
    ("codex", "unauthenticated"): "codex login",
    ("codex", "auth-expired"): "codex login",
    ("gemini", "absent"): "npm install -g @google/gemini-cli",
    ("gemini", "unauthenticated"): "set GEMINI_API_KEY, or use an org Code Assist seat (+GOOGLE_CLOUD_PROJECT)",
    ("gemini", "blocked"): "individual Google-account tiers are server-blocked since 2026-06-18: set GEMINI_API_KEY, or use an org Code Assist seat (+GOOGLE_CLOUD_PROJECT)",
    ("grok", "absent"): "npm install -g @xai-official/grok",
    ("grok", "unauthenticated"): "grok login --device-code",
    ("grok", "auth-expired"): "grok login --device-code",
}


# ---------------------------------------------------------------- utilities

def _now() -> float:
    return time.time()


def _iso(epoch_s: float | int | None) -> str | None:
    if epoch_s is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch_s), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (OverflowError, OSError, ValueError):
        return None


def _run(cmd: list[str], timeout: float) -> tuple[int | None, str, str]:
    """Run a command with a hard timeout. Returns (rc, stdout, stderr);
    rc None means the binary is missing or the run timed out/errored."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, "", ""


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _version_of(binary: str, timeout: float, args: tuple[str, ...] = ("--version",)) -> str | None:
    rc, out, err = _run([binary, *args], timeout)
    if rc != 0:
        return None
    line = (out or err).strip().splitlines()
    return line[0].strip() if line else None


def _humanize_minutes(minutes: float | int | None, fallback: str) -> str:
    if not isinstance(minutes, (int, float)) or minutes <= 0:
        return fallback
    if minutes % 1440 == 0:
        return f"{int(minutes // 1440)}d"
    if minutes % 60 == 0:
        return f"{int(minutes // 60)}h"
    return f"{int(minutes)}m"


def _humanize_seconds(seconds: float | int | None) -> str:
    """Compact countdown: 45s, 12m, 1h40m, 3d2h."""
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return "?"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return f"{h}h{m}m" if m else f"{h}h"
    d, h = divmod(s // 3600, 24)
    return f"{d}d{h}h" if h else f"{d}d"


def _parse_epoch_or_iso(value) -> float | None:
    """Timestamp as epoch seconds; tolerates epoch-s, epoch-ms, ISO strings.
    Timezone-naive ISO strings are read as UTC — every producer here (the
    OAuth endpoint, codex rollouts) emits UTC wall-clock times."""
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e12 else float(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _window(name: str, used_percent: float | None, resets_at_iso: str | None,
            window_minutes: float | int | None = None,
            as_of_epoch: float | None = None) -> dict:
    return _enrich_window({"name": name, "used_percent": used_percent,
                           "resets_at": resets_at_iso,
                           "window_minutes": window_minutes}, as_of_epoch)


PACE_MIN_ELAPSED_FRACTION = 0.05  # below this the pace denominator is noise
HEADROOM_TIGHT = 70.0
HEADROOM_CRITICAL = 90.0


def _enrich_window(w: dict, as_of_epoch: float | None = None) -> dict:
    """Derive left/reset/pace/headroom from ONE reading — no history, no
    persistence, no gates (the no-budget-machinery rule).

    pace = fraction-used / fraction-of-window-elapsed, with the window start
    inferred as resets_at minus window length. >1x burns faster than the
    window replenishes; time_to_exhaustion extrapolates the same line.
    Linear-burn on a rolling window is a stated heuristic (confidence:
    derived) — skipped while <5% of the window has elapsed. headroom is
    percent-only; consumers must read resets_in_seconds beside it (a
    critical window minutes from reset is self-healing).

    Timebases: used_percent is a fact AS OF the reading (as_of_epoch), so
    pace's elapsed fraction is measured there too — mixing in wall-clock-now
    would understate burn on aged snapshots. resets_in_seconds and the
    exhaustion projection are now-facts (countdowns for the caller)."""
    used = w.get("used_percent")
    if isinstance(used, (int, float)):
        w["left_percent"] = round(max(0.0, 100.0 - used), 1)
    reset_epoch = _parse_epoch_or_iso(w.get("resets_at"))
    now = _now()
    as_of = as_of_epoch if isinstance(as_of_epoch, (int, float)) else now
    if reset_epoch is not None:
        if reset_epoch <= now:
            # the reading predates its own window reset: used_percent is
            # stale-high and the true number is unknowable without a re-probe
            w["already_reset"] = True
        else:
            w["resets_in_seconds"] = int(reset_epoch - now)
    mins = w.get("window_minutes")
    if (isinstance(used, (int, float)) and used >= 1.0
            and isinstance(mins, (int, float)) and mins > 0
            and reset_epoch is not None and not w.get("already_reset")):
        window_s = mins * 60.0
        elapsed_s = window_s - (reset_epoch - as_of)
        if PACE_MIN_ELAPSED_FRACTION * window_s <= elapsed_s <= window_s:
            w["pace"] = round((used / 100.0) / (elapsed_s / window_s), 2)
            if used >= 100.0:
                w["time_to_exhaustion_seconds"] = 0
            else:
                # exhaustion projected from the reading, expressed as a
                # now-countdown; an expired projection is omitted, not lied
                tte_now = elapsed_s * (100.0 - used) / used - (now - as_of)
                if tte_now > 0:
                    w["time_to_exhaustion_seconds"] = int(tte_now)
    if not w.get("already_reset") and isinstance(used, (int, float)):
        w["headroom"] = ("critical" if used >= HEADROOM_CRITICAL
                         else "tight" if used >= HEADROOM_TIGHT else "ok")
    else:
        w["headroom"] = None
    return w


_HEADROOM_RANK = {"ok": 0, "tight": 1, "critical": 2}


def _pool_headroom(usage: dict | None) -> dict | None:
    """Worst window's label, naming the window that drove it. None when the
    pool has no numeric readings."""
    if not usage or not usage.get("windows"):
        return None
    worst = None
    for w in usage["windows"]:
        level = w.get("headroom")
        if level and (worst is None
                      or _HEADROOM_RANK[level] > _HEADROOM_RANK[worst["level"]]):
            worst = {"level": level, "window": w["name"],
                     "used_percent": w.get("used_percent")}
    return worst


# ---------------------------------------------------------------- claude

def _claude_creds(home: Path, timeout: float) -> dict | None:
    creds = _read_json(home / ".claude" / ".credentials.json")
    if creds and isinstance(creds.get("claudeAiOauth"), dict):
        return creds["claudeAiOauth"]
    if sys.platform == "darwin":
        rc, out, _ = _run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            timeout,
        )
        if rc == 0 and out.strip():
            try:
                blob = json.loads(out)
                if isinstance(blob.get("claudeAiOauth"), dict):
                    return blob["claudeAiOauth"]
            except json.JSONDecodeError:
                return None
    return None


def _usage_error(msg: str) -> dict:
    return {"source": "oauth-endpoint", "network": True, "windows": None, "error": msg}


def _valid_usage_cache(cached: dict | None) -> bool:
    """A cache entry is only trusted with the exact expected shape; anything
    else (older schema, corruption, planted file) is a cache miss — a bad
    cache must degrade the claude usage row, never crash the doctor."""
    return (isinstance(cached, dict)
            and isinstance(cached.get("data"), dict)
            and isinstance(cached.get("fetched_at"), (int, float))
            and cached["fetched_at"] <= _now() + 60)


def _claude_usage_deep(token: str, cache_dir: Path, timeout: float) -> dict:
    """Fetch the OAuth usage endpoint with monclaude-style politeness:
    180s success cache, 30min backoff after a FAILED fetch (outcome marker,
    not attempt time), mkdir lock, last-good kept."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        return _usage_error(f"cache dir {cache_dir} is not writable (set AIMR_CACHE_DIR)")
    cache = cache_dir / "claude-usage.json"
    failed = cache_dir / "claude-usage.failed"
    lock = cache_dir / "claude-usage.lock"

    cached = _read_json(cache)
    if not _valid_usage_cache(cached):
        cached = None
    if cached and _now() - cached["fetched_at"] < CLAUDE_CACHE_TTL_S:
        return _claude_usage_shape(cached["data"], cached["fetched_at"])

    def serve_stale_or(error_msg: str) -> dict:
        if cached:
            return _claude_usage_shape(cached["data"], cached["fetched_at"], stale=True)
        return _usage_error(error_msg)

    def record_failure_and_serve(error_msg: str) -> dict:
        try:
            failed.touch()
        except OSError:
            pass
        return serve_stale_or(error_msg)

    # Backoff keys on the last FAILURE (marker cleared on success), so a
    # healthy 180s cadence never masks the endpoint's stuck-429 state.
    try:
        failed_recently = _now() - failed.stat().st_mtime < CLAUDE_ERROR_BACKOFF_S
    except OSError:
        failed_recently = False
    if failed_recently:
        return serve_stale_or(
            "backing off after a recent failed fetch (stuck-429 politeness)")

    # mkdir lock so concurrent doctors make one fetch.
    got_lock = False
    try:
        try:
            lock_stat = lock.stat()
        except OSError:
            lock_stat = None
        if lock_stat and _now() - lock_stat.st_mtime > LOCK_STALE_S:
            try:
                lock.rmdir()
            except OSError:
                pass
        try:
            lock.mkdir()
            got_lock = True
        except PermissionError:
            return serve_stale_or(
                f"cache dir {cache_dir} is not writable by this user (set AIMR_CACHE_DIR)")
        except OSError:
            return serve_stale_or("another probe holds the fetch lock; retry shortly")

        req = urllib.request.Request(
            CLAUDE_USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "Accept": "application/json",
                "User-Agent": CLAUDE_USAGE_UA,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=min(timeout, 10)) as resp:
                data = json.load(resp)
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as e:
            return record_failure_and_serve(f"fetch failed: {getattr(e, 'reason', e)}")
        if not isinstance(data, dict) or "five_hour" not in data:
            return record_failure_and_serve("endpoint returned an unexpected payload")
        fetched_at = _now()
        tmp = cache.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"fetched_at": fetched_at, "data": data}))
            os.chmod(tmp, 0o600)
            tmp.replace(cache)
            failed.unlink(missing_ok=True)
        except OSError:
            pass
        return _claude_usage_shape(data, fetched_at)
    finally:
        if got_lock:
            try:
                lock.rmdir()
            except OSError:
                pass


def _claude_usage_shape(data: dict, fetched_at: float, stale: bool = False) -> dict:
    limits = [lim for lim in (data.get("limits") or []) if isinstance(lim, dict)]
    # The endpoint ships its own per-limit severity classification — the
    # provider's ground truth, passed through verbatim beside our derived
    # headroom (limits[].kind session/weekly_all mirror five_hour/seven_day).
    severity_by_kind = {lim["kind"]: lim["severity"] for lim in limits
                        if lim.get("kind") and lim.get("severity")}
    windows = []
    for key, name, mins, kind in (("five_hour", "5h", 300, "session"),
                                  ("seven_day", "7d", 10080, "weekly_all")):
        block = data.get(key) or {}
        util = block.get("utilization")
        w = _window(name, round(util, 1) if isinstance(util, (int, float)) else None,
                    block.get("resets_at"), window_minutes=mins, as_of_epoch=fetched_at)
        if kind in severity_by_kind:
            w["severity"] = severity_by_kind[kind]
        windows.append(w)
    for lim in limits:
        if lim.get("kind") == "weekly_scoped":
            scope = ((lim.get("scope") or {}).get("model") or {}).get("display_name") \
                or (lim.get("scope") or {}).get("surface") or "scoped"
            w = _window(f"7d:{scope}", lim.get("percent"), lim.get("resets_at"),
                        window_minutes=10080, as_of_epoch=fetched_at)
            if lim.get("severity"):
                w["severity"] = lim["severity"]
            windows.append(w)
    extra = data.get("extra_usage") or {}
    out = {
        "source": "oauth-endpoint",
        "network": True,
        "confidence": "exact",
        "scope": "account-wide",
        "as_of": _iso(fetched_at),
        "age_seconds": int(_now() - fetched_at),
        "stale": stale,
        "windows": windows,
    }
    if extra.get("is_enabled"):
        out["extra_usage"] = {
            "used_credits": extra.get("used_credits"),
            "monthly_limit": extra.get("monthly_limit"),
        }
    out["headroom"] = _pool_headroom(out)
    return out


def probe_claude(home: Path, net_usage: bool, timeout: float, cache_dir: Path,
                 versions: bool = True) -> dict:
    path = shutil.which("claude")
    result: dict = {"installed": bool(path), "version": None,
                    "auth": {"status": "unknown", "method": None, "plan": None},
                    "usage": None, "verdict": "absent", "notes": []}
    in_session = os.environ.get("CLAUDECODE") == "1"
    if not path:
        if in_session:
            result["notes"].append(
                "claude CLI not on PATH, but this IS a Claude Code session — "
                "claude-subagent lanes route via the in-session Agent tool")
        else:
            result["verdict"] = "absent"
    elif versions:
        result["version"] = _version_of("claude", timeout)

    creds = _claude_creds(home, timeout)
    if creds:
        expires_ms = creds.get("expiresAt")
        plan = creds.get("subscriptionType")
        tier = creds.get("rateLimitTier")
        result["auth"]["method"] = "oauth"
        result["auth"]["plan"] = f"{plan} ({tier})" if plan and tier else plan or tier
        if isinstance(expires_ms, (int, float)) and expires_ms / 1000 > _now():
            result["auth"]["status"] = "ok"
            result["auth"]["expires_at"] = _iso(expires_ms / 1000)
        elif isinstance(expires_ms, (int, float)):
            result["auth"]["status"] = "expired"
        else:
            result["auth"]["status"] = "ok"  # creds present, no expiry readable
    else:
        result["auth"]["status"] = "missing"

    if net_usage and result["auth"]["status"] == "ok" and creds and creds.get("accessToken"):
        try:
            result["usage"] = _claude_usage_deep(creds["accessToken"], cache_dir, timeout)
        except Exception as e:  # noqa: BLE001 — a usage-probe bug must degrade
            result["usage"] = _usage_error(f"usage probe error: {e!r}")  # …not kill the report
    elif result["auth"]["status"] == "ok":
        if net_usage:
            # already in a usage-probing mode, so the only way here is creds
            # without a readable accessToken — pointing back at the flag the
            # user just passed would be circular advice
            note = ("credentials contain no readable accessToken (keychain-only "
                    "or partial login state) — run `claude` once to refresh the "
                    "login, or read the statusline rate_limits block inside a "
                    "live session")
        else:
            note = ("account quota (5h/7d/scoped) needs --usage or --deep — "
                    "local files are history, not quota")
        result["usage"] = {"source": None, "windows": None, "note": note}

    usable = bool(path) or in_session
    status = result["auth"]["status"]
    if not usable:
        result["verdict"] = "absent"
    elif status == "ok":
        result["verdict"] = "ready"
    elif status == "expired":
        # In-session, the Agent tool routes regardless of the on-disk token
        # (which lapses briefly between refreshes) — same rescue as `missing`.
        result["verdict"] = "ready" if in_session else "auth-expired"
        if in_session:
            result["notes"].append(
                "on-disk OAuth token lapsed, but this IS a live Claude Code session — "
                "subagent lanes route via the Agent tool (external `claude -p` dispatch "
                "may need a refresh)")
    else:  # missing
        result["verdict"] = "ready" if in_session else "unauthenticated"
    if in_session and result["verdict"] == "ready" and not creds:
        result["notes"].append("auth assumed from the live Claude Code session")
    return result


# ---------------------------------------------------------------- codex

def _jwt_claims(token: str) -> dict | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 — any malformed JWT means "unknown plan"
        return None


def _codex_rollout_snapshot(codex_home: Path) -> tuple[str | None, dict] | None:
    """Newest rollout file's last token_count event with non-null rate_limits."""
    try:
        files = sorted(
            codex_home.glob("sessions/*/*/*/rollout-*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:3]
    except OSError:
        return None
    for f in files:
        best: tuple[str | None, dict] | None = None
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"token_count"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = e.get("payload") or {}
                    if e.get("type") == "event_msg" and payload.get("type") == "token_count":
                        rl = payload.get("rate_limits")
                        if isinstance(rl, dict):
                            best = (e.get("timestamp"), rl)
        except OSError:
            continue
        if best:
            return best
    return None


def _codex_windows(rl: dict, snapshot_epoch: float | None = None) -> list[dict]:
    windows = []
    for key, default_name in (("primary", "5h"), ("secondary", "7d")):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        name = _humanize_minutes(w.get("window_minutes"), default_name)
        resets = _iso(w.get("resets_at"))
        if resets is None and snapshot_epoch is not None \
                and isinstance(w.get("resets_in_seconds"), (int, float)):
            # 0.41–0.42-era rollouts carry the reset RELATIVE to the event
            resets = _iso(snapshot_epoch + w["resets_in_seconds"])
        windows.append(_window(name, w.get("used_percent"), resets,
                               window_minutes=w.get("window_minutes"),
                               as_of_epoch=snapshot_epoch))
    if not windows:
        # legacy 2025-era flat shape (pre-nested-windows rollouts)
        for key, name in (("primary_used_percent", "5h"),
                          ("secondary_used_percent", "7d")):
            if isinstance(rl.get(key), (int, float)):
                windows.append(_window(name, rl[key], None))
    return windows


CODEX_APPSERVER_CACHE_TTL_S = 180


def _codex_appserver_usage(timeout: float, cache_dir: Path | None) -> dict | None:
    """App-server readout with the same 180s politeness cache as the claude
    probe — the gauge is advertised as quick, and a cold app-server spawn
    costs seconds. No failure backoff needed (local process, no 429s)."""
    def shape(raw: dict, plan, fetched_at: float) -> dict:
        usage = {"source": "app-server", "network": True, "confidence": "exact",
                 "scope": "account-wide", "as_of": _iso(fetched_at),
                 "age_seconds": int(_now() - fetched_at),
                 "windows": _codex_windows(raw, fetched_at),
                 "plan_type": plan}
        usage["headroom"] = _pool_headroom(usage)
        return usage

    cache = (cache_dir / "codex-usage.json") if cache_dir else None
    if cache:
        c = _read_json(cache)
        if (isinstance(c, dict) and isinstance(c.get("fetched_at"), (int, float))
                and isinstance(c.get("raw"), dict)
                and 0 <= _now() - c["fetched_at"] < CODEX_APPSERVER_CACHE_TTL_S):
            return shape(c["raw"], c.get("plan_type"), c["fetched_at"])
    fetched = _codex_appserver_fetch(timeout)
    if not fetched:
        return None
    raw, plan = fetched
    fetched_at = _now()
    if cache:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps({"fetched_at": fetched_at, "raw": raw,
                                       "plan_type": plan}))
            os.chmod(tmp, 0o600)
            tmp.replace(cache)
        except OSError:
            pass
    return shape(raw, plan, fetched_at)


def _codex_appserver_fetch(timeout: float) -> tuple[dict, str | None] | None:
    """Live rate limits via `codex app-server` JSON-RPC (documented surface).
    Returns (rate-limit dict normalized to the rollout snake_case shape,
    plan_type) so _codex_windows stays the single window parser."""
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "aimr-doctor", "version": "1.0"}}},
        {"jsonrpc": "2.0", "method": "initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"},
    ]
    try:
        proc = subprocess.Popen(
            ["codex", "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    # communicate() bounds the WHOLE exchange (a bare readline() can block
    # past any deadline on a server that stalls without emitting a newline);
    # if the server lingers past the timeout we kill it and parse whatever
    # it already wrote.
    payload = "".join(json.dumps(m) + "\n" for m in msgs)
    try:
        try:
            out, _err = proc.communicate(input=payload, timeout=min(timeout, 15))
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                out, _err = proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                return None
        except (OSError, ValueError):
            return None
        for line in (out or "").splitlines():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") != 2:
                continue
            result = msg.get("result") or {}
            rl = result.get("rateLimits") or result
            norm: dict = {}
            for key in ("primary", "secondary"):
                w = rl.get(key)
                if not isinstance(w, dict):
                    continue
                resets = w.get("resetsAt", w.get("resets_at"))
                if isinstance(resets, str):
                    resets = _parse_epoch_or_iso(resets)
                norm[key] = {
                    "used_percent": w.get("usedPercent", w.get("used_percent")),
                    "window_minutes": w.get("windowDurationMins", w.get("window_minutes")),
                    "resets_at": resets,
                }
            return (norm, result.get("planType")) if norm else None
        return None
    finally:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def probe_codex(net_usage: bool, timeout: float, versions: bool = True,
                cache_dir: Path | None = None) -> dict:
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    path = shutil.which("codex")
    result: dict = {"installed": bool(path), "version": None,
                    "auth": {"status": "unknown", "method": None, "plan": None},
                    "usage": None, "verdict": "absent", "notes": []}
    if not path:
        return result
    if versions:
        result["version"] = _version_of("codex", timeout)

    rc, _out, err = _run(["codex", "login", "status"], timeout)
    if rc == 0:
        result["auth"]["status"] = "ok"
        first = err.strip().splitlines()
        if first and "logged in" in first[0].lower():
            result["auth"]["method"] = "api-key" if "api key" in first[0].lower() else "chatgpt"
    elif rc == 1:
        result["auth"]["status"] = "missing"
    else:
        result["auth"]["status"] = "unknown"
        result["notes"].append("`codex login status` did not answer; try `codex doctor --json`")

    auth_file = _read_json(codex_home / "auth.json")
    if auth_file:
        id_token = (auth_file.get("tokens") or {}).get("id_token")
        if isinstance(id_token, str):
            claims = _jwt_claims(id_token) or {}
            plan = (claims.get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type")
            if plan:
                result["auth"]["plan"] = plan

    snap = _codex_rollout_snapshot(codex_home)
    snap_usage = None
    if snap:
        ts, rl = snap
        snap_epoch = _parse_epoch_or_iso(ts)
        snap_usage = {
            "source": "rollout-scan", "network": False, "confidence": "exact",
            "scope": "account numbers, machine-local snapshot",
            "as_of": ts,
            "age_seconds": int(_now() - snap_epoch) if snap_epoch else None,
            "windows": _codex_windows(rl, snap_epoch),
            "plan_type": rl.get("plan_type"),
        }
        snap_usage["headroom"] = _pool_headroom(snap_usage)
        credits = rl.get("credits")
        if isinstance(credits, dict) and credits.get("has_credits"):
            snap_usage["credits_balance"] = credits.get("balance")

    authed = result["auth"]["status"] == "ok"
    # A snapshot only counts as an answer while it still describes a live
    # window; one whose windows have all reset (or parsed to nothing) must
    # not suppress the free app-server readout in usage/deep modes.
    snap_is_live = bool(snap_usage) and any(
        not w.get("already_reset") for w in snap_usage["windows"])
    if snap_usage and (snap_is_live or not (net_usage and authed)):
        result["usage"] = snap_usage
    elif net_usage and authed:
        result["usage"] = _codex_appserver_usage(timeout, cache_dir) \
            or snap_usage \
            or {"source": None, "windows": None,
                "note": "no rollout snapshot and app-server probe failed"}
    elif authed:
        result["usage"] = {
            "source": None, "windows": None,
            "note": "no codex session on this machine yet — quota appears after the "
                    "first turn (or use --usage / --deep)",
        }

    result["verdict"] = {"ok": "ready", "missing": "unauthenticated"}.get(
        result["auth"]["status"], "unknown")
    return result


# ---------------------------------------------------------------- gemini

def probe_gemini(home: Path, liveness: bool, timeout: float, versions: bool = True) -> dict:
    path = shutil.which("gemini")
    result: dict = {"installed": bool(path), "version": None,
                    "auth": {"status": "unknown", "method": None, "plan": None},
                    "usage": None, "verdict": "absent", "notes": []}
    if not path:
        return result
    if versions:
        result["version"] = _version_of("gemini", timeout)

    gdir = home / ".gemini"
    settings = _read_json(gdir / "settings.json") or {}
    selected = (((settings.get("security") or {}).get("auth")) or {}).get("selectedType")
    api_key = bool(os.environ.get("GEMINI_API_KEY"))
    creds = _read_json(gdir / "oauth_creds.json") or {}
    accounts = _read_json(gdir / "google_accounts.json") or {}
    has_seat_project = bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))

    if api_key or selected == "gemini-api-key":
        result["auth"]["status"] = "ok"
        result["auth"]["method"] = "api-key"
        if not api_key:
            result["notes"].append("settings select gemini-api-key but GEMINI_API_KEY is not set in this environment")
            result["auth"]["status"] = "missing"
    elif selected == "vertex-ai":
        result["auth"]["status"] = "ok"
        result["auth"]["method"] = "vertex"
    elif selected == "oauth-personal" or creds.get("refresh_token") or accounts.get("active"):
        result["auth"]["method"] = "oauth-personal"
        if has_seat_project:
            result["auth"]["status"] = "ok"
            result["notes"].append("GOOGLE_CLOUD_PROJECT set — assuming an org Code Assist seat")
        else:
            result["auth"]["status"] = "blocked"
            result["notes"].append(
                "individual Google-account tiers (free/Pro/Ultra) are server-blocked "
                "since 2026-06-18 on every CLI version — verdict is policy knowledge, "
                "re-verify with --deep")
    else:
        result["auth"]["status"] = "missing"

    if liveness and result["auth"]["status"] in ("ok", "blocked"):
        rc, out, _err = _run(
            ["gemini", "-p", "reply with the word ok", "--output-format", "json"], timeout)
        if rc == 0:
            result["auth"]["status"] = "ok"
            result["notes"].append("liveness probe OK (one tiny request)")
        elif rc == 41:
            result["auth"]["status"] = "blocked" if result["auth"]["method"] == "oauth-personal" else "missing"
            result["notes"].append("liveness probe: FatalAuthenticationError (exit 41)")
        elif rc is not None:
            result["notes"].append(f"liveness probe exited {rc} — see gemini's typed exit codes in setup.md")

    result["usage"] = {
        "source": None, "windows": None,
        "note": "no standing quota readout on the API-key path; per-run tokens live in "
                "--output-format json stats. Daily caps per setup.md",
    }
    result["verdict"] = {"ok": "ready", "missing": "unauthenticated",
                         "blocked": "blocked"}.get(result["auth"]["status"], "unknown")
    return result


# ---------------------------------------------------------------- grok

def _grok_auth_entries(grok_home: Path) -> list[dict]:
    data = _read_json(grok_home / "auth.json") or {}
    return [v for v in data.values() if isinstance(v, dict)]


def probe_grok(liveness: bool, timeout: float, versions: bool = True) -> dict:
    grok_home = Path(os.environ.get("GROK_HOME") or Path.home() / ".grok")
    path = shutil.which("grok")
    # npm-postinstall layouts self-copy the binary under $GROK_HOME/bin
    # without necessarily linking it onto PATH — that's still installed.
    binary = path or (str(grok_home / "bin" / "grok")
                      if os.access(grok_home / "bin" / "grok", os.X_OK) else None)
    result: dict = {"installed": bool(binary), "version": None,
                    "auth": {"status": "unknown", "method": None, "plan": None},
                    "usage": None, "verdict": "absent", "notes": []}
    if not binary:
        return result
    if not path:
        result["notes"].append(
            f"binary at {binary} is NOT on PATH — invoke it by that absolute path "
            "(or add $GROK_HOME/bin to PATH)")
    if versions:
        result["version"] = _version_of(binary, timeout)

    entries = _grok_auth_entries(grok_home)
    if os.environ.get("XAI_API_KEY"):
        result["auth"]["status"] = "ok"
        result["auth"]["method"] = "api-key"
    elif entries:
        expiries = [_parse_epoch_or_iso(e.get("expires_at")) for e in entries]
        live = [x for x in expiries if x and x > _now()]
        result["auth"]["method"] = "oidc-session"
        if live:
            result["auth"]["status"] = "ok"
            result["auth"]["expires_at"] = _iso(max(live))
        elif any(expiries):
            result["auth"]["status"] = "expired"
        else:
            result["auth"]["status"] = "ok"  # entries exist, expiry unreadable
            result["notes"].append("auth.json present but expiry unreadable — treat as probably-authenticated")
    else:
        result["auth"]["status"] = "missing"

    if liveness and result["auth"]["status"] == "ok":
        rc, out, _err = _run(
            [binary, "--no-auto-update", "-p", "ping",
             "--output-format", "json", "--max-turns", "1"], timeout)
        if rc == 0:
            result["notes"].append("liveness probe OK — NOTE: it drew the shared weekly SuperGrok pool")
        elif rc == 1 and "Not signed in" in out:
            result["auth"]["status"] = "missing"
            result["notes"].append("liveness probe: not signed in")
        elif rc is not None:
            result["notes"].append(f"liveness probe exited {rc}")

    result["usage"] = {
        "source": None, "windows": None,
        "note": "weekly shared SuperGrok pool has no scriptable percent readout — "
                "check grok.com Settings -> Usage; headless runs stamp per-run "
                "usage/total_cost_usd in their JSON output",
    }
    result["verdict"] = {"ok": "ready", "missing": "unauthenticated",
                         "expired": "auth-expired"}.get(result["auth"]["status"], "unknown")
    return result


# ------------------------------------------------------------ orchestration

def build_report(registry: dict, mode: str, timeout: float,
                 only_pools: list[str] | None) -> dict:
    home = Path.home()
    # Per-user dir: a shared /tmp/aimr would serve user A's account numbers
    # to user B (and A's 0755 dir would lock B out of fetching at all).
    uid = os.getuid() if hasattr(os, "getuid") else "u"
    cache_dir = Path(os.environ.get("AIMR_CACHE_DIR")
                     or Path(tempfile.gettempdir()) / f"aimr-{uid}")

    # Probe policy per mode. net_usage = the FREE quota readouts (claude
    # OAuth GET, codex app-server); liveness = real prompts that can draw
    # quota (gemini/grok) — usage mode never sends those. versions skipped
    # in usage mode: the gauge doesn't render them and node CLIs are slow.
    net_usage = mode in ("usage", "deep")
    liveness = mode == "deep"
    versions = mode != "usage"

    probes = {
        "claude-sub": lambda: probe_claude(home, net_usage, timeout, cache_dir,
                                           versions=versions),
        "codex-sub": lambda: probe_codex(net_usage, timeout, versions=versions,
                                         cache_dir=cache_dir),
        "gemini": lambda: probe_gemini(home, liveness, timeout, versions=versions),
        "grok": lambda: probe_grok(liveness, timeout, versions=versions),
    }
    pool_ids = list(registry.get("pools") or probes)
    if only_pools:
        pool_ids = [p for p in pool_ids if p in only_pools]

    pools: dict[str, dict] = {}
    for pid in pool_ids:
        if pid in probes:
            pools[pid] = probes[pid]()
        else:
            pools[pid] = {"installed": None, "verdict": "unknown",
                          "notes": [f"no probe implemented for pool '{pid}' — "
                                    "see registry pools entry for the manual recipe"]}
        hint = FIX_HINTS.get((registry.get("pools", {}).get(pid, {}).get("cli", pid.split("-")[0]),
                              pools[pid]["verdict"]))
        if hint:
            pools[pid]["fix"] = hint

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(_now()),
        "mode": mode,
        "derived_fields_note": DERIVED_FIELDS_NOTE,
        "pools": pools,
    }
    if mode == "usage":
        # The gauge is a usage lens: pools only, no capabilities/unrouted
        # keys — `mode` is the shape discriminator (consumers key on it, not
        # on schema_version). Routability is the full doctor's job.
        return report

    capabilities: dict[str, dict] = {}
    for cap_name, cap in (registry.get("capabilities") or {}).items():
        routable, unavailable = [], []
        for p in cap.get("providers", []):
            pool = (p.get("cost") or {}).get("pool")
            # "not-probed" ≠ "unknown": a --pool filter must not make healthy
            # pools read as broken in the capability rollup.
            verdict = pools.get(pool, {}).get("verdict", "not-probed")
            entry = {"id": p.get("id"), "pool": pool, "pool_verdict": verdict}
            if verdict == "ready":
                routable.append(entry)
            else:
                unavailable.append(entry)
        capabilities[cap_name] = {
            "best_available": routable[0]["id"] if routable else None,
            "routable": routable,
            "unavailable": unavailable,
        }
        if routable and unavailable and \
                cap.get("providers") and cap["providers"][0].get("id") != routable[0]["id"]:
            capabilities[cap_name]["substitution"] = (
                f"top-ranked {cap['providers'][0].get('id')} is unavailable "
                f"({unavailable[0]['pool_verdict']}); "
                f"best available is {routable[0]['id']} — state this substitution when routing")
        if not routable:
            reasons = {f"{e['pool']}: {e['pool_verdict']}" for e in unavailable}
            capabilities[cap_name]["blocked_reason"] = ", ".join(sorted(reasons)) or "no providers"

    report["capabilities"] = capabilities
    report["unrouted_clis_detected"] = sorted(
        c for c in UNROUTED_CLIS if shutil.which(c))
    return report


# ---------------------------------------------------------------- rendering

def _fmt_usage(usage: dict | None) -> str:
    if not usage:
        return ""
    if usage.get("windows"):
        parts = []
        for w in usage["windows"]:
            pct = w.get("used_percent")
            pct_s = f"{pct:.0f}%" if isinstance(pct, (int, float)) else "?"
            parts.append(f"{w['name']} {pct_s}")
        age = usage.get("age_seconds")
        suffix = ""
        if isinstance(age, int) and age >= 0:
            suffix = f" (as of {_humanize_seconds(age)} ago)"
        stale = " STALE" if usage.get("stale") else ""
        return f"usage: {' · '.join(parts)}{suffix}{stale} [{usage.get('source')}]"
    if usage.get("error"):
        return f"usage: unavailable ({usage['error']})"
    if usage.get("note"):
        return f"usage: {usage['note']}"
    return ""


GAUGE_BAR_WIDTH = 20


def _gauge_bar(used: float | int | None) -> str:
    if not isinstance(used, (int, float)):
        return "?" * GAUGE_BAR_WIDTH
    filled = min(GAUGE_BAR_WIDTH, max(0, round(used / 100 * GAUGE_BAR_WIDTH)))
    # only truly-0% renders empty and truly-100% renders full — 97.6% must
    # stay visually distinguishable from an exhausted window
    if used > 0:
        filled = max(1, filled)
    if used < 100:
        filled = min(GAUGE_BAR_WIDTH - 1, filled)
    return "█" * filled + "░" * (GAUGE_BAR_WIDTH - filled)


def _gauge_window_line(w: dict, name_width: int) -> str:
    used = w.get("used_percent")
    bits = [f"    {w['name']:<{name_width}} {_gauge_bar(used)}"]
    if isinstance(used, (int, float)):
        bits.append(f"{used:5.1f}% used · {w.get('left_percent', 0):5.1f}% left")
    else:
        bits.append("no reading")
    if w.get("already_reset"):
        bits.append("window has reset since this reading — true usage is lower")
    elif isinstance(w.get("resets_in_seconds"), int):
        bits.append(f"resets in {_humanize_seconds(w['resets_in_seconds'])}")
    pace = w.get("pace")
    if isinstance(pace, (int, float)):
        p = f"pace {pace:.1f}x"
        tte = w.get("time_to_exhaustion_seconds")
        resets_in = w.get("resets_in_seconds")
        if isinstance(tte, int) and isinstance(resets_in, int) and tte < resets_in:
            p += f" — empty in ~{_humanize_seconds(tte)} at this rate, before the reset"
        bits.append(p)
    if w.get("headroom") in ("tight", "critical"):
        bits.append(w["headroom"].upper())
    if w.get("severity") and w["severity"] != "normal":
        bits.append(f"server severity: {w['severity']}")
    return "  ·  ".join([bits[0] + "  " + bits[1]] + bits[2:]) if len(bits) > 1 else bits[0]


def render_gauge(report: dict) -> str:
    """The --usage lens: per-window used/left/reset bars + derived pace and
    headroom. Reports only — thresholds documented in setup.md, no gating."""
    lines = [f"AIMR gauge — {report['generated_at']} "
             "(free readouts only; no liveness probes, no quota drawn)", ""]
    summaries = []
    windows_rendered = 0
    for pid, p in report["pools"].items():
        usage = p.get("usage") or {}
        verdict = p.get("verdict", "unknown")
        auth = p.get("auth") or {}
        if usage.get("windows"):
            head = f"  {pid:<11} {verdict}"
            if auth.get("plan"):
                head += f" · {auth['plan']}"
            head += f" · {usage.get('source')}"
            age = usage.get("age_seconds")
            if isinstance(age, int) and age >= 0:
                head += f" (reading {_humanize_seconds(age)} old)"
            if usage.get("stale"):
                head += " STALE — served last-good after a failed fetch"
            lines.append(head)
            name_width = max(len(w["name"]) for w in usage["windows"])
            for w in usage["windows"]:
                lines.append(_gauge_window_line(w, name_width))
                windows_rendered += 1
            extra = usage.get("extra_usage")
            if isinstance(extra, dict):
                lines.append(f"    extra usage: {extra.get('used_credits')} of "
                             f"{extra.get('monthly_limit')} monthly credits")
            hr = usage.get("headroom")
            if hr:
                summaries.append(
                    f"{pid} {hr['level'].upper()} ({hr['window']} at "
                    f"{hr['used_percent']}% used)" if hr["level"] != "ok" else f"{pid} ok")
        elif usage.get("error"):
            lines.append(f"  {pid:<11} {verdict} — usage unavailable ({usage['error']})")
        elif usage.get("note"):
            lines.append(f"  {pid:<11} {verdict} — {usage['note']}")
        else:
            lines.append(f"  {pid:<11} {verdict} — no readout")
    lines.append("")
    if summaries:
        footer = "; ".join(summaries)
    elif windows_rendered:
        footer = ("unknown — every reading predates its own window reset; "
                  "re-probe after a fresh provider turn")
    else:
        footer = "no live quota readings on this machine"
    lines.append("  headroom: " + footer)
    return "\n".join(lines)


def render_human(report: dict) -> str:
    lines = [f"AIMR doctor — {report['generated_at']} ({report['mode']} mode"
             + ("" if report["mode"] == "deep" else "; network probes off — --deep adds live quota")
             + ")", "", "POOLS"]
    for pid, p in report["pools"].items():
        head = f"  {pid:<11} {p['verdict']:<16}"
        bits = []
        if p.get("version"):
            bits.append(p["version"])
        auth = (p.get("auth") or {}) if p.get("installed") else {}
        if auth.get("status"):
            a = f"auth {auth['status']}"
            if auth.get("plan"):
                a += f" ({auth['plan']})"
            bits.append(a)
        u = _fmt_usage(p.get("usage"))
        if u:
            bits.append(u)
        lines.append(head + "   ".join(bits))
        for note in p.get("notes") or []:
            lines.append(f"               · {note}")
        if p.get("fix"):
            lines.append(f"               fix: {p['fix']}")
    lines += ["", "CAPABILITIES"]
    for cap, c in report["capabilities"].items():
        if c["best_available"]:
            line = f"  {cap:<26} ready via {c['best_available']}"
            if c.get("substitution"):
                line += "  [SUBSTITUTION — see --json]"
        else:
            line = f"  {cap:<26} BLOCKED ({c.get('blocked_reason', 'no providers')})"
        lines.append(line)
    if report["unrouted_clis_detected"]:
        lines += ["", "Unrouted agent CLIs detected (candidate lanes, no contracts): "
                  + ", ".join(report["unrouted_clis_detected"])]
    ready = sum(1 for p in report["pools"].values() if p["verdict"] == "ready")
    routable = sum(1 for c in report["capabilities"].values() if c["best_available"])
    lines += ["", f"{ready}/{len(report['pools'])} pools ready · "
              f"{routable}/{len(report['capabilities'])} capabilities routable"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="AIMR doctor: which lanes are routable right now "
                    "(installed / authenticated / quota).",
        epilog="Default run is LOCAL-ONLY (<2s, zero network, zero quota). "
               "--usage is the quick gauge: FREE quota readouts only (claude "
               "OAuth usage cached 180s, codex rollout/app-server) — no "
               "liveness probes, no quota drawn. --deep adds everything: "
               "usage plus gemini+grok liveness (the grok probe draws the "
               "shared weekly pool). Reads credential files locally for "
               "expiry/plan fields (--usage/--deep also read the claude "
               "access token to send as a Bearer header); never prints "
               "secrets. Exit codes: 0 = >=1 capability routable, 2 = "
               "nothing routable, 1 = doctor failure (--usage: 0 = gauge "
               "produced, 1 = failure).")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    speed = ap.add_mutually_exclusive_group()
    speed.add_argument("--usage", action="store_true",
                       help="quota gauge: used/left/reset per window, free "
                            "readouts only (no liveness probes, no quota drawn)")
    speed.add_argument("--deep", action="store_true",
                       help="add all network probes (live quota + liveness)")
    ap.add_argument("--pool", action="append", default=None, metavar="POOL",
                    help="probe only this pool (repeatable)")
    ap.add_argument("--timeout", type=float, default=5.0,
                    help="per-subprocess/probe timeout in seconds (default 5; "
                         "raise for cold node CLIs or slow networks under --deep)")
    ap.add_argument("--registry", default=None,
                    help="path to registry.json (default: sibling of this script)")
    args = ap.parse_args()

    registry_path = Path(args.registry) if args.registry else \
        Path(__file__).resolve().parent.parent / "registry.json"
    registry = _read_json(registry_path)
    if not registry:
        print(f"aimr_doctor: cannot read registry at {registry_path}", file=sys.stderr)
        return 1

    mode = "deep" if args.deep else "usage" if args.usage else "local"
    try:
        report = build_report(registry, mode=mode, timeout=args.timeout,
                              only_pools=args.pool)
    except Exception as e:  # noqa: BLE001 — the doctor must fail loudly, not trace
        print(f"aimr_doctor: probe failed: {e!r}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_gauge(report) if mode == "usage" else render_human(report))
    if mode == "usage":
        return 0  # the gauge reports usage; routability is the full doctor's job
    routable = any(c["best_available"] for c in report["capabilities"].values())
    return 0 if routable else 2


if __name__ == "__main__":
    sys.exit(main())
