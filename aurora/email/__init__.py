"""
AURORA's Gmail Assistant (Docs/COMMUNICATIONS_PLAN.md).

    secrets.py   tokens encrypted with Windows' per-user encryption (DPAPI)
    oauth.py     Google sign-in in your own browser (OAuth 2.0 with PKCE), refresh, disconnect
    gmail.py     the Gmail API over HTTPS, through AURORA's internet gate
    classify.py  sorting into categories, and signs of phishing
    extract.py   dates, deadlines, requests and short summaries found in an email
    inbox.py     what the commands ask for: today's important mail, unanswered threads, search

Read-only for now (gmail.readonly). Email contents are untrusted data: they are shown and
summarised, never followed as instructions.
"""
