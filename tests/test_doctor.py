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
                      used_5h: float = 12.5, used_7d: float = 40.0) -> str:
    """Fresh, internally-consistent snapshot: events stamped ~now, resets in
    the future. Returns the snapshot's as_of timestamp string."""
    codex_home = home / ".codex"
    (codex_home / "sessions" / "2026" / "07" / "13").mkdir(parents=True, exist_ok=True)
    (codex_home / "auth.json").write_text(json.dumps({
        "tokens": {"id_token": make_codex_jwt(plan)}
    }))
    def iso(offset_s: int) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                             time.gmtime(time.time() + offset_s))
    snap_ts = iso(-60)
    rollout = codex_home / "sessions" / "2026" / "07" / "13" / "rollout-2026-07-13T12-00-00-abc.jsonl"
    events = [
        {"timestamp": iso(-180), "type": "session_meta", "payload": {}},
        {"timestamp": iso(-120), "type": "event_msg",
         "payload": {"type": "token_count", "info": {}, "rate_limits": None}},
        {"timestamp": snap_ts, "type": "event_msg",
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
    return snap_ts


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


def test_claude_in_session_rescues_expired_token(tmp_path):
    """The on-disk OAuth token lapses briefly between refreshes; inside a
    live session the Agent tool routes anyway — expired must not BLOCK
    the subagent lanes there (found by the opus dogfood review)."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "claude")
    write_claude_creds(home, PAST_MS)
    report, rc = run_doctor(home, bin_dir, env_extra={"CLAUDECODE": "1"})
    claude = report["pools"]["claude-sub"]
    assert claude["verdict"] == "ready"
    assert any("lapsed" in n for n in claude["notes"])
    assert report["capabilities"]["review-second-opinion"]["best_available"] \
        == "claude/subagent-opus"
    assert rc == 0


def test_codex_ready_with_rollout_quota(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "codex",
              'if [ "$1" = "login" ]; then echo "Logged in using ChatGPT" >&2; exit 0; fi\n'
              'echo "codex-cli 9.9.9"\nexit 0\n')
    snap_ts = write_codex_state(home, plan="plus", used_5h=12.5, used_7d=40.0)
    report, rc = run_doctor(home, bin_dir)
    assert rc == 0
    codex = report["pools"]["codex-sub"]
    assert codex["verdict"] == "ready"
    assert codex["auth"]["method"] == "chatgpt"
    assert codex["auth"]["plan"] == "plus"
    usage = codex["usage"]
    assert usage["source"] == "rollout-scan"
    assert usage["network"] is False
    assert usage["as_of"] == snap_ts
    by_name = {w["name"]: w for w in usage["windows"]}
    assert by_name["5h"]["used_percent"] == 12.5
    assert by_name["7d"]["used_percent"] == 40.0
    assert by_name["5h"]["resets_at"]  # unix-s converted to ISO
    # top-ranked lanes come back to life
    assert report["capabilities"]["code-recon"]["best_available"] == "codex/gpt-5.5-exec"
    assert "substitution" not in report["capabilities"]["code-recon"]


def test_codex_legacy_flat_rate_limit_shape(tmp_path):
    """2025-era rollouts carry flat primary_used_percent instead of nested
    windows — the scan must still surface the percentages."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "codex",
              'if [ "$1" = "login" ]; then echo "Logged in using ChatGPT" >&2; exit 0; fi\n'
              'echo "codex-cli 9.9.9"\nexit 0\n')
    sessions = home / ".codex" / "sessions" / "2025" / "12" / "01"
    sessions.mkdir(parents=True)
    (sessions / "rollout-2025-12-01T09-00-00-old.jsonl").write_text(json.dumps({
        "timestamp": "2025-12-01T09:00:00.000Z", "type": "event_msg",
        "payload": {"type": "token_count", "info": {},
                    "rate_limits": {"primary_used_percent": 77.0,
                                    "secondary_used_percent": 12.0}}}) + "\n")
    report, _ = run_doctor(home, bin_dir)
    by_name = {w["name"]: w for w in report["pools"]["codex-sub"]["usage"]["windows"]}
    assert by_name["5h"]["used_percent"] == 77.0
    assert by_name["7d"]["used_percent"] == 12.0


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


def test_grok_binary_off_path_still_detected(tmp_path):
    """npm-postinstall layouts leave the binary at $GROK_HOME/bin/grok
    without a PATH link — the doctor must still see it as installed."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()  # note: NO grok stub on PATH
    gbin = home / ".grok" / "bin"; gbin.mkdir(parents=True)
    make_stub(gbin, "grok", 'echo "grok 9.9.9 (offpath)"\nexit 0\n')
    report, rc = run_doctor(home, bin_dir)
    grok = report["pools"]["grok"]
    assert grok["installed"] is True
    assert grok["version"] == "grok 9.9.9 (offpath)"
    assert any("NOT on PATH" in n for n in grok["notes"])
    assert grok["verdict"] == "unauthenticated"  # no auth.json
    assert rc == 2  # nothing else on this machine


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
    # unprobed pools must read "not-probed", never as broken/unknown
    recon = report["capabilities"]["code-recon"]
    assert all(e["pool_verdict"] == "not-probed" for e in recon["unavailable"])
    assert "not-probed" in recon["blocked_reason"]


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


# ------------------------------------------------------------ --usage gauge
# The usage lens: free readouts only (claude OAuth via file:// override,
# codex rollout scan) — never liveness probes, never version subprocesses.

def write_usage_fixture(path: Path, *, used_5h: float, resets_5h_s: int,
                        used_7d: float, resets_7d_s: int,
                        limits: list | None = None) -> str:
    def iso(offset_s: int) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_s))
    path.write_text(json.dumps({
        "five_hour": {"utilization": used_5h, "resets_at": iso(resets_5h_s)},
        "seven_day": {"utilization": used_7d, "resets_at": iso(resets_7d_s)},
        "limits": limits or [],
        "extra_usage": {"is_enabled": False},
    }))
    return path.as_uri()


def test_usage_mode_trims_report_and_skips_liveness_and_versions(tmp_path):
    """--usage may spawn codex (auth read + free app-server readout) but
    must NEVER touch claude/gemini/grok binaries (no version subprocesses,
    no liveness prompts — those can draw quota). Canary stubs record every
    invocation. The JSON trims to pools only."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    for cli in ("claude", "gemini", "grok", "codex"):
        make_stub(bin_dir, cli,
                  f'echo "$@" >> "{tmp_path}/canary-{cli}"\necho "stub 1.0.0"\nexit 0\n')
    write_claude_creds(home, FUTURE_MS)
    url = write_usage_fixture(tmp_path / "usage.json", used_5h=10, resets_5h_s=3600,
                              used_7d=20, resets_7d_s=3 * 86400)
    report, rc = run_doctor(home, bin_dir, "--usage",
                            env_extra={"AIMR_CLAUDE_USAGE_URL": url,
                                       "GEMINI_API_KEY": SECRET_MARKER})
    assert rc == 0
    assert report["mode"] == "usage"
    assert "capabilities" not in report and "unrouted_clis_detected" not in report
    assert report["pools"]["claude-sub"]["usage"]["windows"]
    for cli in ("claude", "gemini", "grok"):
        assert not (tmp_path / f"canary-{cli}").exists(), \
            f"usage mode must not spawn {cli} at all"
    codex_calls = (tmp_path / "canary-codex").read_text().splitlines()
    assert all(c.startswith(("login", "app-server")) for c in codex_calls), \
        f"unexpected codex invocations in usage mode: {codex_calls}"


def test_usage_mode_derived_fields_and_headroom(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    write_claude_creds(home, FUTURE_MS)
    # 5h: 62% used, resets in 2h -> 60% elapsed -> pace ~1.03
    # 7d: 91% used, resets in 3d -> ~57% elapsed -> pace ~1.6, critical
    url = write_usage_fixture(
        tmp_path / "usage.json", used_5h=62.0, resets_5h_s=2 * 3600,
        used_7d=91.0, resets_7d_s=3 * 86400,
        limits=[{"kind": "weekly_all", "percent": 91, "severity": "elevated"},
                {"kind": "weekly_scoped", "percent": 9,
                 "resets_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime(time.time() + 3 * 86400)),
                 "scope": {"model": {"display_name": "Fable"}}}])
    report, rc = run_doctor(home, bin_dir, "--usage", "--pool", "claude-sub",
                            env_extra={"AIMR_CLAUDE_USAGE_URL": url})
    assert rc == 0
    usage = report["pools"]["claude-sub"]["usage"]
    by_name = {w["name"]: w for w in usage["windows"]}
    w5, w7 = by_name["5h"], by_name["7d"]
    assert w5["left_percent"] == 38.0
    assert 7100 < w5["resets_in_seconds"] <= 7200
    assert 0.95 < w5["pace"] < 1.1
    assert w5["headroom"] == "ok"
    assert w7["headroom"] == "critical"
    assert 1.5 < w7["pace"] < 1.7
    # burning faster than the window: projected empty before the reset
    assert w7["time_to_exhaustion_seconds"] < w7["resets_in_seconds"]
    # the server's own classification is passed through verbatim
    assert w7["severity"] == "elevated"
    assert "severity" not in w5
    assert by_name["7d:Fable"]["headroom"] == "ok"
    # pool headroom = worst window, naming the driver
    assert usage["headroom"] == {"level": "critical", "window": "7d",
                                 "used_percent": 91.0}


def test_usage_mode_young_window_suppresses_pace(tmp_path):
    """<5% of the window elapsed -> the pace denominator is noise; the
    field must be absent, not a wild number (the CodexBar suppression rule)."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    write_claude_creds(home, FUTURE_MS)
    url = write_usage_fixture(tmp_path / "usage.json",
                              used_5h=4.0, resets_5h_s=int(4.96 * 3600),
                              used_7d=50.0, resets_7d_s=7 * 86400 - 60)
    report, _ = run_doctor(home, bin_dir, "--usage", "--pool", "claude-sub",
                           env_extra={"AIMR_CLAUDE_USAGE_URL": url})
    by_name = {w["name"]: w for w in report["pools"]["claude-sub"]["usage"]["windows"]}
    assert "pace" not in by_name["5h"]
    assert "pace" not in by_name["7d"]
    assert by_name["5h"]["left_percent"] == 96.0  # left/reset still derived


def test_usage_mode_already_reset_reading_is_flagged(tmp_path):
    """A reading whose window reset in the past is stale-high: flag it,
    suppress headroom (unknowable), never show a countdown."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    write_claude_creds(home, FUTURE_MS)
    url = write_usage_fixture(tmp_path / "usage.json",
                              used_5h=97.0, resets_5h_s=-600,
                              used_7d=10.0, resets_7d_s=3 * 86400)
    report, _ = run_doctor(home, bin_dir, "--usage", "--pool", "claude-sub",
                           env_extra={"AIMR_CLAUDE_USAGE_URL": url})
    by_name = {w["name"]: w for w in report["pools"]["claude-sub"]["usage"]["windows"]}
    w5 = by_name["5h"]
    assert w5["already_reset"] is True
    assert "resets_in_seconds" not in w5 and "pace" not in w5
    assert w5["headroom"] is None
    # the stale-high 97% must not drive pool headroom to critical
    assert report["pools"]["claude-sub"]["usage"]["headroom"]["level"] == "ok"


def test_usage_mode_human_render(tmp_path):
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    write_claude_creds(home, FUTURE_MS)
    url = write_usage_fixture(tmp_path / "usage.json", used_5h=62.0,
                              resets_5h_s=2 * 3600, used_7d=91.0,
                              resets_7d_s=3 * 86400)
    proc = subprocess.run(
        [sys.executable, str(DOCTOR), "--usage"],
        capture_output=True, text=True, timeout=60, check=False,
        env={"HOME": str(home), "PATH": str(bin_dir),
             "AIMR_CACHE_DIR": str(home / ".aimr-cache"),
             "CODEX_HOME": str(home / ".codex"), "GROK_HOME": str(home / ".grok"),
             "AIMR_CLAUDE_USAGE_URL": url},
    )
    assert proc.returncode == 0
    assert SECRET_MARKER not in proc.stdout + proc.stderr
    out = proc.stdout
    assert "AIMR gauge" in out
    assert "█" in out and "░" in out
    assert "% left" in out and "resets in" in out
    assert "pace" in out and "CRITICAL" in out
    assert "headroom:" in out and "claude-sub CRITICAL" in out
    # absent pools render one honest line, not a crash
    assert "codex-sub" in out


def test_usage_mode_empty_machine_exits_zero(tmp_path):
    """The gauge doesn't assess routability: no readouts is still a valid
    report (exit 0), unlike the full doctor's exit-2 contract."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    report, rc = run_doctor(home, bin_dir, "--usage")
    assert rc == 0
    assert report["mode"] == "usage"
    proc = subprocess.run(
        [sys.executable, str(DOCTOR), "--usage"],
        capture_output=True, text=True, timeout=60, check=False,
        env={"HOME": str(home), "PATH": str(bin_dir),
             "AIMR_CACHE_DIR": str(home / ".aimr-cache"),
             "CODEX_HOME": str(home / ".codex"), "GROK_HOME": str(home / ".grok")},
    )
    assert proc.returncode == 0
    assert "no live quota readings" in proc.stdout


def test_usage_and_deep_are_mutually_exclusive():
    proc = subprocess.run([sys.executable, str(DOCTOR), "--usage", "--deep"],
                          capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 2  # argparse usage error
    assert "not allowed with" in proc.stderr


def test_codex_middle_era_resets_in_seconds_shape(tmp_path):
    """0.41–0.42-era rollouts carry resets_in_seconds RELATIVE to the event
    instead of epoch resets_at — recoverable via the snapshot timestamp."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "codex",
              'if [ "$1" = "login" ]; then echo "Logged in using ChatGPT" >&2; exit 0; fi\n'
              'echo "codex-cli 9.9.9"\nexit 0\n')
    sessions = home / ".codex" / "sessions" / "2026" / "01" / "05"
    sessions.mkdir(parents=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 120))
    (sessions / "rollout-2026-01-05T09-00-00-mid.jsonl").write_text(json.dumps({
        "timestamp": ts, "type": "event_msg",
        "payload": {"type": "token_count", "info": {},
                    "rate_limits": {
                        "primary": {"used_percent": 30.0, "window_minutes": 300,
                                    "resets_in_seconds": 7200}}}}) + "\n")
    report, _ = run_doctor(home, bin_dir)
    w5 = report["pools"]["codex-sub"]["usage"]["windows"][0]
    assert w5["resets_at"], "relative reset must be recovered from the event timestamp"
    # reset = event_ts + 7200s, event was 120s ago -> countdown ~7080s
    assert 6900 < w5["resets_in_seconds"] <= 7080
    assert w5["left_percent"] == 70.0


