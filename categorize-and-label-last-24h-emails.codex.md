# Categorize And Label Recent Gmail By Configured Lookback

Use the Gmail connector to summarize email received within the configured lookback window, categorize each message using the editable keyword rules in `email-categories.config.json`, and apply Gmail labels based on the matched category names.

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
7. Apply Gmail labels to each categorized email.
   - The Gmail label name must exactly match the category name from the config.
   - For example, an email in the `Finance` category must receive the `Finance` label.
   - If a message appears in multiple categories, apply every matching category label.
   - If a message only uses `fallback_category`, apply a label exactly matching the fallback category name.
   - Create missing Gmail labels before applying them.
   - Label the original Gmail messages only; do not label the digest draft.
8. Produce a concise digest grouped by category.
9. Create a Gmail draft containing the same digest.
   - Address the draft to the authenticated Gmail account unless the user specifies a different recipient.
   - Use this subject format: `Email Summary for you - <YYYY-MM-DD HH:mm z>`
   - Use the run date and time in the user's local timezone for the subject timestamp.
   - Put the full digest in the draft body.
   - After creating the draft, report the draft ID, subject, and labels applied.

## Output Format

Start with a short count summary:

- Total received emails scanned
- Number of emails in each category
- Number of uncategorized emails
- Number of emails labeled

Then list categorized emails like this:

```text
Category Name
- Sender: <from>
  Subject: <subject>
  Time: <email_ts>
  Labels applied: <category labels>
  Why matched: <matched keywords>
  Summary: <one sentence summary>
  Suggested next step: <reply/review/file/archive/ignore>
```

## Important Rules

- Do not send emails.
- Create exactly one Gmail draft for the digest as part of this task.
- Do not create any other drafts unless explicitly asked after the digest is shown.
- Do not include long quoted email bodies.
- For financial, medical, tax, or security emails, summarize minimally and recommend reviewing the official portal directly.
- Prefer practical categories over perfect classification.
- If the config file is invalid JSON, stop and explain the exact issue.
