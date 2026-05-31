#!/usr/bin/env python3
"""
Developer maintenance script: detect corruption in extracted post files and clean them.

Behavior:
- Reads metadata/posts_metadata.json.
- Optionally filters posts by Post Date range.
- Scans extracted folder for each relevant post.
- If any corrupted extracted image is found, deletes that post's extracted output and
  marks metadata extraction flags as not extracted.
- Regenerates metadata/posts_metadata.xlsx from JSON after updates.

This script is intended for developer operations, not end-user workflows.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import shutil
import tempfile
import time
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.path_config import EXTRACTED_DIR, LOGS_DIR, POST_PAGES_DIR, POSTS_METADATA_JSON, ensure_common_directories

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


ensure_common_directories()
METADATA_JSON = POSTS_METADATA_JSON

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


def safe_save_json(data: Dict[str, Any], filepath: Path, create_backup: bool = True) -> None:
    """Atomically save JSON with optional .backup copy."""
    filepath = Path(filepath)

    if create_backup and filepath.exists():
        backup_path = filepath.with_suffix(filepath.suffix + ".backup")
        shutil.copy2(filepath, backup_path)

    temp_fd, temp_path = tempfile.mkstemp(
        dir=filepath.parent,
        suffix=".tmp",
        prefix=f".{filepath.name}_",
    )

    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, filepath)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def parse_date(value: Optional[str]) -> Optional[datetime]:
    """Best-effort parse for metadata post_date and CLI date args."""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def in_date_range(
    post_date: Optional[datetime],
    start_date: Optional[datetime],
    end_date: Optional[datetime],
) -> bool:
    post_day = post_date.date() if post_date is not None else None
    start_day = start_date.date() if start_date is not None else None
    end_day = end_date.date() if end_date is not None else None

    if start_date is None and end_date is None:
        return True
    if post_day is None:
        return False
    if start_day is not None and post_day < start_day:
        return False
    if end_day is not None and post_day > end_day:
        return False
    return True


def is_image_corrupted(path: Path) -> Tuple[bool, Optional[str]]:
    """Validate image readability; returns (is_corrupted, reason)."""
    if not PIL_AVAILABLE:
        return False, None

    try:
        with Image.open(path) as img:
            img.verify()

        with Image.open(path) as img:
            img.load()

        return False, None
    except Exception as exc:  # pragma: no cover - runtime data dependent
        return True, str(exc)


def find_corrupted_items(extracted_post_dir: Path) -> List[Dict[str, str]]:
    """Scan extracted files and return corrupted image items."""
    corrupted: List[Dict[str, str]] = []

    if not extracted_post_dir.exists():
        return corrupted

    for file_path in extracted_post_dir.rglob("*"):
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        bad, reason = is_image_corrupted(file_path)
        if bad:
            corrupted.append(
                {
                    "file": str(file_path),
                    "reason": reason or "Unknown image decode error",
                }
            )

    return corrupted


def find_first_corrupted_item(extracted_post_dir: Path) -> Optional[Dict[str, str]]:
    """Return first corrupted image item found, or None if all images are readable."""
    if not extracted_post_dir.exists():
        return None

    for file_path in extracted_post_dir.rglob("*"):
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        bad, reason = is_image_corrupted(file_path)
        if bad:
            return {
                "file": str(file_path),
                "reason": reason or "Unknown image decode error",
            }

    return None


def list_image_files(extracted_post_dir: Path) -> List[Path]:
    """List image files under an extracted post directory."""
    if not extracted_post_dir.exists():
        return []

    files: List[Path] = []
    for file_path in extracted_post_dir.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in IMAGE_EXTENSIONS:
            files.append(file_path)
    return files


def post_is_marked_extracted(post: Dict[str, Any]) -> bool:
    zip_files = post.get("zip_files", [])
    return any(isinstance(z, dict) and z.get("extracted", False) for z in zip_files)


def unmark_post_extracted(post: Dict[str, Any]) -> None:
    for zip_info in post.get("zip_files", []):
        if isinstance(zip_info, dict):
            zip_info["extracted"] = False
            zip_info.pop("extraction_date", None)
    post.pop("html_files", None)


def _clear_windows_attributes(path: Path) -> None:
    """Best-effort clear read-only/hidden/system attributes under a path on Windows."""
    if os.name != "nt" or not path.exists():
        return

    try:
        subprocess.run(
            ["attrib", "-R", "-H", "-S", str(path)],
            capture_output=True,
            check=False,
            text=True,
        )
        # Use wildcard for descendants.
        subprocess.run(
            ["attrib", "-R", "-H", "-S", "/S", "/D", str(path / "*")],
            capture_output=True,
            check=False,
            text=True,
        )
    except Exception:
        # Attribute clearing is best-effort only.
        return


def _remove_remaining_entries(path: Path) -> None:
    """Best-effort bottom-up removal of remaining files/folders."""
    if not path.exists():
        return

    for root, dirs, files in os.walk(path, topdown=False):
        root_path = Path(root)

        for filename in files:
            file_path = root_path / filename
            try:
                os.chmod(file_path, 0o666)
            except Exception:
                pass
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                pass

        for dirname in dirs:
            dir_path = root_path / dirname
            try:
                os.chmod(dir_path, 0o777)
            except Exception:
                pass
            try:
                dir_path.rmdir()
            except Exception:
                pass

    try:
        os.chmod(path, 0o777)
    except Exception:
        pass
    try:
        path.rmdir()
    except Exception:
        pass


def _delete_tree_once(path: Path) -> bool:
    """Attempt one robust delete pass. Returns True if path is gone."""
    if not path.exists():
        return True

    try:
        shutil.rmtree(path)
    except Exception:
        pass

    if not path.exists():
        return True

    _clear_windows_attributes(path)
    _remove_remaining_entries(path)

    if not path.exists():
        return True

    try:
        shutil.rmtree(path)
    except Exception:
        pass

    return not path.exists()


def _delete_tree_with_retries(
    path: Path, attempts: int = 5, delay_seconds: float = 0.35
) -> Tuple[bool, bool, Optional[str]]:
    """Delete a directory tree with retries for transient Windows locking/race issues.

    Returns:
        success, relocated_to_quarantine, warning_message

    Notes:
        If the original path is successfully renamed to a quarantine path but quarantine
        deletion still fails, this returns success=True and relocated_to_quarantine=True.
        This allows metadata to proceed because the original extracted path is no longer live.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            if not path.exists():
                return True, False, None
            if _delete_tree_once(path):
                return True, False, None
            last_error = OSError(f"Delete pass did not remove path: {path}")
        except FileNotFoundError:
            return True, False, None
        except Exception as exc:  # pragma: no cover - environment dependent
            last_error = exc

        if attempt < attempts:
            time.sleep(delay_seconds)

    # Quarantine-rename fallback can bypass path-level contention.
    if path.exists():
        quarantine = path.with_name(f"{path.name}__delete_pending_{int(time.time())}")
        try:
            path.rename(quarantine)
            for attempt in range(1, attempts + 1):
                try:
                    if _delete_tree_once(quarantine):
                        return True, True, None
                    last_error = OSError(f"Delete pass did not remove quarantine path: {quarantine}")
                except Exception as exc:  # pragma: no cover - environment dependent
                    last_error = exc

                if attempt < attempts:
                    time.sleep(delay_seconds)

            # Original path has been relocated; treat as effective success.
            warning = (
                f"Original path renamed to quarantine but quarantine cleanup failed: {quarantine}. "
                f"Last error: {last_error}"
            )
            return True, True, warning
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        return False, False, str(last_error)

    return False, False, f"Unknown deletion failure for {path}"


