#!/usr/bin/env python3
"""Categorize recent Gmail messages, apply labels, and create Finance drafts."""

from __future__ import annotations

import base64
import email.message
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo


GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"
LOCAL_TZ = os.environ.get("EMAILASSISTANT_TZ", "America/Los_Angeles")
FIRESTORE_API = "https://firestore.googleapis.com/v1"
METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)


@dataclass
class MessageInfo:
    message_id: str
    sender: str
    subject: str
    email_ts: datetime
    snippet: str
    body: str
    labels: list[str]
    categories: list[str]
    matched_keywords: dict[str, list[str]]


def die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        die(f"{name} must be set.")
    return value


def optional_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def http_json(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    data: dict | None = None,
) -> dict:
    body = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        die(f"HTTP {exc.code} from {url}: {detail}")
    except urllib.error.URLError as exc:
        die(f"Request failed for {url}: {exc}")

    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def metadata_access_token() -> str | None:
    request = urllib.request.Request(
        METADATA_TOKEN_URL,
        headers={"Metadata-Flavor": "Google"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            token_response = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    return token_response.get("access_token")


def gcloud_access_token() -> str | None:
    import subprocess

    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    token = result.stdout.strip()
    return token or None


def google_access_token() -> str | None:
    return metadata_access_token() or gcloud_access_token()


def form_json(url: str, values: dict[str, str]) -> dict:
    body = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        die(f"Token request failed with HTTP {exc.code}: {detail}")


def gmail_url(path: str, params: dict[str, str | int] | None = None) -> str:
    url = f"{GMAIL_API}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return url


def get_access_token() -> str:
    response = form_json(
        TOKEN_URL,
        {
            "client_id": require_env("GMAIL_CLIENT_ID"),
            "client_secret": require_env("GMAIL_CLIENT_SECRET"),
            "refresh_token": require_env("GMAIL_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
    )
    access_token = response.get("access_token")
    if not access_token:
        die("OAuth token response did not include access_token.")
    return access_token


def validate_config(config: dict, source: str) -> dict:
    lookback = config.get("lookback_hours", 24)
    if not isinstance(lookback, (int, float)) or lookback <= 0:
        die(f"{source} field lookback_hours must be a positive number.")

    categories = config.get("categories")
    if not isinstance(categories, list) or not categories:
        die(f"{source} field categories must be a non-empty list.")

    for category in categories:
        if not isinstance(category.get("name"), str) or not category["name"].strip():
            die("Each category must have a non-empty name.")
        if not isinstance(category.get("keywords"), list):
            die(f"Category {category.get('name')!r} must have a keywords list.")

    return config


def firestore_value_to_python(value: dict) -> object:
    if "nullValue" in value:
        return None
    if "booleanValue" in value:
        return value["booleanValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "stringValue" in value:
        return value["stringValue"]
    if "timestampValue" in value:
        return value["timestampValue"]
    if "arrayValue" in value:
        return [
            firestore_value_to_python(item)
            for item in value.get("arrayValue", {}).get("values", [])
        ]
    if "mapValue" in value:
        return {
            key: firestore_value_to_python(item)
            for key, item in value.get("mapValue", {}).get("fields", {}).items()
        }
    return None


def firestore_document_to_dict(document: dict) -> dict:
    return {
        key: firestore_value_to_python(value)
        for key, value in document.get("fields", {}).items()
    }


def firestore_config_document_url(project_id: str, client_slug: str) -> str:
    path = f"/projects/{project_id}/databases/(default)/documents/email_assistant_configs/{client_slug}"
    return f"{FIRESTORE_API}{path}"


def curl_json(url: str, token: str) -> dict | None:
    import subprocess

    try:
        result = subprocess.run(
            [
                "curl",
                "-fsS",
                "-H",
                f"Authorization: Bearer {token}",
                "-H",
                "Accept: application/json",
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return json.loads(result.stdout)


def fetch_firestore_document(url: str, token: str) -> dict | None:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Firestore config request failed with HTTP {exc.code}: {detail}", file=sys.stderr)
    except urllib.error.URLError as exc:
        print(f"Firestore config request failed: {exc}", file=sys.stderr)

    return curl_json(url, token)


def load_config_from_firestore() -> dict:
    project_id = require_env("GCP_PROJECT_ID")
    client_slug = optional_env("CLIENT_SLUG") or "mohanmain"

    token = google_access_token()
    if not token:
        die("Could not get Google access token for Firestore config.")

    url = firestore_config_document_url(project_id, client_slug)
    document = fetch_firestore_document(url, token)
    if not document:
        die(f"Could not read Firestore config email_assistant_configs/{client_slug}.")

    config = firestore_document_to_dict(document)
    print(f"Loaded config from Firestore: email_assistant_configs/{client_slug}", file=sys.stderr)
    return validate_config(config, f"Firestore email_assistant_configs/{client_slug}")


def list_message_ids(token: str, query: str) -> list[str]:
    ids: list[str] = []
    page_token: str | None = None
    while True:
        params: dict[str, str | int] = {"q": query, "maxResults": 500}
        if page_token:
            params["pageToken"] = page_token
        response = http_json(gmail_url("/users/me/messages", params), token=token)
        ids.extend(item["id"] for item in response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return ids


def get_header(payload: dict, name: str) -> str:
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def decode_body_data(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    return raw.decode("utf-8", errors="replace")


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def extract_body(payload: dict) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict) -> None:
        mime_type = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data", "")
        if body_data and mime_type == "text/plain":
            plain_parts.append(decode_body_data(body_data))
        elif body_data and mime_type == "text/html":
            html_parts.append(strip_html(decode_body_data(body_data)))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    text = "\n".join(part.strip() for part in plain_parts if part.strip())
    if not text:
        text = "\n".join(part.strip() for part in html_parts if part.strip())
    return re.sub(r"\s+", " ", text).strip()


def read_message(token: str, message_id: str, tz: ZoneInfo) -> MessageInfo:
    message = http_json(
        gmail_url(f"/users/me/messages/{message_id}", {"format": "full"}),
        token=token,
    )
    payload = message.get("payload", {})
    labels = message.get("labelIds", [])
    email_ts = datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz)
    return MessageInfo(
        message_id=message_id,
        sender=get_header(payload, "From"),
        subject=get_header(payload, "Subject") or "(no subject)",
        email_ts=email_ts,
        snippet=message.get("snippet", ""),
        body=extract_body(payload),
        labels=labels,
        categories=[],
        matched_keywords={},
    )


def searchable_text(message: MessageInfo, fields: list[str]) -> str:
    values = {
        "from": message.sender,
        "subject": message.subject,
        "snippet": message.snippet,
        "body": message.body,
    }
    return "\n".join(values.get(field, "") for field in fields).lower()


def categorize(message: MessageInfo, config: dict) -> None:
    fields = config.get("match_fields", ["from", "subject", "snippet", "body"])
    if not isinstance(fields, list):
        fields = ["from", "subject", "snippet", "body"]
    text = searchable_text(message, fields)

    for category in config["categories"]:
        matches = [
            keyword
            for keyword in category.get("keywords", [])
            if isinstance(keyword, str) and keyword.lower() in text
        ]
        if matches:
            name = category["name"]
            message.categories.append(name)
            message.matched_keywords[name] = matches

    if not message.categories:
        fallback = config.get("fallback_category", "Uncategorized")
        message.categories = [fallback]
        message.matched_keywords[fallback] = []


def list_labels(token: str) -> dict[str, str]:
    response = http_json(gmail_url("/users/me/labels"), token=token)
    return {label["name"]: label["id"] for label in response.get("labels", [])}


def ensure_labels(token: str, names: set[str]) -> dict[str, str]:
    labels = list_labels(token)
    for name in sorted(names):
        if name in labels:
            continue
        response = http_json(
            gmail_url("/users/me/labels"),
            method="POST",
            token=token,
            data={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        labels[name] = response["id"]
    return labels


def apply_labels(token: str, message: MessageInfo, label_ids_by_name: dict[str, str]) -> None:
    add_ids = [label_ids_by_name[name] for name in message.categories if name in label_ids_by_name]
    if not add_ids:
        return
    http_json(
        gmail_url(f"/users/me/messages/{message.message_id}/modify"),
        method="POST",
        token=token,
        data={"addLabelIds": add_ids},
    )


def next_step_for(categories: list[str]) -> str:
    if "Likely Action Items" in categories:
        return "review"
    if any(category in categories for category in ("Finance", "Health")):
        return "review official portal"
    if "Promotions" in categories and len(categories) == 1:
        return "archive or ignore"
    if "Newsletters and News" in categories:
        return "read or ignore"
    return "review"


def one_sentence_summary(message: MessageInfo) -> str:
    source = message.snippet or message.body
    source = re.sub(r"\s+", " ", source).strip()
    if not source:
        return "No preview text was available."
    if len(source) > 220:
        return source[:217].rstrip() + "..."
    return source


def build_count_summary(messages: list[MessageInfo], config: dict) -> str:
    grouped: dict[str, list[MessageInfo]] = defaultdict(list)
    for message in messages:
        for category in message.categories:
            grouped[category].append(message)

    category_names = [category["name"] for category in config["categories"]]
    fallback = config.get("fallback_category", "Uncategorized")
    if fallback not in category_names:
        category_names.append(fallback)

    lines = []
    for category in category_names:
        lines.append(f"- {category}: {len(grouped.get(category, []))}")
    return "\n".join(lines).rstrip() + "\n"


def summary_subject_for(run_time: datetime) -> str:
    return f"Email Category Summary - {run_time.strftime('%Y-%m-%d %H:%M %Z')}"


def finance_messages(messages: list[MessageInfo]) -> list[MessageInfo]:
    return [message for message in messages if "Finance" in message.categories]


def draft_subject_for(message: MessageInfo) -> str:
    subject = re.sub(r"\s+", " ", message.subject).strip() or "(no subject)"
    return f"Finance email review - {subject}"


def build_finance_draft_body(message: MessageInfo) -> str:
    keywords = message.matched_keywords.get("Finance", [])
    why = ", ".join(keywords) if keywords else "Finance category match"
    labels = ", ".join(message.categories)
    return "\n".join(
        [
            "Finance email review",
            "",
            f"Sender: {message.sender}",
            f"Subject: {message.subject}",
            f"Time: {message.email_ts.strftime('%Y-%m-%d %H:%M %Z')}",
            f"Labels applied: {labels}",
            f"Finance keywords: {why}",
            f"Summary: {one_sentence_summary(message)}",
            "Suggested next step: review official portal directly",
            "",
        ]
    )


def create_draft(token: str, to_email: str, subject: str, body: str) -> str:
    msg = email.message.EmailMessage()
    msg["To"] = to_email
    msg["From"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
    response = http_json(
        gmail_url("/users/me/drafts"),
        method="POST",
        token=token,
        data={"message": {"raw": raw}},
    )
    return response["id"]


def main() -> int:
    config = load_config_from_firestore()
    tz = ZoneInfo(LOCAL_TZ)
    run_time = datetime.now(tz)
    cutoff = run_time - timedelta(hours=float(config["lookback_hours"]))
    query = config.get("gmail_query") or "newer_than:1d -in:spam -in:trash -in:sent"

    token = get_access_token()
    profile = http_json(gmail_url("/users/me/profile"), token=token)
    account_email = profile.get("emailAddress") or "me"

    ids = list_message_ids(token, query)
    messages: list[MessageInfo] = []
    skipped_labels = {"DRAFT", "SENT", "SPAM", "TRASH"}
    for message_id in ids:
        message = read_message(token, message_id, tz)
        if skipped_labels.intersection(message.labels):
            continue
        if message.email_ts < cutoff:
            continue
        categorize(message, config)
        messages.append(message)

    label_names = {category for message in messages for category in message.categories}
    label_ids = ensure_labels(token, label_names)
    for message in messages:
        apply_labels(token, message, label_ids)

    count_summary = build_count_summary(messages, config)
    print(count_summary)

    summary_subject = summary_subject_for(run_time)
    summary_draft_id = create_draft(token, account_email, summary_subject, count_summary)
    print("Created one Gmail draft with the category count summary.")
    print(f"Summary Draft ID: {summary_draft_id}")
    print(f"Summary Subject: {summary_subject}")

    finance_items = finance_messages(messages)
    if finance_items:
        print(f"Created {len(finance_items)} individual Gmail draft(s) for Finance emails.")
        for message in finance_items:
            subject = draft_subject_for(message)
            body = build_finance_draft_body(message)
            draft_id = create_draft(token, account_email, subject, body)
            print(f"Draft ID: {draft_id}")
            print(f"Subject: {subject}")
    else:
        print("Skipped Gmail draft creation because no Finance emails matched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
