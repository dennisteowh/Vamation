#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Patreon cookies from a live Chromium DevTools session via agent-browser."
    )
    parser.add_argument("--cdp-port", type=int, required=True, help="Chromium remote debugging port")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/home/workspace/Projects/Vamation/cookies.txt"),
        help="Output path for the Vamation-shaped cookie jar",
    )
    parser.add_argument(
        "--metadata-output",
        type=Path,
        default=Path("/home/workspace/Projects/Vamation/data/metadata/cookies_auth_metadata.json"),
        help="Output path for export metadata",
    )
    parser.add_argument(
        "--full-output",
        type=Path,
        default=None,
        help="Optional output path for the full raw cookie dump from agent-browser",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=Path("/home/workspace/Projects/Vamation/data/metadata/backups/cookies"),
        help="Archive directory for previous cookie files and metadata before replacement",
    )
    return parser.parse_args()


def run_agent_browser(cdp_port: int) -> dict:
    proc = subprocess.run(
        ["agent-browser", "--cdp", str(cdp_port), "cookies", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    if not payload.get("success"):
        raise RuntimeError(f"agent-browser cookie export failed: {payload}")
    return payload


def filter_patreon_cookies(raw_cookies: list[dict]) -> list[dict]:
    filtered: list[dict] = []
    for cookie in raw_cookies:
        domain = cookie.get("domain", "")
        if "patreon.com" not in domain:
            continue
        filtered.append(
            {
                "name": cookie["name"],
                "value": cookie["value"],
                "domain": domain,
            }
        )
    return filtered


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def archive_existing_file(path: Path, archive_dir: Path, timestamp: str) -> Path | None:
    if not path.exists():
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_name = f"{path.stem}_{timestamp}{path.suffix}"
    archived_path = archive_dir / archived_name
    shutil.move(str(path), str(archived_path))
    return archived_path


def main() -> int:
    args = parse_args()

    payload = run_agent_browser(args.cdp_port)
    raw_cookies = payload["data"]["cookies"]
    patreon_cookies = filter_patreon_cookies(raw_cookies)

    if not patreon_cookies:
        raise RuntimeError("No Patreon cookies found in the live browser session")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived_cookie_path = archive_existing_file(args.output, args.archive_dir, timestamp)
    archived_metadata_path = archive_existing_file(args.metadata_output, args.archive_dir, timestamp)

    cookie_names = [cookie["name"] for cookie in patreon_cookies]
    metadata = {
        "source": "agent-browser-cdp",
        "cdp_port": args.cdp_port,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "cookie_count": len(patreon_cookies),
        "cookie_names": cookie_names,
        "domains": sorted({cookie["domain"] for cookie in patreon_cookies}),
        "note": "Extracted from a live authenticated Patreon browser session. Browser-backed fetch is proven; raw requests.Session replay may still fail.",
        "archive_dir": str(args.archive_dir),
        "archived_previous_cookie_file": str(archived_cookie_path) if archived_cookie_path else None,
        "archived_previous_metadata_file": str(archived_metadata_path) if archived_metadata_path else None,
    }

    write_json(args.output, patreon_cookies)
    write_json(args.metadata_output, metadata)

    if args.full_output is not None:
        write_json(args.full_output, payload)

    if archived_cookie_path is not None:
        print(f"Archived previous cookie jar: {archived_cookie_path}")
    if archived_metadata_path is not None:
        print(f"Archived previous cookie metadata: {archived_metadata_path}")
    print(f"Wrote Patreon cookie jar: {args.output}")
    print(f"Wrote cookie metadata: {args.metadata_output}")
    if args.full_output is not None:
        print(f"Wrote full raw cookie dump: {args.full_output}")
    print("Cookie names:")
    for name in cookie_names:
        print(f"- {name}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or str(exc), file=sys.stderr)
        raise
