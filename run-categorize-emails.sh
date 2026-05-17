#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TASK_FILE="$SCRIPT_DIR/categorize-last-24h-emails.codex.md"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/categorize-emails.log"
SECRET_HELPER="$SCRIPT_DIR/scripts/fetch-gcp-secrets.py"

CODEX_BIN="${CODEX_BIN:-$(command -v codex || true)}"

if [[ -z "$CODEX_BIN" || ! -x "$CODEX_BIN" ]]; then
  echo "codex CLI not found. Install Codex or set CODEX_BIN to the full codex path." >&2
  exit 1
fi

if [[ ! -f "$TASK_FILE" ]]; then
  echo "Task file not found: $TASK_FILE" >&2
  exit 1
fi

if [[ -n "${SECRET_MANAGER_ENV_MAP:-}" ]]; then
  if [[ ! -f "$SECRET_HELPER" ]]; then
    echo "Secret helper not found: $SECRET_HELPER" >&2
    exit 1
  fi

  if ! command -v gcloud >/dev/null 2>&1; then
    for gcloud_dir in \
      "$HOME/google-cloud-sdk/bin" \
      "/usr/local/share/google-cloud-sdk/bin" \
      "/opt/homebrew/share/google-cloud-sdk/bin"; do
      if [[ -x "$gcloud_dir/gcloud" ]]; then
        export PATH="$gcloud_dir:$PATH"
        break
      fi
    done
  fi

  if ! command -v gcloud >/dev/null 2>&1; then
    echo "gcloud CLI not found. Install Google Cloud SDK before fetching Secret Manager secrets." >&2
    exit 1
  fi

  if [[ -z "${GITHUB_ENV:-}" ]]; then
    echo "SECRET_MANAGER_ENV_MAP is set, but GITHUB_ENV is missing. Secret export is only supported in GitHub Actions." >&2
    exit 1
  fi

  SECRET_MANAGER_SHELL_ENV_FILE="${SECRET_MANAGER_SHELL_ENV_FILE:-${RUNNER_TEMP:-/tmp}/emailassistant-secrets.env}"
  export SECRET_MANAGER_SHELL_ENV_FILE

  python3 "$SECRET_HELPER"
  # shellcheck disable=SC1090
  source "$SECRET_MANAGER_SHELL_ENV_FILE"
fi

mkdir -p "$LOG_DIR"

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') starting email categorization ====="
  "$CODEX_BIN" exec \
    --skip-git-repo-check \
    --cd "$SCRIPT_DIR" \
    --sandbox workspace-write \
    -c approval_policy=\"never\" \
    - < "$TASK_FILE"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') finished email categorization ====="
} 2>&1 | tee -a "$LOG_FILE"