def test_enrichment_applies_to_codex_windows_in_local_mode(tmp_path):
    """Derived fields ride on every window everywhere — the local codex
    rollout scan included (window_minutes comes from the rollout itself)."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "codex",
              'if [ "$1" = "login" ]; then echo "Logged in using ChatGPT" >&2; exit 0; fi\n'
              'echo "codex-cli 9.9.9"\nexit 0\n')
    write_codex_state(home, used_5h=12.5, used_7d=40.0)
    report, _ = run_doctor(home, bin_dir)  # default local mode
    by_name = {w["name"]: w for w in report["pools"]["codex-sub"]["usage"]["windows"]}
    w5 = by_name["5h"]
    assert w5["left_percent"] == 87.5
    assert isinstance(w5["resets_in_seconds"], int)
    # elapsed = 300m - 60m = 80% of window; 12.5% used -> pace ~0.16
    assert 0.1 < w5["pace"] < 0.2
    assert w5["headroom"] == "ok"
    assert report["pools"]["codex-sub"]["usage"]["headroom"]["level"] == "ok"


# --------------------------------------------- review-round regressions

APPSERVER_STUB = (
    'if [ "$1" = "login" ]; then echo "Logged in using ChatGPT" >&2; exit 0; fi\n'
    'if [ "$1" = "app-server" ]; then\n'
    '  echo appserver >> "{canary}"\n'
    '  cat > /dev/null\n'
    '  echo \'{{"jsonrpc":"2.0","id":2,"result":{{"rateLimits":{{"primary":'
    '{{"usedPercent":55,"windowDurationMins":300,"resetsAt":{reset}}}}},'
    '"planType":"plus"}}}}\'\n'
    '  exit 0\n'
    'fi\n'
    'echo "codex-cli 9.9.9"\nexit 0\n'
)


def write_stale_rollout(home: Path, days_old: int = 8) -> None:
    """A snapshot whose every window reset long ago (codex unused for days)."""
    sessions = home / ".codex" / "sessions" / "2026" / "07" / "01"
    sessions.mkdir(parents=True, exist_ok=True)
    old = time.time() - days_old * 86400
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(old))
    (sessions / "rollout-2026-07-01T09-00-00-old.jsonl").write_text(json.dumps({
        "timestamp": ts, "type": "event_msg",
        "payload": {"type": "token_count", "info": {},
                    "rate_limits": {
                        "primary": {"used_percent": 80.0, "window_minutes": 300,
                                    "resets_at": int(old) + 3600},
                        "secondary": {"used_percent": 60.0, "window_minutes": 10080,
                                      "resets_at": int(old) + 86400}}}}) + "\n")


def test_usage_mode_stale_snapshot_falls_through_to_appserver(tmp_path):
    """A rollout snapshot whose windows have all reset must NOT suppress the
    free app-server readout in usage mode (review finding: agents got no
    codex runway data despite a zero-cost fresh readout being available)."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    canary = tmp_path / "canary-appserver"
    make_stub(bin_dir, "codex", APPSERVER_STUB.format(
        canary=canary, reset=int(time.time()) + 3600))
    write_stale_rollout(home)
    report, rc = run_doctor(home, bin_dir, "--usage", "--pool", "codex-sub")
    assert rc == 0
    usage = report["pools"]["codex-sub"]["usage"]
    assert usage["source"] == "app-server", \
        "stale snapshot must fall through to the live readout"
    w5 = usage["windows"][0]
    assert w5["used_percent"] == 55
    assert w5["headroom"] == "ok"
    # …and the readout is politeness-cached: a second run must not respawn
    report2, _ = run_doctor(home, bin_dir, "--usage", "--pool", "codex-sub")
    assert report2["pools"]["codex-sub"]["usage"]["source"] == "app-server"
    assert canary.read_text().count("appserver") == 1, \
        "second run within the TTL must be served from the cache"


