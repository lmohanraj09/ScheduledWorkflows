#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/email-categories.config.json"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/categorize-and-label-emails.log"
SECRET_HELPER="$SCRIPT_DIR/scripts/fetch-gcp-secrets.py"
GMAIL_AUTOMATION="$SCRIPT_DIR/scripts/categorize_and_label_gmail.py"

if [[ ! -f "$GMAIL_AUTOMATION" ]]; then
  echo "Gmail automation script not found: $GMAIL_AUTOMATION" >&2
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

for required_var in GMAIL_CLIENT_ID GMAIL_CLIENT_SECRET GMAIL_REFRESH_TOKEN; do
  if [[ -z "${!required_var:-}" ]]; then
    echo "$required_var is missing. Add $required_var=<gcp-secret-name> to SECRET_MANAGER_ENV_MAP." >&2
    exit 1
  fi
done

mkdir -p "$LOG_DIR"

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') starting email categorization and labeling ====="
  python3 "$GMAIL_AUTOMATION"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') finished email categorization and labeling ====="
} 2>&1 | tee -a "$LOG_FILE"
