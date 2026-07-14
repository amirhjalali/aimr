"""aimr_doctor.py behavior on synthetic machines.

Each test builds a fake machine: a temp HOME, a stub-bin PATH (so the real
host's CLIs are invisible), and per-CLI state dirs — then runs the doctor
as a subprocess exactly the way an agent would, asserting on the --json
report and the exit code. Nothing here touches the network: --deep tests
use local CLI stubs and a file:// override of the usage endpoint. Every
fake credential embeds SECRET_MARKER, and every run asserts the marker
never reaches stdout/stderr (the never-print-secrets constraint).
"""
from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "skills" / "aimr" / "scripts" / "aimr_doctor.py"

NOW_MS = int(time.time() * 1000)
FUTURE_MS = NOW_MS + 7 * 86400 * 1000
PAST_MS = NOW_MS - 86400 * 1000
SECRET_MARKER = "AIMR-TEST-SECRET-d0n0tpr1nt"


def make_stub(bin_dir: Path, name: str, body: str = 'echo "stub 1.0.0"\nexit 0\n') -> None:
    stub = bin_dir / name
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def run_doctor(home: Path, bin_dir: Path, *args: str, env_extra: dict | None = None):
    env = {
        "HOME": str(home),
        "PATH": str(bin_dir),
        "AIMR_CACHE_DIR": str(home / ".aimr-cache"),
        "CODEX_HOME": str(home / ".codex"),
        "GROK_HOME": str(home / ".grok"),
    }
    if env_extra:
        env.update(env_extra)
    # deliberately NOT inherited: CLAUDECODE, GEMINI_API_KEY, XAI_API_KEY,
    # GOOGLE_CLOUD_PROJECT — the fake machine starts clean.
    proc = subprocess.run(
        [sys.executable, str(DOCTOR), "--json", *args],
        capture_output=True, text=True, timeout=60, env=env, check=False,
    )
    assert proc.stdout, f"no stdout; stderr: {proc.stderr}"
    assert SECRET_MARKER not in proc.stdout, "a planted secret leaked into stdout"
    assert SECRET_MARKER not in proc.stderr, "a planted secret leaked into stderr"
    return json.loads(proc.stdout), proc.returncode


def write_claude_creds(home: Path, expires_ms: int, plan: str = "max") -> None:
    d = home / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": f"sk-{SECRET_MARKER}",
            "expiresAt": expires_ms,
            "subscriptionType": plan,
        }
    }))


