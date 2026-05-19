# Email Assistant Gmail Onboarding Web App

This Cloud Run app lets one client connect Gmail and saves that mailbox's refresh token to Secret Manager.

It is intentionally single-client per deployment. To customize behavior per client, deploy a separate service or use a separate `CLIENT_SLUG` and `REFRESH_TOKEN_SECRET_NAME`.

## Deploy

From the repository root:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  gmail.googleapis.com

gcloud iam service-accounts create emailassistant-web \
  --display-name="Email Assistant Web"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:emailassistant-web@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.admin"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:emailassistant-web@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

gcloud run deploy emailassistant-web \
  --source web \
  --region us-west1 \
  --allow-unauthenticated \
  --service-account emailassistant-web@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --set-env-vars GCP_PROJECT_ID=YOUR_PROJECT_ID,CLIENT_SLUG=mohanmain,REFRESH_TOKEN_SECRET_NAME=gmail-refresh-token-mohanmain \
  --set-secrets GMAIL_CLIENT_ID=google-oauth-client-mohanmain:latest,GMAIL_CLIENT_SECRET=google-oauth-client-secret-mohanmain:latest
```

After deployment, copy the Cloud Run service URL and add this redirect URI to the Google OAuth client:

```text
https://YOUR-CLOUD-RUN-URL/auth/google/callback
```

Then pin the callback URL in Cloud Run so Google OAuth always receives the exact registered URI:

```bash
gcloud run services update emailassistant-web \
  --region us-west1 \
  --set-env-vars OAUTH_REDIRECT_URI=https://YOUR-CLOUD-RUN-URL/auth/google/callback
```

Then open the Cloud Run URL and click **Connect Gmail**.

## Stored Data

Secret Manager:

```text
gmail-refresh-token-mohanmain
```

Firestore:

```text
email_accounts/mohanmain
```
