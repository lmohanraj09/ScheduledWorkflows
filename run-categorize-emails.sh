#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TASK_FILE="$SCRIPT_DIR/categorize-last-24h-emails.codex.md"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/categorize-emails.log"

CODEX_BIN="${CODEX_BIN:-$(command -v codex || true)}"

if [[ -z "$CODEX_BIN" || ! -x "$CODEX_BIN" ]]; then
  echo "codex CLI not found. Install Codex or set CODEX_BIN to the full codex path." >&2
  exit 1
fi

if [[ ! -f "$TASK_FILE" ]]; then
  echo "Task file not found: $TASK_FILE" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') starting email categorization ====="
  "$CODEX_BIN" exec \
    --skip-git-repo-check \
    --cd "$SCRIPT_DIR" \
    --sandbox workspace-write \
    --ask-for-approval never \
    - < "$TASK_FILE"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') finished email categorization ====="
} 2>&1 | tee -a "$LOG_FILE"
