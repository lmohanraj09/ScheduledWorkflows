from __future__ import annotations

import os
import re
import secrets
import urllib.parse
from datetime import datetime, timezone

import requests
from flask import Flask, abort, redirect, request, session, url_for
from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import firestore
from google.cloud import secretmanager
from werkzeug.middleware.proxy_fix import ProxyFix


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def project_id() -> str:
    return require_env("GCP_PROJECT_ID")


def client_slug() -> str:
    raw = os.environ.get("CLIENT_SLUG", "mohanmain").strip().lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")
    if not slug:
        raise RuntimeError("CLIENT_SLUG must contain letters or numbers")
    return slug


def token_secret_name() -> str:
    return os.environ.get("REFRESH_TOKEN_SECRET_NAME", f"gmail-refresh-token-{client_slug()}").strip()


def redirect_uri() -> str:
    configured = os.environ.get("OAUTH_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return url_for("oauth_callback", _external=True)


def secret_client() -> secretmanager.SecretManagerServiceClient:
    return secretmanager.SecretManagerServiceClient()


def firestore_client() -> firestore.Client:
    return firestore.Client(project=project_id())


def ensure_secret(secret_id: str) -> str:
    client = secret_client()
    parent = f"projects/{project_id()}"
    name = f"{parent}/secrets/{secret_id}"
    try:
        client.get_secret(request={"name": name})
    except NotFound:
        try:
            client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
        except AlreadyExists:
            pass
    return name


def save_refresh_token(refresh_token: str) -> str:
    secret_name = ensure_secret(token_secret_name())
    client = secret_client()
    version = client.add_secret_version(
        request={
            "parent": secret_name,
            "payload": {"data": refresh_token.encode("utf-8")},
        }
    )
    return version.name


def exchange_code(code: str) -> dict:
    response = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": require_env("GMAIL_CLIENT_ID"),
            "client_secret": require_env("GMAIL_CLIENT_SECRET"),
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if not response.ok:
        abort(response.status_code, response.text)
    return response.json()


def gmail_profile(access_token: str) -> dict:
    response = requests.get(
        GMAIL_PROFILE_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if not response.ok:
        abort(response.status_code, response.text)
    return response.json()


def save_account_metadata(email: str, secret_version: str) -> None:
    db = firestore_client()
    now = datetime.now(timezone.utc)
    db.collection("email_accounts").document(client_slug()).set(
        {
            "client_slug": client_slug(),
            "email": email,
            "refresh_token_secret": token_secret_name(),
            "latest_secret_version": secret_version,
            "enabled": True,
            "updated_at": now,
            "created_at": now,
        },
        merge=True,
    )


@app.get("/")
def index() -> str:
    start_url = url_for("oauth_start")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Email Assistant</title>
  <style>
    body {{
      color: #17202a;
      font-family: Arial, sans-serif;
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f5f7fa;
    }}
    main {{
      width: min(520px, calc(100vw - 40px));
      background: white;
      border: 1px solid #d8dee8;
      border-radius: 8px;
      padding: 28px;
      box-shadow: 0 10px 24px rgba(23, 32, 42, 0.08);
    }}
    h1 {{ font-size: 24px; margin: 0 0 12px; }}
    p {{ line-height: 1.5; margin: 0 0 20px; }}
    a {{
      display: inline-block;
      color: white;
      background: #1a73e8;
      border-radius: 6px;
      padding: 12px 16px;
      text-decoration: none;
      font-weight: 700;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Email Assistant</h1>
    <p>Connect Gmail so Email Assistant can categorize recent messages, apply labels, and create a summary draft.</p>
    <a href="{start_url}">Connect Gmail</a>
  </main>
</body>
</html>"""


@app.get("/auth/google/start")
def oauth_start():
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    params = {
        "client_id": require_env("GMAIL_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return redirect(f"{AUTH_URL}?{urllib.parse.urlencode(params)}")


@app.get("/auth/google/callback")
def oauth_callback() -> str:
    if request.args.get("error"):
        abort(400, request.args.get("error_description") or request.args["error"])
    if request.args.get("state") != session.pop("oauth_state", None):
        abort(400, "Invalid OAuth state")

    code = request.args.get("code")
    if not code:
        abort(400, "Missing OAuth code")

    tokens = exchange_code(code)
    refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")
    if not access_token:
        abort(400, "Google did not return an access token")
    if not refresh_token:
        abort(400, "Google did not return a refresh token. Reconnect after revoking prior app access, or keep prompt=consent and access_type=offline.")

    profile = gmail_profile(access_token)
    email = profile.get("emailAddress")
    if not email:
        abort(400, "Could not read Gmail profile email")

    secret_version = save_refresh_token(refresh_token)
    save_account_metadata(email, secret_version)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Email Assistant Connected</title>
  <style>
    body {{
      color: #17202a;
      font-family: Arial, sans-serif;
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f5f7fa;
    }}
    main {{
      width: min(560px, calc(100vw - 40px));
      background: white;
      border: 1px solid #d8dee8;
      border-radius: 8px;
      padding: 28px;
      box-shadow: 0 10px 24px rgba(23, 32, 42, 0.08);
    }}
    h1 {{ font-size: 24px; margin: 0 0 12px; }}
    p {{ line-height: 1.5; margin: 0; }}
    code {{ background: #eef2f7; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <main>
    <h1>Gmail connected</h1>
    <p>Connected mailbox <code>{email}</code>. The refresh token was saved to Secret Manager as <code>{token_secret_name()}</code>.</p>
  </main>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
