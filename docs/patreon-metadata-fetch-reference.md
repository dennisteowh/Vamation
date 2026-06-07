# Patreon metadata fetch reference

Purpose: a known-good manual reference for fetching fresh Patreon metadata with the current cookie set, without using the pipeline. This is meant to be compared later against the metadata update pipeline when debugging why the pipeline fails.

## What was verified

Using the cookies in `/home/workspace/Projects/Vamation/cookies.txt`, direct Patreon API requests succeeded from this server.

Verified outcomes:
- `GET https://www.patreon.com/api/campaigns/13637777` returned HTTP 200
- `GET https://www.patreon.com/api/posts` returned HTTP 200 and listed many fresh posts not yet present in local metadata
- `GET https://www.patreon.com/api/posts/160281591` returned HTTP 200 with full post metadata

This suggests the server is not being blocked at the initial metadata retrieval step when using the current cookies and request shape below.

## Cookie source used

- Cookie metadata file: `/home/workspace/Projects/Vamation/data/metadata/cookies_auth_metadata.json`
- Cookie jar file: `/home/workspace/Projects/Vamation/cookies.txt`
- Cookie names recorded there:
  - `__cf_bm`
  - `_cfuvid`
  - `analytics_session_id`
  - `patreon_device_id`
  - `session_id`

## Known-good request setup

### Session setup
- Use a `requests.Session()`
- Load cookies from `cookies.txt`
- Set each cookie with domain from the exported cookie entry, defaulting to `.patreon.com`

### Headers that worked
```python
{
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/vnd.api+json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.patreon.com/',
    'Origin': 'https://www.patreon.com',
}
```

## Known-good campaign validation call

```python
import json, requests
from pathlib import Path

cookies = json.loads(Path('/home/workspace/Projects/Vamation/cookies.txt').read_text())
s = requests.Session()
for c in cookies:
    s.cookies.set(c['name'], c['value'], domain=c.get('domain', '.patreon.com'))

s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/vnd.api+json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.patreon.com/',
    'Origin': 'https://www.patreon.com',
})

r = s.get('https://www.patreon.com/api/campaigns/13637777', timeout=30)
print(r.status_code)
print(r.headers.get('server'))
print(r.headers.get('cf-ray'))
print(r.headers.get('content-type'))
print(r.text[:300])
```

Expected successful characteristics from the test run:
- status `200`
- server `cloudflare`
- content type `application/vnd.api+json`

## Known-good recent posts listing call

This was used to confirm fresh unseen posts are accessible.

```python
import json, requests
from pathlib import Path

cookies = json.loads(Path('/home/workspace/Projects/Vamation/cookies.txt').read_text())
s = requests.Session()
for c in cookies:
    s.cookies.set(c['name'], c['value'], domain=c.get('domain', '.patreon.com'))

s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/vnd.api+json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.patreon.com/',
    'Origin': 'https://www.patreon.com',
})

params = {
    'filter[campaign_id]': '13637777',
    'filter[contains_exclusive_posts]': 'true',
    'sort': '-published_at',
    'json-api-use-default-includes': 'false',
    'fields[post]': 'title,published_at,current_user_can_view,url,post_type',
    'page[count]': '20',
}

r = s.get('https://www.patreon.com/api/posts', params=params, timeout=30)
print(r.status_code)
print(r.headers.get('content-type'))
for item in r.json().get('data', [])[:5]:
    attrs = item.get('attributes', {})
    print(item.get('id'), attrs.get('published_at'), attrs.get('title'))
```

Fresh unseen posts observed in the successful test included:
- `160387793` — `Inesa (ThornSin) 1000 pics`
- `160385020` — `Yor Forger (Spy x Family) 500 pics`
- `160385287` — `leblanc (league of legends)`
- `160382232` — `nakano miku (go-toubun no hanayome) 1300 pics`

## Known-good single post metadata call

This successfully fetched full metadata for a recent post.

```python
import json, requests
from pathlib import Path

post_id = '160281591'
cookies = json.loads(Path('/home/workspace/Projects/Vamation/cookies.txt').read_text())
s = requests.Session()
for c in cookies:
    s.cookies.set(c['name'], c['value'], domain=c.get('domain', '.patreon.com'))

s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/vnd.api+json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.patreon.com/',
    'Origin': 'https://www.patreon.com',
})

params = {
    'include': 'attachments,attachment_media,user,user_defined_tags,campaign,access_rules,content_unlocks,poll.choices,poll.options,audio',
    'fields[post]': 'title,content,embed,image,post_file,published_at,current_user_can_view,is_paid,visibility,teaser_text',
    'fields[media]': 'download_url,image_urls,file_name,mimetype,size_bytes,state',
    'fields[attachment]': 'name,url,download_url,mimetype,size_bytes',
    'fields[user]': 'full_name,url',
    'fields[campaign]': 'name,url',
    'json-api-use-default-includes': 'false',
}

r = s.get(f'https://www.patreon.com/api/posts/{post_id}', params=params, timeout=30)
print(r.status_code)
print(r.headers.get('content-type'))
body = r.json()
print(body['data']['attributes'].get('title'))
print(body['data']['attributes'].get('published_at'))
print(len(body.get('included', [])))
```

Successful test result:
- post id: `160281591`
- title: `busujima saeko 500 pics`
- published_at: `2026-06-05T21:54:08.000+00:00`
- included objects count: `3`

## Comparison hypothesis for later pipeline debugging

When comparing the pipeline to this reference, check for differences in:
- request headers
- cookie loading/parsing
- cookie domains applied to the session
- use of `requests.Session()` vs one-off requests
- initial endpoint order
- query parameters sent to `/api/posts`
- date filtering logic
- retry logic that might overwrite a good auth state
- auth handoff / cookie refresh logic that might replace a working cookie batch with a broken one

## Important conclusion from this reference

At the time of this test, the following worked from this server with the current cookies:
- campaign metadata fetch
- recent posts listing fetch
- individual post metadata fetch

So any pipeline failure should be treated as a mismatch between the pipeline’s request/auth flow and this known-good manual request path, not assumed to be a blanket server-level Cloudflare block.
