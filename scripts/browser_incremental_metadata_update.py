#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.metadata_handler import MetadataHandler, safe_save_json
from shared.path_config import POSTS_METADATA_JSON, ensure_common_directories

SGT = pytz.timezone("Asia/Singapore")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally update Vamation posts metadata through a live authenticated browser session."
    )
    parser.add_argument("--cdp-port", type=int, default=9225, help="Chromium DevTools port")
    parser.add_argument("--campaign-id", default="13637777", help="Patreon campaign ID")
    parser.add_argument(
        "--output",
        type=Path,
        default=POSTS_METADATA_JSON,
        help="Metadata JSON to update in place",
    )
    parser.add_argument(
        "--start-date",
        help="Optional SGT start date override in YYYY-MM-DD. Defaults to latest metadata post date day.",
    )
    parser.add_argument(
        "--end-date",
        help="Optional SGT end date override in YYYY-MM-DD inclusive. Defaults to no upper bound.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=20,
        help="Posts to request per Patreon page",
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
          let body = null;
          try {{
            body = JSON.parse(text);
          }} catch {{
            body = {{ raw_text: text }};
          }}
          return JSON.stringify({{
            status: response.status,
            content_type: response.headers.get("content-type"),
            body,
          }});
        }})()
    """
    result = run_agent_browser_eval(cdp_port, expression)
    if result.get("status") != 200:
        raise RuntimeError(f"Browser fetch failed for {url}: HTTP {result.get('status')}")
    return result["body"]


def browser_fetch_text(cdp_port: int, url: str) -> str:
    expression = f"""
        (async () => {{
          const response = await fetch({json.dumps(url)}, {{
            credentials: "include",
            headers: {{ "Accept": "text/html,application/xhtml+xml" }},
          }});
          const text = await response.text();
          return JSON.stringify({{
            status: response.status,
            body: text,
          }});
        }})()
    """
    result = run_agent_browser_eval(cdp_port, expression)
    if result.get("status") != 200:
        raise RuntimeError(f"Browser fetch failed for {url}: HTTP {result.get('status')}")
    return result.get("body") or ""


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
    extension = Path(original_filename).suffix if original_filename and "." in original_filename else ".jpg"
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


def make_zip_info(post_id: str, media_id: str | None, file_name: str, size_bytes: int | None, mimetype: str | None) -> dict:
    download_url = None
    if media_id:
        download_url = f"https://www.patreon.com/file?h={post_id}&m={media_id}&_rsc=1vnqu"
    return {
        "filename": file_name,
        "size_bytes": size_bytes,
        "media_id": media_id,
        "download_url": download_url,
        "mimetype": mimetype or "application/zip",
        "downloaded": False,
        "extracted": False,
        "download_date": None,
        "local_filename": None,
        "size_mb": round(size_bytes / (1024 * 1024), 2) if size_bytes else None,
    }


def extract_zip_files_from_html(post_id: str, post_html: str) -> list[dict]:
    html_text = html.unescape(post_html or "")
    matches = list(
        re.finditer(
            rf"https://www\.patreon\.com/file\?h={re.escape(str(post_id))}&(?:amp;)?m=(\d+)[^\"'<>\s]*",
            html_text,
        )
    )
    zip_files = []
    seen_media_ids = set()
    for match in matches:
        media_id = match.group(1)
        if media_id in seen_media_ids:
            continue
        seen_media_ids.add(media_id)
        raw_url = match.group(0)
        canonical_url = f"https://www.patreon.com/file?h={post_id}&m={media_id}&_rsc=1vnqu"
        nearby = html_text[match.end() : match.end() + 2000]
        filename_match = re.search(r">([^<>]{1,240}?\.zip)<\/p>", nearby, re.IGNORECASE)
        filename = filename_match.group(1).strip() if filename_match else f"{post_id}_{media_id}.zip"
        zip_files.append(
            {
                "filename": filename,
                "size_bytes": None,
                "media_id": str(media_id),
                "download_url": canonical_url,
                "mimetype": "application/zip",
                "downloaded": False,
                "extracted": False,
                "download_date": None,
                "local_filename": None,
                "size_mb": None,
            }
        )
    return zip_files


def get_zip_info(cdp_port: int, post_id: str, detail_body: dict, post_path: str | None) -> dict:
    zip_files = []
    seen_media_ids = set()
    for item in detail_body.get("included", []):
        if item.get("type") != "media":
            continue
        attrs = item.get("attributes", {})
        file_name = attrs.get("file_name", "")
        if not file_name.lower().endswith(".zip"):
            continue
        media_id = str(item.get("id")) if item.get("id") is not None else None
        if media_id and media_id in seen_media_ids:
            continue
        if media_id:
            seen_media_ids.add(media_id)
        size_bytes = attrs.get("size_bytes")
        zip_files.append(make_zip_info(post_id, media_id, file_name, size_bytes, attrs.get("mimetype")))

    if not zip_files:
        page_path = post_path or f"/posts/{post_id}"
        page_url = urljoin("https://www.patreon.com", page_path)
        page_html = browser_fetch_text(cdp_port, page_url)
        zip_files = extract_zip_files_from_html(post_id, page_html)

    return {
        "has_zip_files": bool(zip_files),
        "zip_files": zip_files,
    }


def normalize_metadata(cdp_port: int, post_id: str, detail_body: dict) -> dict:
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
    metadata.update(get_zip_info(cdp_port, post_id, detail_body, metadata.get("patreon_url")))
    metadata.update(get_profile_images_info(post_id, attrs))
    return metadata


def merge_existing_local_state(existing_post: dict, fresh_post: dict) -> dict:
    merged = dict(fresh_post)
    merged["scraped_date"] = datetime.now(SGT).isoformat()

    for field in ["revised_post_name", "display", "favourite", "cascade_metadata"]:
        if field in existing_post:
            merged[field] = existing_post.get(field)

    existing_zips = existing_post.get("zip_files") or []
    fresh_zips = merged.get("zip_files") or []
    if existing_zips and fresh_zips:
        existing_zip_map = {}
        for zip_info in existing_zips:
            if not isinstance(zip_info, dict):
                continue
            filename = zip_info.get("filename")
            media_id = zip_info.get("media_id")
            if filename:
                existing_zip_map[("filename", filename)] = zip_info
            if media_id:
                existing_zip_map[("media_id", str(media_id))] = zip_info

        for fresh_zip in fresh_zips:
            if not isinstance(fresh_zip, dict):
                continue
            match = None
            if fresh_zip.get("filename"):
                match = existing_zip_map.get(("filename", fresh_zip.get("filename")))
            if match is None and fresh_zip.get("media_id"):
                match = existing_zip_map.get(("media_id", str(fresh_zip.get("media_id"))))
            if match is None:
                continue

            for key in ["downloaded", "extracted", "download_date", "local_filename"]:
                if key in match:
                    fresh_zip[key] = match.get(key)
            for key, value in match.items():
                if key not in fresh_zip:
                    fresh_zip[key] = value

    existing_images = existing_post.get("profile_images") or []
    fresh_images = merged.get("profile_images") or []
    if existing_images and fresh_images:
        existing_image_map = {}
        for image_info in existing_images:
            if not isinstance(image_info, dict):
                continue
            filename = image_info.get("filename")
            image_type = image_info.get("type")
            index = image_info.get("index")
            if filename:
                existing_image_map[("filename", filename)] = image_info
            existing_image_map[("slot", image_type, index)] = image_info

        for fresh_image in fresh_images:
            if not isinstance(fresh_image, dict):
                continue
            match = None
            if fresh_image.get("filename"):
                match = existing_image_map.get(("filename", fresh_image.get("filename")))
            if match is None:
                match = existing_image_map.get(("slot", fresh_image.get("type"), fresh_image.get("index")))
            if match is None:
                continue

            if "downloaded" in match:
                fresh_image["downloaded"] = match.get("downloaded")
            for key, value in match.items():
                if key not in fresh_image:
                    fresh_image[key] = value

    return merged


def normalize_schema(post: dict) -> dict:
    normalized = dict(post)
    normalized["revised_post_name"] = normalized.get("revised_post_name") or normalized.get("post_name", "")
    normalized["display"] = normalized.get("display", True)
    normalized["favourite"] = normalized.get("favourite", False)
    normalized["description"] = normalized.get("description", "") or ""
    normalized["patreon_url"] = normalized.get("patreon_url", "") or ""
    normalized["post_type"] = normalized.get("post_type", "") or ""
    normalized["post_date"] = normalized.get("post_date")
    normalized["scraped_date"] = normalized.get("scraped_date")
    normalized["has_zip_files"] = normalized.get("has_zip_files", False)
    normalized["zip_files"] = normalized.get("zip_files") or []
    normalized["profile_images"] = normalized.get("profile_images") or []
    normalized["profile_images_count"] = len(normalized["profile_images"])
    normalized["cascade_metadata"] = normalized.get("cascade_metadata") or {}
    return normalized


def build_posts_listing_url(campaign_id: str, page_size: int, cursor: str | None = None) -> str:
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
        "page[count]": str(page_size),
    }
    if cursor:
        params["page[cursor]"] = cursor
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


def extract_cursor(listing_body: dict) -> str | None:
    next_link = (listing_body.get("links") or {}).get("next")
    if not next_link:
        return None
    parsed = urllib.parse.urlparse(next_link)
    query = urllib.parse.parse_qs(parsed.query)
    cursor_values = query.get("page[cursor]") or query.get("page%5Bcursor%5D")
    if cursor_values:
        return cursor_values[0]
    if "page%5Bcursor%5D=" in next_link:
        fragment = next_link.split("page%5Bcursor%5D=", 1)[1]
        return urllib.parse.unquote(fragment.split("&", 1)[0])
    if "page[cursor]=" in next_link:
        fragment = next_link.split("page[cursor]=", 1)[1]
        return fragment.split("&", 1)[0]
    return None


def load_existing_metadata(path: Path) -> dict:
    if not path.exists():
        return {"summary": {}, "posts": []}
    return json.loads(path.read_text(encoding="utf-8"))


def get_latest_post_day(existing_metadata: dict) -> str | None:
    latest_dt = None
    for post in existing_metadata.get("posts", []):
        post_date = post.get("post_date")
        if not post_date:
            continue
        try:
            post_dt = datetime.fromisoformat(post_date.replace("Z", "+00:00"))
        except Exception:
            continue
        if latest_dt is None or post_dt > latest_dt:
            latest_dt = post_dt
    if latest_dt is None:
        return None
    return latest_dt.astimezone(SGT).strftime("%Y-%m-%d")


def parse_day_bounds(start_date: str | None, end_date: str | None) -> tuple[datetime | None, datetime | None]:
    start_dt = None
    end_dt = None
    if start_date:
        start_dt = SGT.localize(datetime.strptime(start_date, "%Y-%m-%d"))
    if end_date:
        end_dt = SGT.localize(datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1))
    return start_dt, end_dt


def sort_posts(posts: list[dict]) -> None:
    posts.sort(
        key=lambda p: (
            p.get("post_date") is None,
            -(datetime.fromisoformat(p["post_date"].replace("Z", "+00:00")).timestamp() if p.get("post_date") else 0),
            p.get("post_name", "").lower(),
        )
    )


def save_metadata(output_path: Path, metadata: list[dict], added_count: int, refreshed_count: int) -> None:
    summary = {
        "extraction_date": datetime.now(SGT).isoformat(),
        "status": "COMPLETED",
        "total_posts": len(metadata),
        "posts_with_images": len([m for m in metadata if m.get("profile_images_count", 0) > 0]),
        "total_images_downloaded": sum([m.get("profile_images_count", 0) for m in metadata]),
        "posts_with_zip_files": len([m for m in metadata if m.get("has_zip_files", False)]),
        "run_details": {
            "processed_in_range": added_count + refreshed_count,
            "added_posts": added_count,
            "updated_posts": refreshed_count,
        },
        "date_range": {
            "earliest": min([m["post_date"] for m in metadata if m.get("post_date")], default=None),
            "latest": max([m["post_date"] for m in metadata if m.get("post_date")], default=None),
        },
    }
    safe_save_json({"summary": summary, "posts": metadata}, output_path, create_backup=True)
    handler = MetadataHandler()
    handler.json_to_excel(create_backup=True)


def main() -> int:
    args = parse_args()
    ensure_common_directories()

    existing_metadata = load_existing_metadata(args.output)
    existing_posts = {
        str(post["post_id"]): normalize_schema(post)
        for post in existing_metadata.get("posts", [])
        if post.get("post_id") is not None
    }

    start_date = args.start_date or get_latest_post_day(existing_metadata)
    end_date = args.end_date
    start_dt, end_dt = parse_day_bounds(start_date, end_date)

    if start_date:
        print(f"Incremental browser update from {start_date} onwards (SGT)")
    else:
        print("Incremental browser update with no existing metadata boundary; full history mode")
    if end_date:
        print(f"Upper bound: {end_date} inclusive (SGT)")

    cursor = None
    page_num = 1
    total_posts_in_range = 0
    added_count = 0
    refreshed_count = 0

    while True:
        listing_body = browser_fetch_json(
            args.cdp_port,
            build_posts_listing_url(args.campaign_id, args.page_size, cursor),
        )
        posts = listing_body.get("data", [])
        if not posts:
            break

        print(f"Page {page_num}: {len(posts)} posts")
        oldest_date_on_page = None

        for post in posts:
            post_id = str(post.get("id"))
            attrs = post.get("attributes", {})
            published_str = attrs.get("published_at")
            if not published_str:
                continue

            published_dt = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
            published_sgt = published_dt.astimezone(SGT)
            if oldest_date_on_page is None or published_sgt < oldest_date_on_page:
                oldest_date_on_page = published_sgt

            in_range = True
            if start_dt and published_sgt < start_dt:
                in_range = False
            if end_dt and published_sgt >= end_dt:
                in_range = False
            if not in_range:
                continue

            detail_body = browser_fetch_json(args.cdp_port, build_post_detail_url(post_id))
            fresh_metadata = normalize_schema(normalize_metadata(args.cdp_port, post_id, detail_body))

            if post_id in existing_posts:
                existing_posts[post_id] = normalize_schema(merge_existing_local_state(existing_posts[post_id], fresh_metadata))
                refreshed_count += 1
            else:
                existing_posts[post_id] = normalize_schema(fresh_metadata)
                added_count += 1

            total_posts_in_range += 1

        if start_dt and oldest_date_on_page and oldest_date_on_page < start_dt:
            break
        if len(posts) < args.page_size:
            break

        cursor = extract_cursor(listing_body)
        if not cursor:
            break
        page_num += 1

    metadata = list(existing_posts.values())
    sort_posts(metadata)
    save_metadata(args.output, metadata, added_count, refreshed_count)

    print(f"Updated metadata: {args.output}")
    print(f"Processed in range: {total_posts_in_range}")
    print(f"Added posts: {added_count}")
    print(f"Updated posts: {refreshed_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
