#!/usr/bin/env python3
"""
VAMA Patreon Metadata Entry Deleter v1.0

Deletes specified metadata entries by post_id from both JSON and Excel files.
Updates summaries and statistics accordingly while preserving exact format structure.

Features:
- Delete multiple entries by post_id
- Updates both JSON and Excel metadata files
- Preserves all original format structures
- Updates summary statistics automatically
- Comprehensive logging of deletions

Author: AI Assistant
Date: 2025-11-19
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Set
import pytz

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.path_config import POSTS_METADATA_JSON, POSTS_METADATA_XLSX, ensure_common_directories

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("⚠️  pandas not available - Excel updates will be skipped")
    print("💡 Install with: pip install pandas openpyxl")

# SGT timezone
SGT = pytz.timezone('Asia/Singapore')


class MetadataEntryDeleter:
    """Deletes specified metadata entries by post_id."""
    
    def __init__(self):
        """Initialize the deleter."""
        ensure_common_directories()
        self.json_file = POSTS_METADATA_JSON
        self.excel_file = POSTS_METADATA_XLSX
        
        print(f"🗑️ VAMA Patreon Metadata Entry Deleter v1.0")
        print(f"=" * 60)
        
        if not self.json_file.parent.exists():
            raise Exception(f"Metadata directory not found: {self.json_file.parent}")
        if not self.json_file.exists():
            raise Exception(f"JSON metadata file not found: {self.json_file}")
        
        # Statistics
        self.total_requested = 0
        self.successfully_deleted = 0
        self.not_found = 0
        
        # Deletion log
        self.deletion_log = []
    
    def load_metadata(self) -> Dict:
        """Load current metadata."""
        print(f"\n📖 Loading metadata...")
        
        with open(self.json_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        posts = json_data.get('posts', [])
        print(f"📊 Loaded {len(posts)} posts from metadata")
        
        return json_data
    
    def delete_entries(self, json_data: Dict, post_ids_to_delete: List[str]) -> Dict:
        """Delete entries by post_id and return updated metadata."""
        print(f"\n🗑️ Processing deletions...")
        
        # Convert to set for faster lookup
        delete_set = set(post_ids_to_delete)
        self.total_requested = len(delete_set)
        
        # Track what we found
        found_posts = set()
        deleted_entries = []
        
        # Filter out posts to delete
        original_posts = json_data.get('posts', [])
        remaining_posts = []
        
        for post in original_posts:
            post_id = post.get('post_id', '')
            
            if post_id in delete_set:
                # This post should be deleted
                found_posts.add(post_id)
                deleted_entries.append({
                    'post_id': post_id,
                    'post_name': post.get('post_name', ''),
                    'post_type': post.get('post_type', ''),
                    'has_zip_files': post.get('has_zip_files', False),
                    'zip_files_count': len(post.get('zip_files', [])),
                    'profile_images_count': post.get('profile_images_count', 0)
                })
                print(f"   ✅ Marked for deletion: {post_id} - {post.get('post_name', 'Unknown')}")
            else:
                # Keep this post
                remaining_posts.append(post)
        
        # Track not found
        not_found_ids = delete_set - found_posts
        self.successfully_deleted = len(found_posts)
        self.not_found = len(not_found_ids)
        
        # Log results
        for post_id in not_found_ids:
            print(f"   ❌ Not found: {post_id}")
            self.deletion_log.append({
                'post_id': post_id,
                'status': 'not_found',
                'timestamp': datetime.now(SGT).isoformat()
            })
        
        for entry in deleted_entries:
            self.deletion_log.append({
                **entry,
                'status': 'deleted',
                'timestamp': datetime.now(SGT).isoformat()
            })
        
        # Update posts list
        json_data['posts'] = remaining_posts
        
        print(f"\n📊 Deletion Summary:")
        print(f"   • Requested deletions: {self.total_requested}")
        print(f"   • Successfully deleted: {self.successfully_deleted}")
        print(f"   • Not found: {self.not_found}")
        print(f"   • Remaining posts: {len(remaining_posts)}")
        
        return json_data
    
    def update_summary(self, json_data: Dict) -> None:
        """Update summary statistics while preserving existing structure."""
        print(f"\n📊 Updating summary statistics...")
        
        # Preserve existing summary structure
        if 'summary' not in json_data:
            json_data['summary'] = {}
        
        summary = json_data['summary']
        posts = json_data['posts']
        
        # Update core tracking fields
        summary['last_update'] = datetime.now(SGT).isoformat()
        summary['status'] = 'UPDATED'
        summary['total_posts'] = len(posts)
        
        # Recalculate all statistics to ensure accuracy
        summary['posts_with_images'] = len([p for p in posts if p.get('profile_images_count', 0) > 0])
        summary['total_images_downloaded'] = sum([p.get('profile_images_count', 0) for p in posts])
        summary['posts_with_zip_files'] = len([p for p in posts if p.get('has_zip_files', False)])
        
        # Recalculate date range from remaining posts
        valid_dates = [p['post_date'] for p in posts if p.get('post_date')]
        if valid_dates:
            if 'date_range' not in summary:
                summary['date_range'] = {}
            summary['date_range']['earliest'] = min(valid_dates)
            summary['date_range']['latest'] = max(valid_dates)
        else:
            # No posts with dates remain
            if 'date_range' in summary:
                summary['date_range']['earliest'] = ''
                summary['date_range']['latest'] = ''
        
        print(f"✅ Summary statistics updated")
    
    def save_updated_metadata(self, json_data: Dict) -> bool:
        """Save updated JSON and regenerate Excel file."""
        print(f"\n💾 Saving updated metadata...")
        
        try:
            # Save JSON file
            with open(self.json_file, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)
            print(f"✅ JSON file updated: {self.json_file}")
            
            # Regenerate Excel file if pandas is available
            if PANDAS_AVAILABLE:
                self._regenerate_excel_file(json_data['posts'], json_data['summary'])
                print(f"✅ Excel file regenerated: {self.excel_file}")
            else:
                print(f"⚠️  Excel file not updated (pandas not available)")
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to save metadata: {e}")
            return False
    
    def _regenerate_excel_file(self, posts: List[Dict], summary_data: Dict = None):
        """Regenerate Excel file with exact format preservation."""
        excel_data = []
        
        for post in posts:
            row = {
                'Post ID': post.get('post_id', ''),
                'Post Name': post.get('post_name', ''),
                'Revised Post Name': post.get('revised_post_name', ''),
                'Display': post.get('display', True),
                'Post Date': post.get('post_date', None),
                'Scraped Date': post.get('scraped_date', ''),
                'Description': post.get('description', ''),
                'Patreon URL': post.get('patreon_url', ''),
                'Post Type': post.get('post_type', ''),
                'Has ZIP Files': post.get('has_zip_files', False),
                'Images Count': post.get('profile_images_count', 0)
            }
            
            # Add ZIP file information
            if post.get('zip_files'):
                zip_file = post['zip_files'][0]
                row.update({
                    'ZIP Filename': zip_file.get('filename', ''),
                    'ZIP Size (MB)': zip_file.get('size_mb', ''),
                    'ZIP Size (Bytes)': zip_file.get('size_bytes', ''),
                    'ZIP Media ID': zip_file.get('media_id', ''),
                    'ZIP Downloaded': zip_file.get('downloaded', False),
                    'ZIP Extracted': zip_file.get('extracted', False),
                    'ZIP Download Date': zip_file.get('download_date', ''),
                    'ZIP Local Filename': zip_file.get('local_filename', ''),
                    'Total ZIP Files': len(post['zip_files']),
                    'Other ZIP Files': '; '.join([z.get('filename', '') for z in post['zip_files'][1:]]) if len(post['zip_files']) > 1 else ''
                })
            else:
                row.update({
                    'ZIP Filename': '', 'ZIP Size (MB)': '', 'ZIP Size (Bytes)': '', 'ZIP Media ID': '',
                    'ZIP Downloaded': False, 'ZIP Extracted': False, 'ZIP Download Date': '',
                    'ZIP Local Filename': '', 'Total ZIP Files': 0, 'Other ZIP Files': ''
                })
            
            # Add image information
            if post.get('profile_images'):
                img = post['profile_images'][0]
                row.update({
                    'Image URL': img.get('url', ''),
                    'Image Filename': img.get('filename', ''),
                    'Image Local Path': img.get('local_path', ''),
                    'Image Type': img.get('type', '')
                })
            else:
                row.update({'Image URL': '', 'Image Filename': '', 'Image Local Path': '', 'Image Type': ''})
            
            excel_data.append(row)
        
        # Create DataFrame and save to Excel
        df = pd.DataFrame(excel_data)
        
        # Sort DataFrame by Post Date (descending, NaT at bottom) then Post Name (ascending)
        df['_sort_date'] = pd.to_datetime(df['Post Date'], errors='coerce')
        df = df.sort_values(
            ['_sort_date', 'Post Name'], 
            ascending=[False, True], 
            na_position='last'
        )
        df = df.drop('_sort_date', axis=1)
        
        with pd.ExcelWriter(self.excel_file, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Posts', index=False)
            
            # Freeze the top row for easy viewing
            worksheet = writer.sheets['Posts']
            worksheet.freeze_panes = 'A2'
            
            # Summary sheet - exact format match
            if summary_data is None:
                summary_data = {
                    'total_posts': len(posts),
                    'extraction_date': datetime.now(SGT).isoformat()
                }
            
            summary_df = pd.DataFrame([
                ['Last Update', summary_data.get('last_update', '')],
                ['Extraction Date', summary_data.get('extraction_date', '')],
                ['Total Posts', summary_data.get('total_posts', len(posts))],
                ['Posts with Images', len([p for p in posts if p.get('profile_images_count', 0) > 0])],
                ['Total Images Downloaded', sum([p.get('profile_images_count', 0) for p in posts])],
                ['Posts with ZIP Files', len([p for p in posts if p.get('has_zip_files')])],
                ['ZIP Files Downloaded', len([p for p in posts for z in p.get('zip_files', []) if z.get('downloaded')])],
                ['Earliest Post Date', summary_data.get('date_range', {}).get('earliest', '')],
                ['Latest Post Date', summary_data.get('date_range', {}).get('latest', '')]
            ], columns=['Metric', 'Value'])
            
            summary_df.to_excel(writer, sheet_name='Summary', index=False)
    
    def save_deletion_log(self) -> None:
        """Save deletion log for review."""
        if not self.deletion_log:
            return
        
        log_filename = f"deletion_log_{datetime.now(SGT).strftime('%Y%m%d_%H%M%S')}.json"
        log_path = Path(__file__).parent / log_filename
        
        log_data = {
            'timestamp': datetime.now(SGT).isoformat(),
            'statistics': {
                'total_requested': self.total_requested,
                'successfully_deleted': self.successfully_deleted,
                'not_found': self.not_found
            },
            'deletions': self.deletion_log
        }
        
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
            print(f"\n💾 Deletion log saved: {log_path}")
        except Exception as e:
            print(f"\n⚠️  Failed to save deletion log: {e}")
    
    def get_post_ids_input(self) -> List[str]:
        """Get post IDs from user input."""
        print(f"\n📝 Enter post IDs to delete:")
        print(f"   • You can enter multiple IDs separated by commas, spaces, or newlines")
        print(f"   • Press Enter twice when done")
        print(f"   • Example: 12345, 67890, 13579")
        
        input_lines = []
        print(f"\nPost IDs:")
        
        while True:
            try:
                line = input().strip()
                if line == "":
                    if input_lines:  # Empty line and we have some input
                        break
                    else:  # First line is empty
                        continue
                else:
                    input_lines.append(line)
            except KeyboardInterrupt:
                print(f"\n⏸️  Operation cancelled by user")
                return []
        
        # Parse all input
        all_input = ' '.join(input_lines)
        
        # Split by comma, space, or newline and clean up
        post_ids = []
        for chunk in all_input.replace(',', ' ').split():
            chunk = chunk.strip()
            if chunk:
                post_ids.append(chunk)
        
        # Remove duplicates while preserving order
        unique_post_ids = []
        seen = set()
        for post_id in post_ids:
            if post_id not in seen:
                unique_post_ids.append(post_id)
                seen.add(post_id)
        
        print(f"\n📋 Parsed {len(unique_post_ids)} unique post IDs:")
        for i, post_id in enumerate(unique_post_ids, 1):
            print(f"   {i}. {post_id}")
        
        return unique_post_ids
    
    def confirm_deletions(self, post_ids: List[str], json_data: Dict) -> bool:
        """Confirm deletions with user."""
        if not post_ids:
            return False
        
        print(f"\n🔍 Checking which posts exist...")
        
        existing_posts = {post.get('post_id'): post for post in json_data.get('posts', [])}
        found_posts = []
        not_found_posts = []
        
        for post_id in post_ids:
            if post_id in existing_posts:
                post = existing_posts[post_id]
                found_posts.append({
                    'post_id': post_id,
                    'post_name': post.get('post_name', 'Unknown'),
                    'post_type': post.get('post_type', ''),
                    'has_zip_files': post.get('has_zip_files', False)
                })
            else:
                not_found_posts.append(post_id)
        
        print(f"\n📊 Deletion Preview:")
        print(f"   • Posts found and will be deleted: {len(found_posts)}")
        print(f"   • Posts not found (will be skipped): {len(not_found_posts)}")
        
        if found_posts:
            print(f"\n✅ Posts to be DELETED:")
            for post in found_posts:
                zip_info = " (with ZIP files)" if post['has_zip_files'] else ""
                type_info = f" [{post['post_type']}]" if post['post_type'] else ""
                print(f"   • {post['post_id']} - {post['post_name']}{type_info}{zip_info}")
        
        if not_found_posts:
            print(f"\n❌ Posts NOT FOUND (will be skipped):")
            for post_id in not_found_posts:
                print(f"   • {post_id}")
        
        if not found_posts:
            print(f"\n⚠️  No posts found to delete!")
            return False
        
        print(f"\n⚠️  WARNING: This will permanently delete {len(found_posts)} metadata entries!")
        print(f"⚠️  This action cannot be undone!")
        
        while True:
            confirm = input(f"\nProceed with deletion? (yes/no): ").strip().lower()
            if confirm in ['yes', 'y']:
                return True
            elif confirm in ['no', 'n']:
                print(f"Deletion cancelled.")
                return False
            else:
                print(f"Please enter 'yes' or 'no'")
    
    def run(self) -> bool:
        """Run the deletion process."""
        try:
            # Load metadata
            json_data = self.load_metadata()
            
            # Get post IDs to delete
            post_ids = self.get_post_ids_input()
            if not post_ids:
                print("No post IDs provided.")
                return False
            
            # Confirm deletions
            if not self.confirm_deletions(post_ids, json_data):
                return False
            
            # Perform deletions
            updated_json_data = self.delete_entries(json_data, post_ids)
            
            # Update summary statistics
            self.update_summary(updated_json_data)
            
            # Save updated metadata
            if self.save_updated_metadata(updated_json_data):
                print(f"✅ Metadata files updated successfully")
            else:
                print(f"❌ Failed to save updated metadata")
                return False
            
            # Save deletion log
            self.save_deletion_log()
            
            print(f"\n🎉 Deletion completed!")
            print(f"   • {self.successfully_deleted} entries deleted")
            print(f"   • {self.not_found} entries not found")
            print(f"📁 Updated files:")
            print(f"   • {self.json_file}")
            if PANDAS_AVAILABLE:
                print(f"   • {self.excel_file}")
            
            return True
            
        except Exception as e:
            print(f"❌ Error during deletion: {e}")
            import traceback
            traceback.print_exc()
            return False


def main():
    """Main entry point."""
    try:
        deleter = MetadataEntryDeleter()
        deleter.run()
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()
