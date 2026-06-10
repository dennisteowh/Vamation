# Patreon browser auth workflow

Purpose: the durable reference for the successful Patreon login handoff that was verified on Zo on 2026-06-09.

This document records the workflow that did work, the files involved, and the main caveat discovered during testing.

## Summary

What worked:

1. Launch a real headed Chromium session behind a temporary noVNC handoff
2. Complete Patreon login and Cloudflare verification interactively in that browser
3. Extract the live Patreon cookies from the running browser through Chromium DevTools
4. Use the authenticated browser context itself to fetch Patreon metadata successfully

What did not work in the same test:

- Copying the extracted cookies into a plain Python `requests.Session()` still returned HTTP `403`

Conclusion:

- Browser-backed authenticated fetch is proven
- Raw cookie replay into the current server-side request path is not yet proven

## Important files

- Cookie jar expected by the pipeline: `/home/workspace/Projects/Vamation/cookies.txt`
- Cookie metadata record: `/home/workspace/Projects/Vamation/data/metadata/cookies_auth_metadata.json`
- Cookie archive directory: `/home/workspace/Projects/Vamation/data/metadata/backups/cookies/`
- Existing manual metadata probe reference: `/home/workspace/Projects/Vamation/docs/patreon-metadata-fetch-reference.md`
- Browser handoff prep script: `/home/workspace/Projects/Vamation/scripts/prepare_patreon_browser_auth.py`
- Cookie extraction helper added from this workflow: `/home/workspace/Projects/Vamation/scripts/extract_patreon_browser_cookies.py`

## Successful browser-session cookie set

The successful session included these Patreon cookies:

- `session_id`
- `patreon_device_id`
- `cf_clearance`
- `_cfuvid`
- `__cf_bm`
- `g_state`
- `analytics_session_id`
- `patreon_locale_code`
- `patreon_location_country_code`
- `stream_user_token`

Do not assume the old minimal cookie set is sufficient. In particular, `cf_clearance` was present in the successful session and should be preserved when available.

## Verified successful fetch result

Using the authenticated browser context, the latest Patreon post listing succeeded and the newest post metadata was fetched successfully.

Latest verified post during the test:

- post id: `160602840`
- title: `Rosemary Winters (Resident evil 8 Village)`
- published at: `2026-06-09T10:05:39.000+00:00`
- `current_user_can_view`: `true`

## Recommended refresh workflow

Use this when Patreon cookies need refreshing.

1. Run `scripts/prepare_patreon_browser_auth.py`
2. Wait for it to print the runtime details for the browser handoff
3. Surface the noVNC handoff link to the human user
4. Hand over control to the human user so they can complete the Patreon login and any Cloudflare challenge interactively
5. Keep the browser open after login succeeds
6. Run `scripts/extract_patreon_browser_cookies.py` against the live Chromium DevTools port
7. Validate with a browser-context metadata probe before closing the browser
8. Only after that, decide whether to:
   - keep using browser-context fetches, or
   - continue debugging the raw cookie replay path

## Successful extraction method

The successful extraction path used Chromium DevTools through `agent-browser`, not manual copy/paste from devtools.

Conceptually:

1. Connect to the live browser's DevTools port
2. Read the current cookie jar
3. Filter to Patreon cookies
4. Save:
   - archive the previous cookie jar and cookie metadata into `data/metadata/backups/cookies/` with a UTC timestamp in the filename
   - a Vamation-shaped cookie file: list of `{name, value, domain}`
   - a metadata record describing the export
   - optionally the full raw cookie dump for debugging

Example command shape:

```bash
python scripts/prepare_patreon_browser_auth.py

# Human handoff happens here:
# - surface the noVNC link produced by the prep script
# - user completes Patreon login and Cloudflare verification
# - keep the browser session running

python scripts/extract_patreon_browser_cookies.py \
  --cdp-port 9225 \
  --output /home/workspace/Projects/Vamation/cookies.txt \
  --metadata-output /home/workspace/Projects/Vamation/data/metadata/cookies_auth_metadata.json \
  --full-output /home/workspace/Projects/Vamation/data/metadata/patreon_browser_cookies_full.json
```

## Browser-context probe pattern

The browser-backed probe succeeded with a fetch from the logged-in Patreon browser tab using `credentials: "include"` and `Accept: application/vnd.api+json`.

That is the current known-good auth path.

## Current caveat

At the time of this workflow:

- browser-context fetch: `200`
- plain `requests.Session()` replay with the extracted cookies: `403`

So if a future cookie refresh appears to succeed but the pipeline still fails, treat that as a request-path mismatch, not as proof that the browser auth failed.