def make_codex_jwt(plan: str = "plus") -> str:
    def seg(obj: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()
        return raw.rstrip("=")
    return (f"{seg({'alg': 'none'})}."
            f"{seg({'https://api.openai.com/auth': {'chatgpt_plan_type': plan}})}."
            f"{SECRET_MARKER}")


def write_codex_state(home: Path, *, plan: str = "plus",
                      used_5h: float = 12.5, used_7d: float = 40.0) -> None:
    codex_home = home / ".codex"
    (codex_home / "sessions" / "2026" / "07" / "13").mkdir(parents=True, exist_ok=True)
    (codex_home / "auth.json").write_text(json.dumps({
        "tokens": {"id_token": make_codex_jwt(plan)}
    }))
    rollout = codex_home / "sessions" / "2026" / "07" / "13" / "rollout-2026-07-13T12-00-00-abc.jsonl"
    events = [
        {"timestamp": "2026-07-13T11:59:00.000Z", "type": "session_meta", "payload": {}},
        {"timestamp": "2026-07-13T12:00:00.000Z", "type": "event_msg",
         "payload": {"type": "token_count", "info": {}, "rate_limits": None}},
        {"timestamp": "2026-07-13T12:01:00.000Z", "type": "event_msg",
         "payload": {"type": "token_count", "info": {},
                     "rate_limits": {
                         "primary": {"used_percent": used_5h, "window_minutes": 300,
                                     "resets_at": int(time.time()) + 3600},
                         "secondary": {"used_percent": used_7d, "window_minutes": 10080,
                                       "resets_at": int(time.time()) + 86400},
                         "plan_type": plan,
                     }}},
    ]
    rollout.write_text("\n".join(json.dumps(e) for e in events) + "\n")


# ------------------------------------------------------------------- tests

def test_help_smoke():
    proc = subprocess.run([sys.executable, str(DOCTOR), "--help"],
                          capture_output=True, text=True, timeout=30, check=False,
                          env={**os.environ, "COLUMNS": "200"})
    assert proc.returncode == 0
    flat = " ".join(proc.stdout.split())  # wrap-proof against terminal width
    assert "--deep" in flat and "Exit codes" in flat


def test_empty_machine_blocks_everything(tmp_path):
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    report, rc = run_doctor(home, bin_dir)
    assert rc == 2, "nothing routable must exit 2"
    assert report["schema_version"] == 1
    assert report["mode"] == "local"
    assert {p["verdict"] for p in report["pools"].values()} == {"absent"}
    assert all(c["best_available"] is None for c in report["capabilities"].values())
    assert all(c.get("blocked_reason") for c in report["capabilities"].values())
    # absent pools carry install fix hints
    assert report["pools"]["codex-sub"]["fix"].startswith("npm install")


def test_claude_ready_routes_fallback_lanes(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "claude", 'echo "9.9.9 (Claude Code)"\nexit 0\n')
    write_claude_creds(home, FUTURE_MS)
    report, rc = run_doctor(home, bin_dir)
    assert rc == 0
    claude = report["pools"]["claude-sub"]
    assert claude["verdict"] == "ready"
    assert claude["auth"]["status"] == "ok"
    assert claude["auth"]["plan"] == "max"
    # local mode: no quota numbers, an honest pointer instead
    assert claude["usage"]["windows"] is None
    assert "--deep" in claude["usage"]["note"]
    # fallback lanes carry the claude-only machine
    recon = report["capabilities"]["code-recon"]
    assert recon["best_available"] == "claude/subagent-sonnet"
    assert "codex/gpt-5.5-exec" in recon["substitution"]
    assert report["capabilities"]["image-generation"]["best_available"] is None


def test_claude_in_session_without_cli(tmp_path):
    """Inside a live Claude Code session (CLAUDECODE=1) the subagent lanes
    route via the Agent tool even with no claude binary on PATH."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    report, rc = run_doctor(home, bin_dir, env_extra={"CLAUDECODE": "1"})
    claude = report["pools"]["claude-sub"]
    assert claude["verdict"] == "ready"
    assert any("Agent tool" in n for n in claude["notes"])
    assert report["capabilities"]["code-recon"]["best_available"] == "claude/subagent-sonnet"
    assert rc == 0


def test_claude_expired_credentials(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "claude")
    write_claude_creds(home, PAST_MS)
    report, _ = run_doctor(home, bin_dir)
    assert report["pools"]["claude-sub"]["verdict"] == "auth-expired"


def test_codex_ready_with_rollout_quota(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "codex",
              'if [ "$1" = "login" ]; then echo "Logged in using ChatGPT" >&2; exit 0; fi\n'
              'echo "codex-cli 9.9.9"\nexit 0\n')
    write_codex_state(home, plan="plus", used_5h=12.5, used_7d=40.0)
    report, rc = run_doctor(home, bin_dir)
    assert rc == 0
    codex = report["pools"]["codex-sub"]
    assert codex["verdict"] == "ready"
    assert codex["auth"]["method"] == "chatgpt"
    assert codex["auth"]["plan"] == "plus"
    usage = codex["usage"]
    assert usage["source"] == "rollout-scan"
    assert usage["network"] is False
    assert usage["as_of"] == "2026-07-13T12:01:00.000Z"
    by_name = {w["name"]: w for w in usage["windows"]}
    assert by_name["5h"]["used_percent"] == 12.5
    assert by_name["7d"]["used_percent"] == 40.0
    assert by_name["5h"]["resets_at"]  # unix-s converted to ISO
    # top-ranked lanes come back to life
    assert report["capabilities"]["code-recon"]["best_available"] == "codex/gpt-5.5-exec"
    assert "substitution" not in report["capabilities"]["code-recon"]


def test_codex_unauthenticated(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "codex",
              'if [ "$1" = "login" ]; then echo "Not logged in" >&2; exit 1; fi\n'
              'echo "codex-cli 9.9.9"\nexit 0\n')
    report, rc = run_doctor(home, bin_dir)
    assert report["pools"]["codex-sub"]["verdict"] == "unauthenticated"
    assert report["pools"]["codex-sub"]["fix"] == "codex login"
    assert rc == 2


def test_gemini_oauth_personal_is_policy_blocked(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "gemini")
    gdir = home / ".gemini"; gdir.mkdir(parents=True)
    (gdir / "settings.json").write_text(json.dumps(
        {"security": {"auth": {"selectedType": "oauth-personal"}}}))
    (gdir / "oauth_creds.json").write_text(json.dumps(
        {"refresh_token": SECRET_MARKER, "expiry_date": FUTURE_MS}))
    report, _ = run_doctor(home, bin_dir)
    gem = report["pools"]["gemini"]
    assert gem["verdict"] == "blocked"
    assert "2026-06-18" in " ".join(gem["notes"])
    assert "GEMINI_API_KEY" in gem["fix"]


def test_gemini_api_key_routes(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "gemini")
    report, rc = run_doctor(home, bin_dir, env_extra={"GEMINI_API_KEY": SECRET_MARKER})
    assert report["pools"]["gemini"]["verdict"] == "ready"
    assert report["pools"]["gemini"]["auth"]["method"] == "api-key"
    assert report["capabilities"]["long-context-multimodal"]["best_available"] == "gemini/cli"
    assert rc == 0


def test_grok_session_auth(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "grok", 'echo "grok 9.9.9 (deadbeef)"\nexit 0\n')
    gdir = home / ".grok"; gdir.mkdir(parents=True)
    (gdir / "auth.json").write_text(json.dumps({
        "https://auth.x.ai::client": {
            "key": SECRET_MARKER, "refresh_token": SECRET_MARKER,
            "expires_at": int(time.time()) + 6 * 86400, "auth_mode": "oidc",
        }}))
    report, rc = run_doctor(home, bin_dir)
    grok = report["pools"]["grok"]
    assert grok["verdict"] == "ready"
    assert grok["auth"]["method"] == "oidc-session"
    assert report["capabilities"]["image-to-video"]["best_available"] == "grok/image_to_video"
    assert rc == 0


def test_grok_expired_session(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "grok")
    gdir = home / ".grok"; gdir.mkdir(parents=True)
    (gdir / "auth.json").write_text(json.dumps({
        "https://auth.x.ai::client": {"expires_at": int(time.time()) - 3600}}))
    report, _ = run_doctor(home, bin_dir)
    assert report["pools"]["grok"]["verdict"] == "auth-expired"


def test_pool_filter(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    report, _ = run_doctor(home, bin_dir, "--pool", "grok")
    assert list(report["pools"]) == ["grok"]


def test_unrouted_cli_detection(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "aider")
    make_stub(bin_dir, "droid")
    report, _ = run_doctor(home, bin_dir)
    assert report["unrouted_clis_detected"] == ["aider", "droid"]


def test_human_output_renders(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    proc = subprocess.run(
        [sys.executable, str(DOCTOR)],
        capture_output=True, text=True, timeout=60, check=False,
        env={"HOME": str(home), "PATH": str(bin_dir),
             "AIMR_CACHE_DIR": str(home / ".aimr-cache"),
             "CODEX_HOME": str(home / ".codex"), "GROK_HOME": str(home / ".grok")},
    )
    assert proc.returncode == 2
    assert "POOLS" in proc.stdout and "CAPABILITIES" in proc.stdout
    assert "BLOCKED" in proc.stdout


def test_missing_registry_fails_loudly(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(DOCTOR), "--registry", str(tmp_path / "nope.json")],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 1
    assert "cannot read registry" in proc.stderr


def test_codex_login_status_odd_exit_is_unknown(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "codex",
              'if [ "$1" = "login" ]; then exit 3; fi\necho "codex-cli 9.9.9"\nexit 0\n')
    report, _ = run_doctor(home, bin_dir)
    assert report["pools"]["codex-sub"]["verdict"] == "unknown"


# ------------------------------------------------------------- --deep paths
# Hermetic: CLI stubs + a file:// override of the usage endpoint. No network.

def test_deep_gemini_liveness_exit_41(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "gemini",
              'if [ "$1" = "--version" ]; then echo "gemini 9.9.9"; exit 0; fi\nexit 41\n')
    gdir = home / ".gemini"; gdir.mkdir(parents=True)
    (gdir / "settings.json").write_text(json.dumps(
        {"security": {"auth": {"selectedType": "oauth-personal"}}}))
    (gdir / "oauth_creds.json").write_text(json.dumps(
        {"refresh_token": SECRET_MARKER, "expiry_date": FUTURE_MS}))
    # a seat project would locally read "ok" — the live 41 must override it
    report, _ = run_doctor(home, bin_dir, "--deep", "--pool", "gemini",
                           env_extra={"GOOGLE_CLOUD_PROJECT": "some-project"})
    gem = report["pools"]["gemini"]
    assert gem["verdict"] == "blocked"
    assert any("exit 41" in n for n in gem["notes"])


def test_deep_grok_liveness_not_signed_in(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(
        bin_dir, "grok",
        'if [ "$1" = "--version" ]; then echo "grok 9.9.9 (stub)"; exit 0; fi\n'
        'echo "{\\"type\\":\\"error\\",\\"message\\":\\"Not signed in.\\"}"\nexit 1\n')
    gdir = home / ".grok"; gdir.mkdir(parents=True)
    (gdir / "auth.json").write_text(json.dumps({
        "https://auth.x.ai::client": {
            "key": SECRET_MARKER,
            "expires_at": int(time.time()) + 6 * 86400}}))
    report, _ = run_doctor(home, bin_dir, "--deep", "--pool", "grok")
    grok = report["pools"]["grok"]
    assert grok["verdict"] == "unauthenticated", \
        "live not-signed-in must override the (stale) auth.json read"


def test_deep_claude_usage_via_file_endpoint(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "claude", 'echo "9.9.9 (Claude Code)"\nexit 0\n')
    write_claude_creds(home, FUTURE_MS)
    fixture = tmp_path / "usage.json"
    fixture.write_text(json.dumps({
        "five_hour": {"utilization": 12.4, "resets_at": "2026-07-14T10:00:00Z"},
        "seven_day": {"utilization": 55.0, "resets_at": "2026-07-18T00:00:00Z"},
        "limits": [{"kind": "weekly_scoped", "percent": 31,
                    "resets_at": "2026-07-18T00:00:00Z",
                    "scope": {"model": {"display_name": "Fable"}}}],
        "extra_usage": {"is_enabled": False},
    }))
    env = {"AIMR_CLAUDE_USAGE_URL": fixture.as_uri()}
    report, rc = run_doctor(home, bin_dir, "--deep", "--pool", "claude-sub",
                            env_extra=env)
    usage = report["pools"]["claude-sub"]["usage"]
    assert usage["source"] == "oauth-endpoint"
    assert usage["confidence"] == "exact"
    by_name = {w["name"]: w for w in usage["windows"]}
    assert by_name["5h"]["used_percent"] == 12.4
    assert by_name["7d"]["used_percent"] == 55.0
    assert by_name["7d:Fable"]["used_percent"] == 31
    assert rc == 0
    # politeness cache: written 0600, and a second run serves it (same as_of)
    cache = home / ".aimr-cache" / "claude-usage.json"
    assert cache.exists()
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    report2, _ = run_doctor(home, bin_dir, "--deep", "--pool", "claude-sub",
                            env_extra=env)
    assert report2["pools"]["claude-sub"]["usage"]["as_of"] == usage["as_of"]


def test_deep_claude_malformed_cache_degrades_not_crashes(tmp_path):
    """A planted/corrupt cache file must degrade the usage row, never kill
    the whole report (the KeyError('data') regression)."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "claude", 'echo "9.9.9 (Claude Code)"\nexit 0\n')
    write_claude_creds(home, FUTURE_MS)
    cache_dir = home / ".aimr-cache"; cache_dir.mkdir(parents=True)
    (cache_dir / "claude-usage.json").write_text(
        json.dumps({"fetched_at": time.time()}))  # valid JSON, wrong shape
    fixture = tmp_path / "usage.json"
    fixture.write_text(json.dumps({
        "five_hour": {"utilization": 1.0, "resets_at": None},
        "seven_day": {"utilization": 2.0, "resets_at": None},
    }))
    report, rc = run_doctor(home, bin_dir, "--deep", "--pool", "claude-sub",
                            env_extra={"AIMR_CLAUDE_USAGE_URL": fixture.as_uri()})
    assert rc == 0, "malformed cache must not crash the doctor"
    usage = report["pools"]["claude-sub"]["usage"]
    assert usage["windows"], "wrong-shape cache is a miss; fetch must proceed"


def test_deep_claude_backoff_after_failed_fetch(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "claude", 'echo "9.9.9 (Claude Code)"\nexit 0\n')
    write_claude_creds(home, FUTURE_MS)
    dead = (tmp_path / "missing.json").as_uri()  # fetch will fail
    report, _ = run_doctor(home, bin_dir, "--deep", "--pool", "claude-sub",
                           env_extra={"AIMR_CLAUDE_USAGE_URL": dead})
    assert "fetch failed" in (report["pools"]["claude-sub"]["usage"].get("error") or "")
    assert (home / ".aimr-cache" / "claude-usage.failed").exists()
    # second run inside the backoff window must not even attempt the fetch
    report2, _ = run_doctor(home, bin_dir, "--deep", "--pool", "claude-sub",
                            env_extra={"AIMR_CLAUDE_USAGE_URL": dead})
    assert "backing off" in (report2["pools"]["claude-sub"]["usage"].get("error") or "")
