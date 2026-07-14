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

Two speeds
----------
- Default: LOCAL ONLY. Binary + version checks, credential-file reads, and
  the codex rollout-file quota snapshot (free to read; it mirrors server
  rate-limit headers as of the last codex turn on this machine). Zero
  network calls, zero tokens, zero quota drawn.
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
codex usage     : as fresh as the last codex turn; snapshot timestamp shown
registry scores : suite+date in registry.json — not this script's business

Exit codes: 0 = report produced, at least one capability routable;
            2 = report produced, NOTHING routable;
            1 = the doctor itself failed.

Usage numbers always carry source + as-of + confidence (the registry
honesty rule applied to probe output); unknown is null, never a guess.
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
CLAUDE_CACHE_TTL_S = 180
CLAUDE_ERROR_BACKOFF_S = 1800
LOCK_STALE_S = 30

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


def _window(name: str, used_percent: float | None, resets_at_iso: str | None) -> dict:
    return {"name": name, "used_percent": used_percent, "resets_at": resets_at_iso}


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
    windows = []
    for key, name in (("five_hour", "5h"), ("seven_day", "7d")):
        block = data.get(key) or {}
        util = block.get("utilization")
        windows.append(_window(name, round(util, 1) if isinstance(util, (int, float)) else None,
                               block.get("resets_at")))
    for lim in data.get("limits") or []:
        if isinstance(lim, dict) and lim.get("kind") == "weekly_scoped":
            scope = ((lim.get("scope") or {}).get("model") or {}).get("display_name") \
                or (lim.get("scope") or {}).get("surface") or "scoped"
            windows.append(_window(f"7d:{scope}", lim.get("percent"), lim.get("resets_at")))
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
    return out


def probe_claude(home: Path, deep: bool, timeout: float, cache_dir: Path) -> dict:
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
    else:
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

    if deep and result["auth"]["status"] == "ok" and creds and creds.get("accessToken"):
        try:
            result["usage"] = _claude_usage_deep(creds["accessToken"], cache_dir, timeout)
        except Exception as e:  # noqa: BLE001 — a usage-probe bug must degrade
            result["usage"] = _usage_error(f"usage probe error: {e!r}")  # …not kill the report
    elif result["auth"]["status"] == "ok":
        result["usage"] = {
            "source": None, "windows": None,
            "note": "account quota (5h/7d/scoped) needs --deep — local files are history, not quota",
        }

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


def _codex_windows(rl: dict) -> list[dict]:
    windows = []
    for key, default_name in (("primary", "5h"), ("secondary", "7d")):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        name = _humanize_minutes(w.get("window_minutes"), default_name)
        windows.append(_window(name, w.get("used_percent"), _iso(w.get("resets_at"))))
    if not windows:
        # legacy 2025-era flat shape (pre-nested-windows rollouts)
        for key, name in (("primary_used_percent", "5h"),
                          ("secondary_used_percent", "7d")):
            if isinstance(rl.get(key), (int, float)):
                windows.append(_window(name, rl[key], None))
    return windows


