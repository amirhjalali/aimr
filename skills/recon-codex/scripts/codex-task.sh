#!/usr/bin/env bash
# Codex-as-executor harness (portable): run a Codex CLI task in an isolated
# git worktree of WHATEVER repo you invoke it from. Bundled with the
# codex-delegation skill; a repo may carry its own tuned copy (gabooja:
# scripts/agents/codex-task.sh).
#
# Usage (run from anywhere inside the target repo):
#   codex-task.sh start <branch> <brief-file> [extra codex exec args...]
#   codex-task.sh diff  <branch>
#   codex-task.sh last  <branch>
#   codex-task.sh clean <branch>
#
# Env: CODEX_WT_ROOT overrides the worktree parent dir;
#      CODEX_SKIP_INSTALL=1 skips dependency install.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
WT_ROOT="${CODEX_WT_ROOT:-$(dirname "$REPO_ROOT")/$(basename "$REPO_ROOT")-codex-worktrees}"

cmd="${1:?usage: codex-task.sh start|diff|last|clean ...}"
shift

wt_dir() { echo "$WT_ROOT/$1"; }

install_deps() {
  # Best-effort so Codex can self-verify; secrets (.env*) are never copied.
  [ "${CODEX_SKIP_INSTALL:-0}" = "1" ] && return 0
  if [ -f pnpm-lock.yaml ]; then pnpm install --prefer-offline --silent
  elif [ -f package-lock.json ]; then npm install --no-audit --no-fund --silent
  elif [ -f yarn.lock ]; then yarn install --silent
  elif [ -f bun.lockb ] || [ -f bun.lock ]; then bun install --silent
  fi
}

case "$cmd" in
  start)
    branch="${1:?branch name required}"
    brief="${2:?brief file required}"
    shift 2
    dir="$(wt_dir "$branch")"
    git -C "$REPO_ROOT" fetch origin --quiet 2>/dev/null || true
    mkdir -p "$WT_ROOT"
    base="origin/HEAD"
    git -C "$REPO_ROOT" rev-parse --verify origin/main >/dev/null 2>&1 && base="origin/main"
    git -C "$REPO_ROOT" worktree add -b "$branch" "$dir" "$base"
    (cd "$dir" && install_deps)
    codex exec -C "$dir" -s workspace-write \
      --output-last-message "$dir/.codex-last.txt" \
      "$@" - <"$brief"
    echo
    echo "=== codex last message ==="
    cat "$dir/.codex-last.txt"
    echo
    echo "=== diff stat ==="
    git -C "$dir" add -A --intent-to-add
    git -C "$dir" reset -q -- .codex-last.txt 2>/dev/null || true
    git -C "$dir" diff --stat
    ;;
  diff)
    branch="${1:?branch name required}"
    dir="$(wt_dir "$branch")"
    git -C "$dir" add -A --intent-to-add
    git -C "$dir" reset -q -- .codex-last.txt 2>/dev/null || true
    git -C "$dir" diff
    ;;
  last)
    branch="${1:?branch name required}"
    cat "$(wt_dir "$branch")/.codex-last.txt"
    ;;
  clean)
    branch="${1:?branch name required}"
    git -C "$REPO_ROOT" worktree remove --force "$(wt_dir "$branch")"
    git -C "$REPO_ROOT" branch -D "$branch" 2>/dev/null || true
    ;;
  *)
    echo "unknown command: $cmd" >&2
    exit 1
    ;;
esac
