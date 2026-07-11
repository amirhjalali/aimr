#!/usr/bin/env python3
"""Generation pass for the image-gen-v1 suite.

Executes every task in tasks.json against one provider's headless runner,
collects artifacts + timing/failure data, and writes a judging manifest.
Judging is a separate vision-LLM pass (see judge_prompt.md) — this script
deliberately does no scoring.

Usage:
  python runner.py --provider codex/gpt-image-2 --out runs/img-v1-20260711

Providers are looked up in registry.json; only invocation.type == "headless-cli"
entries with a bundled skill runner are executable here. Currently wired:
codex/gpt-image-2 (via skills/image-gen-codex/scripts/codex_image_gen.py).
Adding a provider = add a build_jobs/execute pair below.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE_DIR = Path(__file__).resolve().parent
TASKS = json.loads((SUITE_DIR / "tasks.json").read_text())


def run_codex_gpt_image_2(tasks: list[dict], out_dir: Path) -> list[dict]:
    """Drive the bundled batch runner from skills/image-gen-codex."""
    runner = ROOT / "skills" / "image-gen-codex" / "scripts" / "codex_image_gen.py"
    jobs = [
        {"id": t["id"], "prompt": t["prompt"],
         "out": str(out_dir / f"{t['id']}.png"), "size": t.get("size", "1024x1024")}
        for t in tasks
    ]
    jobs_file = out_dir / "jobs.json"
    jobs_file.write_text(json.dumps(jobs, indent=2))
    log_file = out_dir / "run_log.json"
    started = time.time()
    proc = subprocess.run(
        [sys.executable, str(runner), "--jobs", str(jobs_file),
         "--workers", "4", "--log", str(log_file)],
        cwd=out_dir, timeout=3600 * 2,
    )
    results = []
    for t in tasks:
        artifact = out_dir / f"{t['id']}.png"
        ok = artifact.exists() and artifact.stat().st_size > 0
        results.append({
            "task_id": t["id"], "artifact": str(artifact) if ok else None,
            "status": "ok" if ok else "failed",
        })
    print(f"generation pass exit={proc.returncode} wall={time.time() - started:.0f}s "
          f"(exit 2 = hard rate limit / credits)", file=sys.stderr)
    return results


EXECUTORS = {
    "codex/gpt-image-2": run_codex_gpt_image_2,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", required=True, choices=sorted(EXECUTORS))
    ap.add_argument("--out", required=True, help="run directory (created)")
    args = ap.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    results = EXECUTORS[args.provider](TASKS["tasks"], out_dir)

    manifest = {
        "suite": TASKS["suite"],
        "provider": args.provider,
        "run_dir": str(out_dir),
        "results": results,
        "next_step": "Vision-LLM judging: score each artifact per judge_prompt.md "
                     "against rubric.md, write scores.json here, then copy the "
                     "aggregate into registry.json with suite id and date.",
    }
    (out_dir / "judging_manifest.json").write_text(json.dumps(manifest, indent=2))
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"{ok}/{len(results)} artifacts generated → {out_dir / 'judging_manifest.json'}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
