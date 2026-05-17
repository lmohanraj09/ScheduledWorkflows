#!/usr/bin/env python3
"""Fetch Google Cloud Secret Manager secrets into a GitHub Actions env file."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path


def parse_secret_map(raw_map: str) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []

    for line_number, raw_line in enumerate(raw_map.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            raise ValueError(
                f"SECRET_MANAGER_ENV_MAP line {line_number} must use ENV_NAME=SECRET_NAME[:VERSION]"
            )

        env_name, secret_ref = line.split("=", 1)
        env_name = env_name.strip()
        secret_ref = secret_ref.strip()

        if not env_name or not env_name.replace("_", "").isalnum() or env_name[0].isdigit():
            raise ValueError(
                f"SECRET_MANAGER_ENV_MAP line {line_number} has invalid env var name: {env_name!r}"
            )

        if not secret_ref:
            raise ValueError(
                f"SECRET_MANAGER_ENV_MAP line {line_number} is missing a Secret Manager secret name"
            )

        secret_name, version = secret_ref, "latest"
        if ":" in secret_ref:
            secret_name, version = secret_ref.rsplit(":", 1)
            secret_name = secret_name.strip()
            version = version.strip() or "latest"

        entries.append((env_name, secret_name, version))

    return entries


def fetch_secret(project_id: str, secret_name: str, version: str) -> str:
    result = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            version,
            "--secret",
            secret_name,
            "--project",
            project_id,
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.rstrip("\n")


def append_github_env(env_file: Path, key: str, value: str) -> None:
    delimiter = f"__{key}_SECRET__"
    with env_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


def append_shell_env(env_file: Path, key: str, value: str) -> None:
    with env_file.open("a", encoding="utf-8") as handle:
        handle.write(f"export {key}={shlex.quote(value)}\n")


def main() -> int:
    raw_map = os.environ.get("SECRET_MANAGER_ENV_MAP", "")
    if not raw_map.strip():
        print("SECRET_MANAGER_ENV_MAP is empty; no Google Cloud secrets to fetch.")
        return 0

    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        print("GCP_PROJECT_ID must be set.", file=sys.stderr)
        return 1

    github_env = os.environ.get("GITHUB_ENV")
    if not github_env:
        print("GITHUB_ENV must be set when exporting secrets.", file=sys.stderr)
        return 1

    shell_env = os.environ.get("SECRET_MANAGER_SHELL_ENV_FILE")

    try:
        entries = parse_secret_map(raw_map)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    env_file = Path(github_env)
    shell_env_file = Path(shell_env) if shell_env else None
    if shell_env_file:
        shell_env_file.write_text("", encoding="utf-8")
        shell_env_file.chmod(0o600)

    for env_name, secret_name, version in entries:
        try:
            value = fetch_secret(project_id, secret_name, version)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip()
            print(
                f"Failed to fetch Secret Manager secret {secret_name!r} version {version!r}: {stderr}",
                file=sys.stderr,
            )
            return exc.returncode or 1

        print(f"::add-mask::{value}")
        append_github_env(env_file, env_name, value)
        if shell_env_file:
            append_shell_env(shell_env_file, env_name, value)
        print(f"Fetched Secret Manager secret {secret_name}:{version} into {env_name}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
