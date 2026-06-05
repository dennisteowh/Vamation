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

os.environ.setdefault('VAMATION_DISABLE_BACKGROUND_INIT', '1')

from app.webapp.app import FileOperationsManager

DEFAULT_MANIFEST = PROJECT_ROOT / 'data' / 'metadata' / 'backups' / 'pre_reset_recovery_manifest_20260530T135607Z.json'
DEFAULT_STATE = PROJECT_ROOT / 'data' / 'metadata' / 'backups' / 'recovery_progress.json'


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def manifest_posts(manifest_path: Path) -> list[dict[str, Any]]:
    manifest = load_json(manifest_path)
    posts = manifest.get('posts_with_extracted_zip', [])
    normalized = []
    for post in posts:
        normalized.append({
            'post_id': str(post['post_id']),
            'post_name': post.get('revised_post_name') or post.get('post_name', ''),
            'source_manifest_entry': post,
        })
    return normalized


def build_initial_state(manifest_path: Path) -> dict[str, Any]:
    posts = manifest_posts(manifest_path)
    return {
        'created_at': now_iso(),
        'updated_at': now_iso(),
        'manifest_path': str(manifest_path),
        'mode': 'recovery-from-pre-reset-manifest',
        'batch_limit': 10,
        'summary': {
            'total_selected': len(posts),
            'queued': len(posts),
            'in_progress': 0,
            'completed': 0,
            'failed': 0,
        },
        'posts': [
            {
                'post_id': post['post_id'],
                'post_name': post['post_name'],
                'status': 'queued',
                'attempts': 0,
                'last_error': None,
                'started_at': None,
                'completed_at': None,
                'source_manifest_entry': post['source_manifest_entry'],
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


def ensure_state(state_path: Path, manifest_path: Path) -> dict[str, Any]:
    if state_path.exists():
        state = load_json(state_path)
        if 'posts' in state and isinstance(state['posts'], list):
            recompute_summary(state)
            return state
    state = build_initial_state(manifest_path)
    save_json(state_path, state)
    return state


def next_batch(state: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    candidates = [p for p in state.get('posts', []) if p.get('status') in {'queued', 'failed'}]
    return candidates[:limit]


def run_recovery(state_path: Path, manifest_path: Path, limit: int, force_regenerate: bool = True) -> None:
    state = ensure_state(state_path, manifest_path)
    state['batch_limit'] = limit
    targets = next_batch(state, limit)

    print(f'Recovery state: {state_path}')
    print(f'Manifest: {manifest_path}')
    print(f'Total tracked posts: {len(state.get("posts", []))}')
    print(f'Processing this run: {len(targets)}')

    for post_state in targets:
        post_id = str(post_state['post_id'])
        post_name = post_state.get('post_name', '')
        print(f'\n=== Recovering {post_id} :: {post_name} ===')

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
                print(f'OK {post_id}')
            else:
                post_state['status'] = 'failed'
                post_state['completed_at'] = now_iso()
                post_state['last_error'] = result.get('error', 'Unknown failure')
                print(f'FAIL {post_id}: {post_state["last_error"]}')
        except Exception as exc:
            post_state['status'] = 'failed'
            post_state['completed_at'] = now_iso()
            post_state['last_error'] = str(exc)
            print(f'FAIL {post_id}: {exc}')

        recompute_summary(state)
        save_json(state_path, state)

    print('\nDone.')
    print(json.dumps(state.get('summary', {}), indent=2))


def show_selection(manifest_path: Path, state_path: Path, limit: int) -> None:
    state = ensure_state(state_path, manifest_path)
    posts = next_batch(state, limit)
    print(json.dumps([
        {
            'post_id': p['post_id'],
            'post_name': p.get('post_name', ''),
            'status': p.get('status'),
            'attempts': p.get('attempts', 0),
        }
        for p in posts
    ], ensure_ascii=False, indent=2))
    print(f'State file: {state_path}')


def main() -> int:
    parser = argparse.ArgumentParser(description='Recover previously extracted Vamation posts in incremental batches.')
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()

    if not args.run:
        show_selection(args.manifest, args.state, args.limit)
        print(f'Run with --run to recover the next {args.limit} queued/failed posts.')
        return 0

    run_recovery(args.state, args.manifest, args.limit)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