def test_local_mode_keeps_stale_snapshot_without_spawning(tmp_path):
    """Default local mode stays zero-spawn beyond the auth read: a stale
    snapshot is still served (flagged already_reset), never app-server."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    canary = tmp_path / "canary-appserver"
    make_stub(bin_dir, "codex", APPSERVER_STUB.format(
        canary=canary, reset=int(time.time()) + 3600))
    write_stale_rollout(home)
    report, _ = run_doctor(home, bin_dir, "--pool", "codex-sub")
    usage = report["pools"]["codex-sub"]["usage"]
    assert usage["source"] == "rollout-scan"
    assert all(w["already_reset"] for w in usage["windows"])
    assert usage["headroom"] is None
    assert not canary.exists(), "local mode must not spawn app-server"


def test_pace_uses_reading_timebase_not_wall_clock(tmp_path):
    """used_percent is a fact as of the snapshot; pace must measure elapsed
    there too. Snapshot 3h ago, 30% into a 5h window, 40% used -> pace
    ~1.33 (burning hot), NOT ~0.44 (the wall-clock-now understatement).
    The exhaustion projection expired hours ago -> omitted, not negative."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    make_stub(bin_dir, "codex",
              'if [ "$1" = "login" ]; then echo "Logged in using ChatGPT" >&2; exit 0; fi\n'
              'echo "codex-cli 9.9.9"\nexit 0\n')
    sessions = home / ".codex" / "sessions" / "2026" / "07" / "14"
    sessions.mkdir(parents=True)
    snap_time = time.time() - 3 * 3600  # 3h old reading
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(snap_time))
    (sessions / "rollout-2026-07-14T22-00-00-hot.jsonl").write_text(json.dumps({
        "timestamp": ts, "type": "event_msg",
        "payload": {"type": "token_count", "info": {},
                    "rate_limits": {
                        # at snapshot time: 90m elapsed of 300m, reset 210m out
                        "primary": {"used_percent": 40.0, "window_minutes": 300,
                                    "resets_at": int(snap_time) + 210 * 60}}}}) + "\n")
    report, _ = run_doctor(home, bin_dir, "--pool", "codex-sub")
    w5 = report["pools"]["codex-sub"]["usage"]["windows"][0]
    assert 1.25 < w5["pace"] < 1.45, f"pace {w5['pace']} must use the reading's timebase"
    # empty was projected 135m after a 180m-old reading -> already in the past
    assert "time_to_exhaustion_seconds" not in w5
    # the reset countdown IS a now-fact: 210m from snapshot = 30m from now
    assert 1700 < w5["resets_in_seconds"] <= 1800


