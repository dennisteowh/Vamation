#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

CAMPAIGN_ID = "13637777"
API_URL = (
    "https://www.patreon.com/api/posts?"
    "filter[campaign_id]=13637777&"
    "filter[contains_exclusive_posts]=true&"
    "filter[is_draft]=false&"
    "sort=-published_at&"
    "include=attachments,audio,images,poll.choices,poll.current_user_responses.user,poll.current_user_responses.poll_choice,user,user_defined_tags,ti_checks&"
    "fields[post]=change_visibility_at,comment_count,content,current_user_can_comment,current_user_can_view,current_user_has_liked,embed,image,is_paid,like_count,min_cents_pledged_to_view,post_file,published_at,patron_count,patreon_url,post_type,pledge_url,preview_asset_type,thumbnail_url,title,upgrade_url,url,was_posted_by_campaign_owner,has_ti_violation&"
    "fields[user]=image_url,full_name,url&"
    "fields[campaign]=avatar_photo_url,earnings_visibility,is_nsfw,is_monthly,name,url&"
    "fields[attachment]=name,url&"
    "fields[image]=height,width,url,file_name&"
    "json-api-use-default-includes=false&"
    "json-api-version=1.0"
)


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), check=check, text=True, capture_output=True)


def apply_cookies(session: str, cookie_file: Path) -> None:
    run("agent-browser", "--session", session, "close", "--all", check=False)
    run("agent-browser", "--session", session, "open", "about:blank")
    run("agent-browser", "--session", session, "cookies", "clear")
    cookies = json.loads(cookie_file.read_text())
    for c in cookies:
        cmd = [
            "agent-browser", "--session", session,
            "cookies", "set", c["name"], c["value"],
            "--domain", c["domain"],
            "--path", "/",
            "--secure",
        ]
        if c["domain"].startswith("."):
            cmd += ["--url", "https://www.patreon.com"]
        else:
            cmd += ["--url", f"https://{c['domain']}"]
        run(*cmd)


def probe(session: str, cookie_file: Path) -> dict:
    apply_cookies(session, cookie_file)
    open_res = run("agent-browser", "--session", session, "open", "https://www.patreon.com", check=False)
    title = run("agent-browser", "--session", session, "get", "title", check=False).stdout.strip()
    url = run("agent-browser", "--session", session, "get", "url", check=False).stdout.strip()
    js = (
        'fetch(' + json.dumps(API_URL) + ',{' 
        'credentials:"include",headers:{"Accept":"application/vnd.api+json"}'
        '}).then(async r=>({status:r.status,url:r.url,text:(await r.text()).slice(0,2000)}))'
    )
    eval_res = run("agent-browser", "--session", session, "eval", js, check=False)
    payload = None
    try:
        payload = json.loads(eval_res.stdout.strip().splitlines()[-1])
    except Exception:
        payload = {"raw": eval_res.stdout}
    return {
        "cookie_file": str(cookie_file),
        "open_stdout": open_res.stdout,
        "title": title,
        "url": url,
        "fetch": payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cookie_file")
    parser.add_argument("--session", default="vamation-browser-metadata-probe")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = probe(args.session, Path(args.cookie_file))
    text = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
