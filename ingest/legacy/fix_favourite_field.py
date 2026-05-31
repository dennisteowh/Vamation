#!/usr/bin/env python3
"""
Temporary script to add 'favourite' field to all posts in metadata JSON.
This ensures JSON and Excel are consistent.

Run this once to update existing metadata, then delete this file.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.path_config import POSTS_METADATA_JSON

def fix_favourite_field():
    """Add favourite: false to all posts that don't have it."""
    
    # Path to metadata
    metadata_path = POSTS_METADATA_JSON
    
    if not metadata_path.exists():
        print(f"❌ Metadata file not found: {metadata_path}")
        return
    
    # Load metadata
    print(f"📖 Loading metadata from: {metadata_path}")
    with open(metadata_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    posts = data.get('posts', [])
    print(f"📊 Found {len(posts)} posts")
    
    # Count posts missing favourite field
    missing_count = sum(1 for post in posts if 'favourite' not in post)
    print(f"🔍 Posts missing 'favourite' field: {missing_count}")
    
    if missing_count == 0:
        print("✅ All posts already have the 'favourite' field - nothing to do!")
        return
    
    # Add favourite field to posts that don't have it
    updated_count = 0
    for post in posts:
        if 'favourite' not in post:
            post['favourite'] = False
            updated_count += 1
    
    # Create backup
    backup_path = metadata_path.with_suffix('.json.backup-before-favourite-fix')
    print(f"💾 Creating backup: {backup_path}")
    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    # Save updated metadata
    print(f"💾 Saving updated metadata...")
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Successfully updated {updated_count} posts")
    print(f"📊 All {len(posts)} posts now have 'favourite' field")
    print(f"\n🎉 JSON and Excel metadata are now consistent!")
    print(f"\n💡 You can now delete this script: fix_favourite_field.py")

if __name__ == "__main__":
    print("🔧 Favourite Field Fixer")
    print("=" * 60)
    fix_favourite_field()
