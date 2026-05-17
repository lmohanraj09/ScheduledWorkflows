#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TASK_FILE="$SCRIPT_DIR/categorize-and-label-last-24h-emails.codex.md"
CONFIG_FILE="$SCRIPT_DIR/email-categories.config.json"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/categorize-and-label-emails.log"
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

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Config file not found: $CONFIG_FILE" >&2
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

if [[ -n "${CI:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is missing. Add OPENAI_API_KEY=<gcp-secret-name> to SECRET_MANAGER_ENV_MAP and make sure that secret has a non-empty enabled version." >&2
  exit 1
fi

if [[ -n "${CI:-}" && -n "${OPENAI_API_KEY:-}" ]]; then
  case "$OPENAI_API_KEY" in
    sk-*) echo "OPENAI_API_KEY is present and has an OpenAI-looking prefix; length: ${#OPENAI_API_KEY}" ;;
    *) echo "OPENAI_API_KEY is present but does not start with sk-; length: ${#OPENAI_API_KEY}" >&2 ;;
  esac

  openai_status="$(
    curl -sS -o /tmp/emailassistant-openai-auth-check.json \
      -w "%{http_code}" \
      -H "Authorization: Bearer $OPENAI_API_KEY" \
      https://api.openai.com/v1/models
  )"
  if [[ "$openai_status" != "200" ]]; then
    echo "OpenAI API key check failed with HTTP $openai_status. The key was fetched from Secret Manager, but OpenAI rejected it." >&2
    exit 1
  fi
  echo "OpenAI API key check passed."

  printenv OPENAI_API_KEY | "$CODEX_BIN" login --with-api-key >/dev/null
  echo "Codex API key login completed."
fi

mkdir -p "$LOG_DIR"

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') starting email categorization and labeling ====="
  "$CODEX_BIN" exec \
    --skip-git-repo-check \
    --cd "$SCRIPT_DIR" \
    --sandbox workspace-write \
    -c approval_policy=\"never\" \
    -c forced_login_method=\"api\" \
    - < "$TASK_FILE"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') finished email categorization and labeling ====="
} 2>&1 | tee -a "$LOG_FILE"