def test_usage_mode_creds_without_token_get_actionable_note(tmp_path):
    """Under --usage, credentials lacking an accessToken must not produce
    the circular 'needs --usage or --deep' advice."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    d = home / ".claude"; d.mkdir(parents=True)
    (d / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"expiresAt": FUTURE_MS, "subscriptionType": "max"}}))
    report, _ = run_doctor(home, bin_dir, "--usage", "--pool", "claude-sub")
    note = report["pools"]["claude-sub"]["usage"]["note"]
    assert "--usage" not in note and "--deep" not in note
    assert "accessToken" in note
    # local mode keeps the flag pointer (there the advice is not circular)
    report2, _ = run_doctor(home, bin_dir, "--pool", "claude-sub")
    assert "--usage" in report2["pools"]["claude-sub"]["usage"]["note"]


def test_gauge_footer_and_bar_honesty(tmp_path):
    """97.6% used renders a not-quite-full bar; an all-already-reset machine
    says 'headroom: unknown', not the contradictory 'no live quota
    readings' beneath rendered readings."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    write_claude_creds(home, FUTURE_MS)
    url = write_usage_fixture(tmp_path / "usage.json", used_5h=97.6,
                              resets_5h_s=3600, used_7d=1.2,
                              resets_7d_s=5 * 86400)
    env = {"HOME": str(home), "PATH": str(bin_dir),
           "AIMR_CACHE_DIR": str(home / ".aimr-cache"),
           "CODEX_HOME": str(home / ".codex"), "GROK_HOME": str(home / ".grok"),
           "AIMR_CLAUDE_USAGE_URL": url}
    proc = subprocess.run([sys.executable, str(DOCTOR), "--usage"],
                          capture_output=True, text=True, timeout=60,
                          check=False, env=env)
    assert "█" * 20 not in proc.stdout, "97.6% must not render as a full bar"
    assert "░" * 20 not in proc.stdout, "1.2% must not render as an empty bar"
    # now the all-reset machine: readings exist, headroom is unknown
    bin2 = tmp_path / "bin2"; bin2.mkdir()
    make_stub(bin2, "codex",
              'if [ "$1" = "login" ]; then echo "Not logged in" >&2; exit 1; fi\n'
              'echo "codex-cli 9.9.9"\nexit 0\n')
    home2 = tmp_path / "home2"; home2.mkdir()
    write_stale_rollout(home2)
    proc2 = subprocess.run([sys.executable, str(DOCTOR), "--usage", "--pool", "codex-sub"],
                           capture_output=True, text=True, timeout=60, check=False,
                           env={"HOME": str(home2), "PATH": str(bin2),
                                "AIMR_CACHE_DIR": str(home2 / ".aimr-cache"),
                                "CODEX_HOME": str(home2 / ".codex"),
                                "GROK_HOME": str(home2 / ".grok")})
    assert "headroom: unknown" in proc2.stdout
    assert "no live quota readings" not in proc2.stdout


