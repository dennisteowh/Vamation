#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ['VAMATION_DISABLE_BACKGROUND_INIT'] = '1'

from app.webapp.app import FileOperationsManager, metadata_manager  # noqa: E402

DEFAULT_MANIFEST = PROJECT_ROOT / 'data' / 'metadata' / 'backups' / 'pre_reset_recovery_manifest_20260530T135607Z.json'
DEFAULT_STATE = PROJECT_ROOT / 'data' / 'metadata' / 'backups' / 'recovery_progress.json'


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def build_initial_state(manifest_path: Path, limit: int) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    posts = manifest.get('posts_with_extracted_zip', [])[:limit]
    return {
        'created_at': now_iso(),
        'updated_at': now_iso(),
        'manifest_path': str(manifest_path),
        'mode': 'recovery-from-pre-reset-manifest',
        'batch_limit': limit,
        'summary': {
            'total_selected': len(posts),
            'queued': len(posts),
            'in_progress': 0,
            'completed': 0,
            'failed': 0,
        },
        'posts': [
            {
                'post_id': str(post['post_id']),
                'post_name': post.get('revised_post_name') or post.get('post_name', ''),
                'status': 'queued',
                'attempts': 0,
                'last_error': None,
                'started_at': None,
                'completed_at': None,
                'source_manifest_entry': post,
            }
            for post in posts
        ]
    }


def recompute_summary(state: dict[str, Any]) -> None:
    counts = {'queued': 0, 'in_progress': 0, 'completed': 0, 'failed': 0}
    for post in state.get('posts', []):
        status = post.get('status', 'queued')
        if status in counts:
            counts[status] += 1
    state['summary'] = {
        'total_selected': len(state.get('posts', [])),
        **counts,
    }
    state['updated_at'] = now_iso()


def ensure_state(state_path: Path, manifest_path: Path, limit: int) -> dict[str, Any]:
    if state_path.exists():
        return load_json(state_path)
    state = build_initial_state(manifest_path, limit)
    save_json(state_path, state)
    return state


def get_target_posts(state: dict[str, Any], only_status: set[str] | None = None) -> list[dict[str, Any]]:
    posts = state.get('posts', [])
    if not only_status:
        return posts
    return [post for post in posts if post.get('status') in only_status]


def run_recovery(state_path: Path, manifest_path: Path, limit: int, force_regenerate: bool = True) -> None:
    state = ensure_state(state_path, manifest_path, limit)
    targets = get_target_posts(state, {'queued', 'failed'})

    print(f"Recovery state: {state_path}")
    print(f"Manifest: {manifest_path}")
    print(f"Selected posts: {len(state.get('posts', []))}")
    print(f"Pending this run: {len(targets)}")

    for post_state in targets:
        post_id = str(post_state['post_id'])
        post_name = post_state.get('post_name', '')
        print(f"\n=== Recovering {post_id} :: {post_name} ===")

        post_state['status'] = 'in_progress'
        post_state['attempts'] = int(post_state.get('attempts', 0)) + 1
        post_state['started_at'] = now_iso()
        post_state['last_error'] = None
        recompute_summary(state)
        save_json(state_path, state)

        try:
            if force_regenerate:
                FileOperationsManager._clear_post_generated_outputs(post_id, clear_extracted=True)
            result = FileOperationsManager.extract_post_files(post_id)
            if result.get('success'):
                post_state['status'] = 'completed'
                post_state['completed_at'] = now_iso()
                post_state['last_error'] = None
                print(f"OK {post_id}")
            else:
                post_state['status'] = 'failed'
                post_state['completed_at'] = now_iso()
                post_state['last_error'] = result.get('error', 'Unknown failure')
                print(f"FAIL {post_id}: {post_state['last_error']}")
        except Exception as exc:
            post_state['status'] = 'failed'
            post_state['completed_at'] = now_iso()
            post_state['last_error'] = str(exc)
            print(f"FAIL {post_id}: {exc}")

        recompute_summary(state)
        save_json(state_path, state)

    print('\nDone.')
    print(json.dumps(state.get('summary', {}), indent=2))


def show_selection(manifest_path: Path, limit: int) -> None:
    manifest = load_json(manifest_path)
    posts = manifest.get('posts_with_extracted_zip', [])[:limit]
    print(json.dumps([
        {
            'post_id': str(post.get('post_id', '')),
            'post_name': post.get('revised_post_name') or post.get('post_name', ''),
            'zip_count': len(post.get('zip_files', [])),
        }
        for post in posts
    ], indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description='Recover previously extracted Vamation posts from the pre-reset manifest.')
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--limit', type=int, default=4, help='How many manifest posts to target. Default: 4')
    parser.add_argument('--run', action='store_true', help='Actually run the recovery. Without this flag, the script only prints the selected posts.')
    args = parser.parse_args()

    if not args.manifest.exists():
        print(f'Manifest not found: {args.manifest}', file=sys.stderr)
        return 1

    if not args.run:
        show_selection(args.manifest, args.limit)
        print(f"\nDry run only. State file will be created/updated when you run with --run: {args.state}")
        return 0

    run_recovery(args.state, args.manifest, args.limit)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
