#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('VAMATION_DISABLE_BACKGROUND_INIT', '1')

from app.webapp.app import Config, FileOperationsManager


def metadata_post_ids() -> list[str]:
    ids = []
    for metadata_path in sorted(Config.POST_PAGES_DIR.glob('*_metadata.json')):
        ids.append(metadata_path.stem.removesuffix('_metadata'))
    return ids


def regenerate_post(post_id: str) -> tuple[bool, str | None]:
    metadata_path = Config.POST_PAGES_DIR / f'{post_id}_metadata.json'
    if not metadata_path.exists():
        return False, 'missing metadata'
    result = FileOperationsManager._generate_post_html_only(post_id, {'post_id': post_id})
    if result.get('success'):
        return True, None
    return False, result.get('error') or 'unknown error'


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Regenerate Vamation post single/cascade HTML from existing per-post metadata.'
    )
    parser.add_argument('post_ids', nargs='*', help='Specific post IDs to regenerate. Defaults to all metadata-backed posts.')
    parser.add_argument('--limit', type=int, default=0, help='Optional cap when regenerating all posts.')
    args = parser.parse_args()

    targets = args.post_ids or metadata_post_ids()
    if args.limit > 0:
        targets = targets[:args.limit]

    print(f'Target posts: {len(targets)}')

    succeeded = 0
    failed = 0
    missing_single = 0
    missing_cascade = 0

    for post_id in targets:
        ok, error = regenerate_post(post_id)
        single_path = Config.POST_PAGES_DIR / f'{post_id}.html'
        cascade_path = Config.POST_PAGES_DIR / f'{post_id}_cascade.html'

        if not single_path.exists():
            missing_single += 1
        if not cascade_path.exists():
            missing_cascade += 1

        if ok:
            succeeded += 1
            print(f'OK {post_id}')
        else:
            failed += 1
            print(f'FAIL {post_id}: {error}')

    print('')
    print(f'succeeded = {succeeded}')
    print(f'failed = {failed}')
    print(f'missing_single = {missing_single}')
    print(f'missing_cascade = {missing_cascade}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
