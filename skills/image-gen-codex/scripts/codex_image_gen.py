#!/usr/bin/env python3
"""Portable GPT Image 2 generator driven through the Codex CLI.

Repo-agnostic. No project structure, no run_plan.json, no CDN assumptions.
Generates images by asking a `codex exec` agent to call its built-in image tool
with the phrase "GPT Image 2.0", then harvesting the resulting PNG.

This single file encodes the operational lessons distilled from ~15 experiments'
worth of Codex-CLI image runs (see references/lessons.md). Do not re-derive them;
patch this script instead.

Prerequisites
-------------
- `codex` CLI installed and LOGGED IN (ChatGPT plan). No OPENAI_API_KEY needed;
  the Codex session provides auth. Verify with `codex login status`.
- Python 3.10+. Standard library only. Pillow is optional (used only to backfill
  width/height when the agent omits them).

Single image
------------
    python codex_image_gen.py \
        --prompt "Vintage badge: a fox in a ranger hat, muted 3-color palette. \
Format: square 1:1, centered, isolated graphic." \
        --out ./fox.png

With reference image(s) for style/character anchoring
----------------------------------------------------
    python codex_image_gen.py --prompt "..." --out ./out.png \
        --ref ./character_sheet.png --ref ./palette.png --size 1536x1024

Batch (parallel) from a JSON job file
-------------------------------------
    # jobs.json = [{"id": "a", "prompt": "...", "out": "a.png",
    #               "refs": ["ref.png"], "size": "1024x1024"}, ...]
    python codex_image_gen.py --jobs jobs.json --workers 8

Exit codes: 0 = ok (or partial in batch), 2 = aborted after consecutive hard
rate-limit hits (credits/quota). Use this in orchestrators to stop a wave early.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal as _signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# --- The prompt contract -----------------------------------------------------
# Proven verbatim across the source experiments. Every constraint earns its keep:
#   * "exactly one image ... GPT Image 2.0" selects the model (no CLI flag exists
#     for the image model) and prevents multi-candidate generation.
#   * "with this exact prompt (do not modify...)" + triple quotes stop the coding
#     agent from "helpfully" rewriting a tuned prompt.
#   * "Save ... to ./out.png" pins a deterministic path inside the isolated workdir.
#   * "EXACTLY one line of JSON ... No preamble" makes the result machine-parseable.
PROMPT_TEMPLATE = """Generate exactly one image using GPT Image 2.0 with this exact prompt (do not modify, paraphrase, or expand it):

\"\"\"{prompt}\"\"\"
{size_clause}
Save the resulting PNG to ./out.png in the current working directory.

After saving, your final message must be EXACTLY one line of JSON and nothing else:
{{"path": "<absolute path to out.png>", "model": "<model id you used>", "width": <int>, "height": <int>, "gen_seconds": <float>}}

Do not output any other text before or after the JSON. No preamble, no postamble."""

# Reference-image jobs get an extra instruction: the attachments are MODEL image
# input (passed through to the image tool), not files to open and describe. Text
# re-description loses visual identity — style survives, the exact face does not.
REF_CLAUSE = """

