# Categorize Recent Gmail By Configured Lookback

Use the Gmail connector to summarize email received within the configured lookback window and categorize each message using the editable keyword rules in `email-categories.config.json`.

## Steps

1. Read `email-categories.config.json`.
2. Read the config's `lookback_hours` number.
   - This controls how many hours back the run should include.
   - Default value: `24`
   - If missing, invalid, or not a positive number, stop and explain the exact issue.
3. Search Gmail using the config's `gmail_query` as the broad mailbox filter.
   - Default query: `newer_than:1d -in:spam -in:trash -in:sent`
   - This should exclude spam, trash, and sent mail.
4. Read all matching messages.
5. Keep only received emails whose `email_ts` is within `lookback_hours` of the run time in the user's local timezone.
6. For each remaining email, build searchable text from the fields listed in `match_fields`.
   - Matching is case-insensitive.
   - A message can appear in multiple categories if it matches multiple category keyword sets.
   - If no category matches, use `fallback_category`.
7. Produce a concise count summary grouped by category.
   - Include only category names and counts.
   - Do not list individual email details in the run output.
8. Create one separate Gmail draft for the count summary.
   - Address the draft to the authenticated Gmail account unless the user specifies a different recipient.
   - Use this subject format: `Email Category Summary - <YYYY-MM-DD HH:mm z>`
   - Use the run date and time in the user's local timezone for the subject timestamp.
   - Put only the category names and email counts in the draft body.
   - After creating the summary draft, report the draft ID and subject.
9. For each remaining email that has the `Finance` category, create one individual Gmail draft for that email.
   - Address the draft to the authenticated Gmail account unless the user specifies a different recipient.
   - Use this subject format: `Finance email review - <original email subject>`
   - Put only that Finance email's sender, subject, received time, matched Finance keywords, a minimal one-sentence summary, and suggested next step in the draft body.
   - For the suggested next step, recommend reviewing the official portal directly.
   - After creating drafts, report each draft ID and subject.
   - If no remaining email has the `Finance` category, do not create a draft and report that draft creation was skipped because no Finance emails matched.

## Output Format

Output only a short count summary:

- <Category Name>: <count>

## Important Rules

- Do not send emails.
- Create exactly one Gmail draft for the category count summary on each run.
- Create one individual Gmail draft for each email that has the `Finance` category.
- Do not create individual Finance drafts when no email has the `Finance` category.
- Do not include individual email details in the summary draft.
- Do not create drafts for non-Finance emails unless explicitly asked after the summary is shown.
- Do not include long quoted email bodies.
- For financial, medical, tax, or security emails, summarize minimally and recommend reviewing the official portal directly.
- Prefer practical categories over perfect classification.
- If the config file is invalid JSON, stop and explain the exact issue.