def delete_extracted_outputs(post_id: str, dry_run: bool) -> Tuple[List[str], List[str], List[str], bool]:
    """Delete extracted folder and generated post files for a post ID.

    Returns:
        removed_paths, errors, warnings, success
    """
    removed: List[str] = []
    errors: List[str] = []
    warnings: List[str] = []

    extracted_post_dir = EXTRACTED_DIR / post_id
    generated_files = [
        POST_PAGES_DIR / f"{post_id}.html",
        POST_PAGES_DIR / f"{post_id}_cascade.html",
        POST_PAGES_DIR / f"{post_id}_metadata.json",
    ]

    if extracted_post_dir.exists():
        removed.append(str(extracted_post_dir))
        if not dry_run:
            success, relocated, warning = _delete_tree_with_retries(extracted_post_dir)
            if not success:
                errors.append(f"Failed to delete extracted dir {extracted_post_dir}: {warning}")
            else:
                if relocated:
                    warnings.append(
                        f"Extracted dir relocated to quarantine before cleanup: {extracted_post_dir}"
                    )
                if warning:
                    warnings.append(warning)

    for file_path in generated_files:
        if file_path.exists():
            removed.append(str(file_path))
            if not dry_run:
                try:
                    file_path.unlink()
                except Exception as exc:
                    errors.append(f"Failed to delete file {file_path}: {exc}")

    success = len(errors) == 0
    return removed, errors, warnings, success


