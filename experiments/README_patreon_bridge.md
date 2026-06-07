# Patreon auth handoff bridge

This experiment now doubles as the pipeline fallback auth bridge.

## Purpose

- Reuse the normal project `cookies.txt` when it is still valid.
- Only if `cookies.txt` is missing or invalid, launch a fresh Zo-side Patreon auth handoff flow.
- Let a human complete Cloudflare and Patreon login in the handoff browser.
- Export authenticated Patreon cookies back into the real project cookie file automatically.
- Record when authentication completed in a separate metadata log without changing the downstream cookie file format.

## Real pipeline behaviour

When the main pipeline runs:

1. It tries `cookies.txt` first.
2. If those cookies still validate, the pipeline continues normally.
3. If they do not validate, it launches the auth handoff browser and exits quickly.
4. While the browser is open, a background watcher keeps checking for a valid authenticated Patreon session.
5. Once the login is genuinely valid for the Patreon API, it automatically:
   - overwrites `cookies.txt`
   - writes `data/metadata/cookies_auth_metadata.json`

## Files

- `patreon_auth_handoff.py` — browser handoff + automatic cookie export helper
- `artifacts/auth-handoff/status.json` — live bridge status
- `artifacts/auth-handoff/latest.png` — latest screenshot when captured
- `artifacts/auth-handoff/pipeline-cookie-batch.json` — experiment export target
- `artifacts/auth-handoff/pipeline-cookie-batch.auth.json` — experiment auth metadata
- `data/metadata/cookies_auth_metadata.json` — real pipeline auth metadata log

## Notes

- The cookie file remains a plain cookie array so downstream code still works unchanged.
- Authentication timing and validation details are stored separately in JSON metadata.
- Auto-export only writes the real cookie file after the batch passes validation.
- This avoids silently keeping an old invalid cookie batch around.
