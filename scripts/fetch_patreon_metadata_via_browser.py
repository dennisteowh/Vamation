#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.path_config import DERIVED_DIR, ensure_common_directories

SGT = ZoneInfo("Asia/Singapore")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Patreon metadata through a live authenticated browser session."
    )
    parser.add_argument("--cdp-port", type=int, default=9225, help="Chromium DevTools port")
    parser.add_argument("--campaign-id", default="13637777", help="Patreon campaign ID")
    parser.add_argument("--limit", type=int, default=5, help="Number of newest posts to fetch")
    parser.add_argument(
        "--output",
        type=Path,
        default=DERIVED_DIR / "patreon_browser_metadata_test.json",
        help="Output path for the test metadata JSON",
    )
    return parser.parse_args()


def run_agent_browser_eval(cdp_port: int, expression: str) -> object:
    proc = subprocess.run(
        ["agent-browser", "--cdp", str(cdp_port), "eval", expression],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = proc.stdout.strip()
    if not payload:
        raise RuntimeError("Empty response from agent-browser eval")
    parsed = json.loads(payload)
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    return parsed


def browser_fetch_json(cdp_port: int, url: str) -> dict:
    expression = f"""
        (async () => {{
          const response = await fetch({json.dumps(url)}, {{
            credentials: "include",
            headers: {{ "Accept": "application/vnd.api+json" }},
          }});
          const text = await response.text();
          return JSON.stringify({{
            status: response.status,
            content_type: response.headers.get("content-type"),
            body: JSON.parse(text),
          }});
        }})()
    """
    result = run_agent_browser_eval(cdp_port, expression)
    if result.get("status") != 200:
        raise RuntimeError(f"Browser fetch failed for {url}: HTTP {result.get('status')}")
    return result["body"]


def clean_html_content(content: str | None) -> str:
    if not content:
        return ""
    clean_text = re.sub(r"<[^>]+>", "", str(content))
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    if len(clean_text) > 500:
        clean_text = clean_text[:497] + "..."
    return clean_text


def format_post_date(published_str: str | None) -> str | None:
    if not published_str:
        return None
    published_dt = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
    return published_dt.astimezone(SGT).isoformat()


def create_image_info(post_id: str, image_url: str, image_index: int, image_type: str) -> dict | None:
    if not image_url:
        return None
    parsed_url = urlparse(image_url)
    original_filename = Path(parsed_url.path).name
    if not original_filename or "." not in original_filename:
        extension = ".jpg"
    else:
        extension = Path(original_filename).suffix
    safe_filename = f"{post_id}_{image_type}{extension}"
    return {
        "url": image_url,
        "filename": safe_filename,
        "index": image_index,
        "type": image_type,
        "downloaded": False,
    }


def get_profile_images_info(post_id: str, attrs: dict) -> dict:
    images = []
    thumbnail_url = attrs.get("thumbnail_url")
    if thumbnail_url:
        image_data = create_image_info(post_id, thumbnail_url, 1, "thumbnail")
        if image_data:
            images.append(image_data)
    else:
        main_image = attrs.get("image")
        if isinstance(main_image, dict) and main_image.get("url"):
            image_data = create_image_info(post_id, main_image["url"], 1, "main")
            if image_data:
                images.append(image_data)
    return {
        "profile_images": images,
        "profile_images_count": len(images),
    }


def get_zip_info(post_id: str, detail_body: dict) -> dict:
    zip_files = []
    for item in detail_body.get("included", []):
        if item.get("type") != "media":
            continue
        attrs = item.get("attributes", {})
        file_name = attrs.get("file_name", "")
        if not file_name.lower().endswith(".zip"):
            continue
        size_bytes = attrs.get("size_bytes")
        zip_info = {
            "filename": file_name,
            "size_bytes": size_bytes,
            "media_id": item.get("id"),
            "download_url": f"https://www.patreon.com/file?h={post_id}&m={item.get('id')}&_rsc=1vnqu",
            "mimetype": attrs.get("mimetype"),
            "downloaded": False,
            "extracted": False,
            "download_date": None,
            "local_filename": None,
        }
        zip_info["size_mb"] = round(size_bytes / (1024 * 1024), 2) if size_bytes else None
        zip_files.append(zip_info)
    return {
        "has_zip_files": bool(zip_files),
        "zip_files": zip_files,
    }


def normalize_metadata(post_id: str, detail_body: dict) -> dict:
    attrs = detail_body.get("data", {}).get("attributes", {})
    metadata = {
        "post_id": post_id,
        "post_name": attrs.get("title", "Untitled"),
        "revised_post_name": attrs.get("title", "Untitled"),
        "display": True,
        "favourite": False,
        "description": clean_html_content(attrs.get("content")),
        "patreon_url": attrs.get("patreon_url", "") or "",
        "post_type": attrs.get("post_type", "") or "",
        "post_date": format_post_date(attrs.get("published_at")),
        "scraped_date": datetime.now(SGT).isoformat(),
        "cascade_metadata": {},
    }
    metadata.update(get_zip_info(post_id, detail_body))
    metadata.update(get_profile_images_info(post_id, attrs))
    return metadata


def build_posts_listing_url(campaign_id: str, limit: int) -> str:
    params = {
        "filter[campaign_id]": campaign_id,
        "filter[contains_exclusive_posts]": "true",
        "filter[is_draft]": "false",
        "sort": "-published_at",
        "include": "attachments,audio,images,poll.choices,poll.current_user_responses.user,poll.current_user_responses.poll_choice,user,user_defined_tags,ti_checks",
        "fields[post]": "change_visibility_at,comment_count,content,current_user_can_comment,current_user_can_view,current_user_has_liked,embed,image,is_paid,like_count,min_cents_pledged_to_view,post_file,published_at,patron_count,patreon_url,post_type,pledge_url,preview_asset_type,thumbnail_url,title,upgrade_url,url,was_posted_by_campaign_owner,has_ti_violation",
        "fields[user]": "image_url,full_name,url",
        "fields[campaign]": "avatar_photo_url,earnings_visibility,is_nsfw,is_monthly,name,url",
        "fields[attachment]": "name,url",
        "fields[image]": "height,width,url,file_name",
        "json-api-use-default-includes": "false",
        "json-api-version": "1.0",
        "page[count]": str(limit),
    }
    return f"https://www.patreon.com/api/posts?{urlencode(params)}"


def build_post_detail_url(post_id: str) -> str:
    params = {
        "include": "attachments,attachment_media,user,user_defined_tags,campaign,access_rules,content_unlocks,poll.choices,poll.options,audio",
        "fields[post]": "title,content,embed,image,post_file,published_at,current_user_can_view,is_paid,visibility,teaser_text,post_type,thumbnail_url,patreon_url",
        "fields[media]": "download_url,image_urls,file_name,mimetype,size_bytes,state",
        "fields[attachment]": "name,url,download_url,mimetype,size_bytes",
        "fields[user]": "full_name,url",
        "fields[campaign]": "name,url",
        "json-api-use-default-includes": "false",
    }
    return f"https://www.patreon.com/api/posts/{post_id}?{urlencode(params)}"


def save_output(output_path: Path, metadata: list[dict], campaign_id: str, limit: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "extraction_date": datetime.now(SGT).isoformat(),
        "status": "BROWSER_TEST",
        "campaign_id": campaign_id,
        "total_posts": len(metadata),
        "posts_with_images": len([m for m in metadata if m.get("profile_images_count", 0) > 0]),
        "total_images_downloaded": sum([m.get("profile_images_count", 0) for m in metadata]),
        "posts_with_zip_files": len([m for m in metadata if m.get("has_zip_files", False)]),
        "limit_requested": limit,
        "date_range": {
            "earliest": min([m["post_date"] for m in metadata if m.get("post_date")], default=None),
            "latest": max([m["post_date"] for m in metadata if m.get("post_date")], default=None),
        },
    }
    output_data = {
        "summary": summary,
        "posts": metadata,
    }
    output_path.write_text(json.dumps(output_data, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    ensure_common_directories()

    listing_body = browser_fetch_json(args.cdp_port, build_posts_listing_url(args.campaign_id, args.limit))
    posts = listing_body.get("data", [])

    metadata = []
    for post in posts:
        post_id = post.get("id")
        if not post_id:
            continue
        detail_body = browser_fetch_json(args.cdp_port, build_post_detail_url(post_id))
        metadata.append(normalize_metadata(post_id, detail_body))

    metadata.sort(
        key=lambda p: (
            p.get("post_date") or "",
            p.get("post_id") or "",
        ),
        reverse=True,
    )

    save_output(args.output, metadata, args.campaign_id, args.limit)
    print(f"Wrote browser-backed metadata test output: {args.output}")
    print(f"Posts fetched: {len(metadata)}")
    for post in metadata[: min(len(metadata), 5)]:
        print(f"- {post.get('post_id')} | {post.get('post_date')} | {post.get('post_name')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or str(exc), file=sys.stderr)
        raise