def regenerate_excel_from_json() -> Tuple[bool, str]:
    """Regenerate Excel mirror using existing MetadataHandler."""
    try:
        from shared.metadata_handler import MetadataHandler

        handler = MetadataHandler()
        success = handler.json_to_excel(create_backup=True)
        if success:
            return True, "Excel regenerated successfully"
        return False, "MetadataHandler.json_to_excel returned failure"
    except Exception as exc:  # pragma: no cover - runtime environment dependent
        return False, str(exc)


def write_log(summary: Dict[str, Any], dry_run: bool) -> Optional[Path]:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        suffix = "dryrun" if dry_run else "live"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = LOGS_DIR / f"clean_corruption_{suffix}_{ts}.json"
        with open(log_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, ensure_ascii=False)
        return log_path
    except Exception:
        return None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect corrupted extracted items and reset extraction metadata for affected posts."
    )
    parser.add_argument(
        "--start-date",
        help="Inclusive start date for Post Date filter (e.g. 2025-11-01). Defaults to all.",
    )
    parser.add_argument(
        "--end-date",
        help="Inclusive end date for Post Date filter (e.g. 2025-11-30). Defaults to all.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report only; do not delete files or update metadata.",
    )
    parser.add_argument(
        "--fast-mode",
        action="store_true",
        help=(
            "Stop corruption scan at first bad image per post. "
            "Default behavior scans all images and reports all corrupted items."
        ),
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if not METADATA_JSON.exists():
        print(f"ERROR: Metadata JSON not found: {METADATA_JSON}")
        return 1

    start_date = parse_date(args.start_date) if args.start_date else None
    end_date = parse_date(args.end_date) if args.end_date else None

    if args.start_date and start_date is None:
        print(f"ERROR: Could not parse --start-date: {args.start_date}")
        return 1
    if args.end_date and end_date is None:
        print(f"ERROR: Could not parse --end-date: {args.end_date}")
        return 1

    can_scan_corruption = PIL_AVAILABLE
    if not can_scan_corruption:
        print("WARNING: Pillow is not available. Image decode corruption checks are disabled.")
        print("         Consistency cleanup can still run (missing/empty extracted outputs).")

    with open(METADATA_JSON, "r", encoding="utf-8") as handle:
        metadata = json.load(handle)

    posts = metadata.get("posts", [])

    scanned_posts = 0
    affected_posts = 0
    corrupted_items_total = 0
    consistency_issues_total = 0
    corruption_posts_total = 0
    metadata_changed = False
    deletion_failures_total = 0
    affected_details: List[Dict[str, Any]] = []

    for post in posts:
        post_id = str(post.get("post_id", "")).strip()
        if not post_id:
            continue

        post_date = parse_date(post.get("post_date"))
        if not in_date_range(post_date, start_date, end_date):
            continue

        # Only posts currently marked extracted are in scope for reset.
        if not post_is_marked_extracted(post):
            continue

        scanned_posts += 1
        extracted_post_dir = EXTRACTED_DIR / post_id
        corrupted_items: List[Dict[str, str]] = []
        cleanup_reasons: List[str] = []

        if not extracted_post_dir.exists():
            cleanup_reasons.append("missing_extracted_directory")
        else:
            image_files = list_image_files(extracted_post_dir)

            # Mark inconsistent extracted state if no extracted images exist.
            if not image_files:
                cleanup_reasons.append("no_extracted_images")

            if can_scan_corruption and image_files:
                if args.fast_mode:
                    first_corrupted = find_first_corrupted_item(extracted_post_dir)
                    if first_corrupted:
                        corrupted_items = [first_corrupted]
                else:
                    corrupted_items = find_corrupted_items(extracted_post_dir)

                if corrupted_items:
                    cleanup_reasons.append("corrupted_image_items")

        if not cleanup_reasons:
            continue

        affected_posts += 1
        if "corrupted_image_items" in cleanup_reasons:
            corruption_posts_total += 1
            if not args.fast_mode:
                corrupted_items_total += len(corrupted_items)
        consistency_issues_total += len([r for r in cleanup_reasons if r != "corrupted_image_items"])

        removed_paths, deletion_errors, deletion_warnings, delete_success = delete_extracted_outputs(
            post_id, dry_run=args.dry_run
        )
        if not args.dry_run and delete_success:
            unmark_post_extracted(post)
            metadata_changed = True
        elif not args.dry_run and not delete_success:
            deletion_failures_total += 1

        affected_details.append(
            {
                "post_id": post_id,
                "post_date": post.get("post_date"),
                "fast_mode": args.fast_mode,
                "cleanup_reasons": cleanup_reasons,
                "corrupted_items": corrupted_items,
                "removed_paths": removed_paths,
                "deletion_errors": deletion_errors,
                "deletion_warnings": deletion_warnings,
                "delete_success": delete_success,
            }
        )

        print(
            f"[AFFECTED] post_id={post_id} reasons={cleanup_reasons} "
            f"corrupted_items={len(corrupted_items)}"
        )
        if deletion_errors:
            for err in deletion_errors:
                print(f"  [DELETE-ERROR] {err}")
        if deletion_warnings:
            for warning in deletion_warnings:
                print(f"  [DELETE-WARN] {warning}")

    excel_result = {"attempted": False, "success": None, "message": ""}
    if metadata_changed and not args.dry_run:
        safe_save_json(metadata, METADATA_JSON, create_backup=True)
        excel_result["attempted"] = True
        success, message = regenerate_excel_from_json()
        excel_result["success"] = success
        excel_result["message"] = message

    summary = {
        "timestamp": datetime.now().isoformat(),
        "dry_run": args.dry_run,
        "fast_mode": args.fast_mode,
        "date_filter": {
            "start_date": args.start_date,
            "end_date": args.end_date,
        },
        "paths": {
            "metadata_json": str(METADATA_JSON),
            "extracted_dir": str(EXTRACTED_DIR),
            "post_pages_dir": str(POST_PAGES_DIR),
        },
        "stats": {
            "scanned_posts": scanned_posts,
            "affected_posts": affected_posts,
            "corruption_posts_total": corruption_posts_total,
            "corrupted_items_total": None if args.fast_mode else corrupted_items_total,
            "consistency_issues_total": consistency_issues_total,
            "deletion_failures_total": deletion_failures_total,
            "metadata_changed": metadata_changed,
        },
        "excel_sync": excel_result,
        "affected": affected_details,
    }

    log_path = write_log(summary, dry_run=args.dry_run)

    print("\n=== CLEAN CORRUPTION SUMMARY ===")
    print(f"Dry run: {args.dry_run}")
    print(f"Fast mode: {args.fast_mode}")
    print(f"Scanned posts: {scanned_posts}")
    print(f"Affected posts: {affected_posts}")
    print(f"Corruption posts total: {corruption_posts_total}")
    if args.fast_mode:
        print("Corrupted items total: skipped (fast mode)")
    else:
        print(f"Corrupted items total: {corrupted_items_total}")
    print(f"Consistency issues total: {consistency_issues_total}")
    print(f"Deletion failures total: {deletion_failures_total}")
    print(f"Metadata changed: {metadata_changed}")
    if excel_result["attempted"]:
        print(f"Excel sync success: {excel_result['success']}")
        print(f"Excel sync message: {excel_result['message']}")
    if log_path:
        print(f"Log written: {log_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