def _codex_appserver_deep(timeout: float) -> dict | None:
    """Live rate limits via `codex app-server` JSON-RPC (documented surface)."""
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
            windows = []
            for key, default_name in (("primary", "5h"), ("secondary", "7d")):
                w = rl.get(key)
                if not isinstance(w, dict):
                    continue
                name = _humanize_minutes(
                    w.get("windowDurationMins", w.get("window_minutes")), default_name)
                resets = w.get("resetsAt", w.get("resets_at"))
                if isinstance(resets, (int, float)):
                    resets = _iso(resets)
                windows.append(_window(name, w.get("usedPercent", w.get("used_percent")), resets))
            if windows:
                return {"source": "app-server", "network": True, "confidence": "exact",
                        "scope": "account-wide", "as_of": _iso(_now()),
                        "age_seconds": 0, "windows": windows,
                        "plan_type": result.get("planType")}
            return None
        return None
    finally:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def probe_codex(deep: bool, timeout: float) -> dict:
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    path = shutil.which("codex")
    result: dict = {"installed": bool(path), "version": None,
                    "auth": {"status": "unknown", "method": None, "plan": None},
                    "usage": None, "verdict": "absent", "notes": []}
    if not path:
        return result
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
    if snap:
        ts, rl = snap
        age = None
        if ts:
            try:
                age = int(_now() - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
            except ValueError:
                age = None
        result["usage"] = {
            "source": "rollout-scan", "network": False, "confidence": "exact",
            "scope": "account numbers, machine-local snapshot",
            "as_of": ts, "age_seconds": age,
            "windows": _codex_windows(rl),
            "plan_type": rl.get("plan_type"),
        }
        credits = rl.get("credits")
        if isinstance(credits, dict) and credits.get("has_credits"):
            result["usage"]["credits_balance"] = credits.get("balance")
    elif deep and result["auth"]["status"] == "ok":
        result["usage"] = _codex_appserver_deep(timeout) or {
            "source": None, "windows": None,
            "note": "no rollout snapshot and app-server probe failed",
        }
    elif result["auth"]["status"] == "ok":
        result["usage"] = {
            "source": None, "windows": None,
            "note": "no codex session on this machine yet — quota appears after the first turn (or use --deep)",
        }

    result["verdict"] = {"ok": "ready", "missing": "unauthenticated"}.get(
        result["auth"]["status"], "unknown")
    return result


# ---------------------------------------------------------------- gemini

def probe_gemini(home: Path, deep: bool, timeout: float) -> dict:
    path = shutil.which("gemini")
    result: dict = {"installed": bool(path), "version": None,
                    "auth": {"status": "unknown", "method": None, "plan": None},
                    "usage": None, "verdict": "absent", "notes": []}
    if not path:
        return result
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

    if deep and result["auth"]["status"] in ("ok", "blocked"):
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


def _parse_expiry(value) -> float | None:
    """expires_at as epoch seconds; tolerate ms and ISO strings."""
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e12 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def probe_grok(deep: bool, timeout: float) -> dict:
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
    result["version"] = _version_of(binary, timeout)

    entries = _grok_auth_entries(grok_home)
    if os.environ.get("XAI_API_KEY"):
        result["auth"]["status"] = "ok"
        result["auth"]["method"] = "api-key"
    elif entries:
        expiries = [_parse_expiry(e.get("expires_at")) for e in entries]
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

    if deep and result["auth"]["status"] == "ok":
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

def build_report(registry: dict, deep: bool, timeout: float,
                 only_pools: list[str] | None) -> dict:
    home = Path.home()
    # Per-user dir: a shared /tmp/aimr would serve user A's account numbers
    # to user B (and A's 0755 dir would lock B out of fetching at all).
    uid = os.getuid() if hasattr(os, "getuid") else "u"
    cache_dir = Path(os.environ.get("AIMR_CACHE_DIR")
                     or Path(tempfile.gettempdir()) / f"aimr-{uid}")

    probes = {
        "claude-sub": lambda: probe_claude(home, deep, timeout, cache_dir),
        "codex-sub": lambda: probe_codex(deep, timeout),
        "gemini": lambda: probe_gemini(home, deep, timeout),
        "grok": lambda: probe_grok(deep, timeout),
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

    unrouted = sorted(c for c in UNROUTED_CLIS if shutil.which(c))

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(_now()),
        "mode": "deep" if deep else "local",
        "pools": pools,
        "capabilities": capabilities,
        "unrouted_clis_detected": unrouted,
    }


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
            suffix = f" (as of {age}s ago)" if age < 3600 else f" (as of {age // 3600}h ago)"
        stale = " STALE" if usage.get("stale") else ""
        return f"usage: {' · '.join(parts)}{suffix}{stale} [{usage.get('source')}]"
    if usage.get("error"):
        return f"usage: unavailable ({usage['error']})"
    if usage.get("note"):
        return f"usage: {usage['note']}"
    return ""


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
               "--deep adds network probes: claude OAuth usage (cached 180s), "
               "codex app-server, gemini+grok liveness (the grok probe draws "
               "the shared weekly pool). Reads credential files locally for "
               "expiry/plan fields (--deep also reads the claude access token "
               "to send as a Bearer header); never prints secrets. "
               "Exit codes: 0 = >=1 capability routable, 2 = nothing "
               "routable, 1 = doctor failure.")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--deep", action="store_true",
                    help="add network probes (live quota / liveness)")
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

    try:
        report = build_report(registry, deep=args.deep, timeout=args.timeout,
                              only_pools=args.pool)
    except Exception as e:  # noqa: BLE001 — the doctor must fail loudly, not trace
        print(f"aimr_doctor: probe failed: {e!r}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2) if args.json else render_human(report))
    routable = any(c["best_available"] for c in report["capabilities"].values())
    return 0 if routable else 2


if __name__ == "__main__":
    sys.exit(main())