def test_naive_iso_resets_parse_as_utc(tmp_path):
    """Timezone-naive ISO resets_at must be read as UTC even on a machine
    with a far-from-UTC local timezone (else already_reset misfires)."""
    home = tmp_path / "home"; bin_dir = tmp_path / "bin"
    home.mkdir(); bin_dir.mkdir()
    write_claude_creds(home, FUTURE_MS)
    naive_future = time.strftime("%Y-%m-%dT%H:%M:%S",
                                 time.gmtime(time.time() + 2 * 3600))  # no Z
    fixture = tmp_path / "usage.json"
    fixture.write_text(json.dumps({
        "five_hour": {"utilization": 50.0, "resets_at": naive_future},
        "seven_day": {"utilization": 10.0, "resets_at": None},
    }))
    report, _ = run_doctor(home, bin_dir, "--usage", "--pool", "claude-sub",
                           env_extra={"AIMR_CLAUDE_USAGE_URL": fixture.as_uri(),
                                      "TZ": "Asia/Tokyo"})  # UTC+9, no DST
    w5 = report["pools"]["claude-sub"]["usage"]["windows"][0]
    assert "already_reset" not in w5, "naive UTC time misread as local (+9h skew)"
    assert 6900 < w5["resets_in_seconds"] <= 7200


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
