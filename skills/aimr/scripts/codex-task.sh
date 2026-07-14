#!/usr/bin/env bash
# Codex-as-executor harness (portable): run a Codex CLI task in an isolated
# git worktree of WHATEVER repo you invoke it from. Bundled with the AIMR
# skill (code-implementation lane); a repo may carry its own tuned copy.
#
# Usage (run from anywhere inside the target repo):
#   codex-task.sh start <branch> <brief-file> [extra codex exec args...]
#   codex-task.sh diff  <branch>
#   codex-task.sh last  <branch>
#   codex-task.sh clean <branch>
#
# Env: CODEX_WT_ROOT overrides the worktree parent dir;
#      CODEX_SKIP_INSTALL=1 skips dependency install;
#      CODEX_TIMEOUT_S bounds the codex exec run (default 1800, matching the
#      registry's timeout_s contract; 0 disables).
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
    # Bounded run: SIGTERM at the deadline, SIGKILL 30s later — to the whole
    # process GROUP where setsid exists (killing only the lead pid leaves the
    # hung tree alive; same lesson codex_image_gen.py encodes with killpg).
    # A killed run may still have finished its file work — check the worktree
    # before rerunning (see the timeout row in SKILL.md's failure table).
    timeout_s="${CODEX_TIMEOUT_S:-1800}"
    if [ "$timeout_s" = "0" ]; then
      codex exec -C "$dir" -s workspace-write \
        --output-last-message "$dir/.codex-last.txt" \
        "$@" - <"$brief"
    else
      if command -v setsid >/dev/null 2>&1; then
        setsid codex exec -C "$dir" -s workspace-write \
          --output-last-message "$dir/.codex-last.txt" \
          "$@" - <"$brief" &
      else
        codex exec -C "$dir" -s workspace-write \
          --output-last-message "$dir/.codex-last.txt" \
          "$@" - <"$brief" &
      fi
      codex_pid=$!
      # setsid detaches codex from OUR process group: forward INT/TERM so a
      # Ctrl-C / caller kill doesn't orphan the run (bash's wait is
      # interruptible by trapped signals, so this fires promptly).
      trap '{ kill -TERM -- "-$codex_pid" 2>/dev/null || kill -TERM "$codex_pid" 2>/dev/null; }' INT TERM
      (
        sleep "$timeout_s" &&
          { kill -TERM -- "-$codex_pid" 2>/dev/null || kill -TERM "$codex_pid" 2>/dev/null; } &&
          sleep 30 &&
          { kill -KILL -- "-$codex_pid" 2>/dev/null || kill -KILL "$codex_pid" 2>/dev/null; }
      ) &
      watchdog_pid=$!
      set +e; wait "$codex_pid"; codex_rc=$?; set -e
      trap - INT TERM
      kill "$watchdog_pid" 2>/dev/null || true
      wait "$watchdog_pid" 2>/dev/null || true
      if [ "$codex_rc" -ne 0 ]; then
        # Died by signal (rc >= 128): sweep the group unconditionally — the
        # watchdog's KILL leg never fires when the leader exits on TERM but
        # a child ignored it (mirrors codex_image_gen.py's killpg).
        if [ "$codex_rc" -ge 128 ]; then
          kill -KILL -- "-$codex_pid" 2>/dev/null || true
        fi
        echo "codex exec exited $codex_rc (timeout after ${timeout_s}s sends TERM)" >&2
      fi
    fi
    echo
    echo "=== codex last message ==="
    if [ -f "$dir/.codex-last.txt" ]; then
      cat "$dir/.codex-last.txt"
    else
      echo "(no last message — the codex run was killed or failed before writing one)"
    fi
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