The attached reference image(s) ARE image input for the generation model, NOT files to open and inspect. Pass them through to the image generation tool so the model SEES them during generation. Do not describe the references in prose and regenerate from text alone."""

# --- Failure classification --------------------------------------------------
# HARD  = confirmed account/usage caps -> halt the wave after N consecutive hits.
# SOFT  = transient stream cuts + content-filter trips -> record and skip, do not
#         halt. Note: named-artist / real-photo moderation surfaces as
#         "stream disconnected", symptomatically identical to a rate limit but is
#         actually a policy trip — rewrite the prompt, do not treat as a cap.
HARD_RATE_LIMIT_TOKENS = (
    "rate limit", "rate-limit", "rate_limit", "429",
    "too many requests", "quota", "usage limit", "limit reached",
    "exceeded", "please try again later", "you've reached",
    "you have reached", "monthly limit", "daily limit", "credits exhausted",
)
SOFT_FAILURE_TOKENS = (
    "stream disconnected", "an error occurred while processing your request",
    "i'm not able to", "i cannot generate", "content policy", "violates",
)


def classify_failure(entry: dict) -> str:
    blob = " ".join(
        str(entry.get(k, "")) for k in
        ("error", "stderr_tail", "last_message_tail", "codex_stdout_tail")
    ).lower()
    if any(t in blob for t in HARD_RATE_LIMIT_TOKENS):
        return "hard"
    if any(t in blob for t in SOFT_FAILURE_TOKENS):
        return "soft"
    return "unknown"


def parse_last_json(text: str) -> dict | None:
    """Return the LAST {...} line that parses as JSON (robust to leading noise)."""
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _dims(png: Path, parsed: dict) -> tuple[int | None, int | None]:
    w, h = parsed.get("width"), parsed.get("height")
    if isinstance(w, int) and isinstance(h, int):
        return w, h
    try:  # Pillow is optional — only needed when the agent omits dims.
        from PIL import Image
        with Image.open(png) as im:
            return im.width, im.height
    except Exception:
        return None, None


def build_cmd(prompt_text: str, workdir: Path, last_msg: Path,
              refs: list[Path]) -> list[str]:
    """Assemble the codex exec argv.

    Flags, each load-bearing:
      exec                       non-interactive/headless (never bare `codex`)
      --skip-git-repo-check      the workdir is a throwaway temp dir, not a repo
      --sandbox workspace-write  autonomous tool use + write access to --cd.
                                 This is the current replacement for the now-
                                 deprecated `--full-auto` (codex prints
                                 "`--full-auto` is deprecated; use `--sandbox
                                 workspace-write` instead"). If a hardened/no-TTY
                                 host still blocks on approvals, fall back to
                                 `--dangerously-bypass-approvals-and-sandbox`.
      --cd <workdir>             per-job working root (isolation primitive)
      -o <last_msg>              write the agent's final message here (trusted
                                 result channel; stdout is only a fallback)
      -i <ref> ...               attach reference images (variadic)
      --                         terminate -i parsing so the prompt isn't eaten
    The prompt is the final POSITIONAL argument (not stdin — passing it on stdin
    has caused "No prompt provided via stdin" -> no_output failures).
    """
    cmd = [
        "codex", "exec",
        "--skip-git-repo-check",
        "--sandbox", "workspace-write",
        "--cd", str(workdir),
        "-o", str(last_msg),
    ]
    for r in refs:
        cmd += ["-i", str(r)]
    if refs:
        cmd.append("--")
    cmd.append(prompt_text)
    return cmd


def generate_one(job: dict, *, workdir_root: Path, timeout: int) -> dict:
    """Generate a single image. Thread-safe: touches only its own workdir + out.

    job keys: id, prompt, out (dest path), refs (list of paths), size ("WxH").
    Returns a log dict with status in {success, timeout, no_output, exception}.
    """
    job_id = str(job["id"])
    out_dest = Path(job["out"]).expanduser().resolve()
    refs = [Path(r).expanduser().resolve() for r in job.get("refs", [])]
    size = job.get("size")

    workdir = workdir_root / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    last_msg = workdir / "last.txt"
    out_png = workdir / "out.png"
    for stale in (last_msg, out_png):  # pre-clean; workdir is reused on retry
        if stale.exists():
            stale.unlink()

    # Copy refs into the workdir so paths are stable/local for the agent.
    local_refs: list[Path] = []
    for r in refs:
        if not r.exists():
            return {"id": job_id, "status": "exception",
                    "error": f"missing ref: {r}"}
        dest = workdir / r.name
        shutil.copyfile(r, dest)
        local_refs.append(dest)

    size_clause = f"\nOutput size: {size} pixels." if size else ""
    prompt_text = PROMPT_TEMPLATE.format(prompt=job["prompt"], size_clause=size_clause)
    if local_refs:
        prompt_text += REF_CLAUSE
    cmd = build_cmd(prompt_text, workdir, last_msg, local_refs)

    started = time.time()
    # start_new_session=True puts codex in its own process group so a timeout can
    # SIGKILL the WHOLE tree — the only reliable way to reap a hung `codex exec`.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), _signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.communicate(timeout=10)
        except Exception:
            pass
        return {"id": job_id, "status": "timeout",
                "error": f"codex exec timed out after {timeout}s",
                "elapsed": round(time.time() - started, 1)}
    except Exception as e:  # noqa: BLE001
        return {"id": job_id, "status": "exception", "error": repr(e)}

    elapsed = round(time.time() - started, 1)
    last_text = last_msg.read_text() if last_msg.exists() else ""
    parsed = parse_last_json(last_text) or parse_last_json(stdout) or {}

    # Resolve the produced PNG: agent-reported path (if it exists) else workdir
    # out.png. NEVER scan ~/.codex/generated_images — it races between workers.
    src = None
    rp = parsed.get("path")
    if rp and Path(rp).exists():
        src = Path(rp)
    elif out_png.exists():
        src = out_png
    if src is None:
        return {"id": job_id, "status": "no_output", "elapsed": elapsed,
                "error": "no PNG produced",
                "stderr_tail": (stderr or "")[-1500:],
                "last_message_tail": last_text[-1500:],
                "codex_stdout_tail": (stdout or "")[-1500:]}

    out_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, out_dest)
    w, h = _dims(out_dest, parsed)
    return {"id": job_id, "status": "success", "out": str(out_dest),
            "width": w, "height": h, "elapsed": elapsed,
            "model": parsed.get("model"),
            "kb": round(out_dest.stat().st_size / 1024, 1)}


def _atomic_write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(path)


def run_batch(jobs: list[dict], *, workers: int, timeout: int,
              workdir_root: Path, abort_after_hard: int, force: bool,
              log_path: Path | None) -> int:
    """Run jobs in parallel. Returns process exit code (2 if hard-aborted)."""
    todo = [j for j in jobs
            if force or not Path(j["out"]).expanduser().exists()]
    skipped = len(jobs) - len(todo)
    if skipped:
        print(f"[skip] {skipped} job(s) already have output (use --force to redo)")

    results: list[dict] = []
    consecutive_hard = 0
    aborted = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(generate_one, j, workdir_root=workdir_root,
                            timeout=timeout): j for j in todo}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            if r["status"] == "success":
                consecutive_hard = 0
                print(f"  +  {r['id']:<24} {r.get('width')}x{r.get('height')} "
                      f"{r.get('elapsed')}s")
            else:
                kind = classify_failure(r) if r["status"] != "timeout" else "soft"
                tag = {"hard": "!!", "soft": "..", "unknown": "??"}[kind]
                print(f"  {tag} {r['id']:<24} {r['status']}: "
                      f"{r.get('error', '')[:80]}")
                consecutive_hard = consecutive_hard + 1 if kind == "hard" else 0
            if log_path:
                _atomic_write_json(log_path, results)
            if consecutive_hard >= abort_after_hard:
                print(f"\nABORT: {consecutive_hard} consecutive hard rate-limit "
                      f"hits — stopping dispatch. (credits/quota likely exhausted)")
                for f in futs:  # cancel not-yet-started jobs
                    f.cancel()
                aborted = True
                break

    ok = sum(1 for r in results if r["status"] == "success")
    print(f"\nDone: {ok}/{len(todo)} succeeded"
          + (f", {skipped} skipped" if skipped else ""))
    return 2 if aborted else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate images with GPT Image 2 via Codex CLI")
    ap.add_argument("--prompt", help="Prompt text (single-image mode)")
    ap.add_argument("--out", help="Destination PNG path (single-image mode)")
    ap.add_argument("--ref", action="append", default=[],
                    help="Reference image for anchoring (repeatable)")
    ap.add_argument("--size", help='Requested output size, e.g. "1536x1024". '
                    "The CLI has no size flag; this is injected into the prompt.")
    ap.add_argument("--jobs", help="Path to JSON array of jobs (batch mode)")
    ap.add_argument("--workers", type=int, default=8,
                    help="Parallel workers in batch mode (8 is the proven safe "
                    "ceiling on a $100/mo ChatGPT plan)")
    ap.add_argument("--timeout", type=int, default=None,
                    help="Per-image timeout seconds. Default: 600 text-only, "
                    "1500 when refs are attached (multimodal integration is slow)")
    ap.add_argument("--workdir", default=None,
                    help="Root for per-job temp workdirs (default: ./codex_img_workdirs)")
    ap.add_argument("--abort-after-hard", type=int, default=3,
                    help="Abort batch after this many consecutive hard rate-limit hits")
    ap.add_argument("--force", action="store_true",
                    help="Regenerate even if the output file already exists")
    ap.add_argument("--log", default=None, help="Write a JSON run-log to this path (batch)")
    args = ap.parse_args()

    workdir_root = Path(args.workdir or "./codex_img_workdirs").expanduser().resolve()
    workdir_root.mkdir(parents=True, exist_ok=True)

    if args.jobs:
        jobs = json.loads(Path(args.jobs).read_text())
        default_to = args.timeout or (1500 if any(j.get("refs") for j in jobs) else 600)
        return run_batch(jobs, workers=args.workers, timeout=default_to,
                         workdir_root=workdir_root,
                         abort_after_hard=args.abort_after_hard,
                         force=args.force,
                         log_path=Path(args.log).expanduser() if args.log else None)

    if not (args.prompt and args.out):
        ap.error("single-image mode needs --prompt and --out (or use --jobs)")
    if not args.force and Path(args.out).expanduser().exists():
        print(f"[skip] {args.out} exists (use --force to redo)")
        return 0
    timeout = args.timeout or (1500 if args.ref else 600)
    r = generate_one({"id": Path(args.out).stem, "prompt": args.prompt,
                      "out": args.out, "refs": args.ref, "size": args.size},
                     workdir_root=workdir_root, timeout=timeout)
    print(json.dumps(r, indent=2))
    return 0 if r["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
