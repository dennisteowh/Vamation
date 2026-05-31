#!/usr/bin/env python3
"""
VAMA Metadata Handler - Bidirectional JSON <-> Excel Sync
Handles synchronization between JSON and Excel metadata files.

Features:
- JSON → Excel: Convert JSON metadata to Excel (JSON is source of truth)
- Excel → JSON: Update JSON from Excel changes (for manual editing convenience)
- Forward-compatible with new fields
- Preserves exact format structure
- Automatic field detection and type inference

Rules for Excel → JSON conversion:
1. Nested structures (zip_files, profile_images) are stored as text in Excel:
   - Multiple items separated by " ||| " delimiter
   - Individual fields within items separated by " | " delimiter
   - Example: "file1.zip | 100MB | true ||| file2.zip | 50MB | false"
   
2. Boolean fields: TRUE/FALSE in Excel → true/false in JSON
3. Date fields: ISO format strings preserved
4. Custom fields: Any new columns in Excel become top-level JSON fields
5. New rows in Excel create new post entries with template structure

Author: AI Assistant
Date: 2025-11-20
"""

import json
import os
import sys
import shutil
import tempfile
import pytz
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import re

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.path_config import POSTS_METADATA_JSON, POSTS_METADATA_XLSX, POST_PAGES_DIR, ensure_common_directories

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("⚠️  pandas not available - Excel operations will fail")
    print("💡 Install with: pip install pandas openpyxl")
    sys.exit(1)


# ============================================================================
# ATOMIC FILE WRITE HELPERS
# ============================================================================

def safe_save_json(data: dict, filepath: Path, create_backup: bool = True) -> None:
    """Atomically save JSON data to file with optional backup."""
    filepath = Path(filepath)
    
    if create_backup and filepath.exists():
        backup_path = filepath.with_suffix(filepath.suffix + '.backup')
        shutil.copy2(filepath, backup_path)
    
    temp_fd, temp_path = tempfile.mkstemp(
        dir=filepath.parent,
        suffix='.tmp',
        prefix=f'.{filepath.name}_'
    )
    
    try:
        with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        os.replace(temp_path, filepath)
        
    except Exception as e:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise e


def safe_save_excel(df: pd.DataFrame, filepath: Path, sheet_name: str = 'Posts',
                   summary_df: Optional[pd.DataFrame] = None, create_backup: bool = True) -> None:
    """Atomically save Excel file with optional backup."""
    filepath = Path(filepath)
    
    if create_backup and filepath.exists():
        backup_path = filepath.with_suffix(filepath.suffix + '.backup')
        shutil.copy2(filepath, backup_path)
    
    temp_fd, temp_path = tempfile.mkstemp(
        dir=filepath.parent,
        suffix='.tmp.xlsx',
        prefix=f'.{filepath.stem}_'
    )
    os.close(temp_fd)
    
    try:
        with pd.ExcelWriter(temp_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes = 'A2'
            
            if summary_df is not None:
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
        
        os.replace(temp_path, filepath)
        
    except Exception as e:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise e


# Configuration
SGT = pytz.timezone('Asia/Singapore')


class MetadataHandler:
    """Handles bidirectional synchronization between JSON and Excel metadata."""
    
    EXCEL_CELL_LIMIT = 32767
    
    # Core fields that map directly between JSON and Excel
    CORE_FIELDS = {
        'post_id': 'Post ID',
        'post_name': 'Post Name',
        'revised_post_name': 'Revised Post Name',
        'display': 'Display',
        'favourite': 'Favourite',
        'post_date': 'Post Date',
        'scraped_date': 'Scraped Date',
        'description': 'Description',
        'patreon_url': 'Patreon URL',
        'post_type': 'Post Type',
        'has_zip_files': 'Has ZIP Files',
        'profile_images_count': 'Images Count'
    }
    
    # ZIP file fields (first item only for Excel)
    ZIP_FIELDS = {
        'filename': 'ZIP Filename',
        'size_mb': 'ZIP Size (MB)',
        'size_bytes': 'ZIP Size (Bytes)',
        'media_id': 'ZIP Media ID',
        'downloaded': 'ZIP Downloaded',
        'extracted': 'ZIP Extracted',
        'download_date': 'ZIP Download Date',
        'local_filename': 'ZIP Local Filename'
    }
    
    # Image fields (first item only for Excel)
    IMAGE_FIELDS = {
        'url': 'Image URL',
        'filename': 'Image Filename',
        'local_path': 'Image Local Path',
        'type': 'Image Type'
    }
    
    def __init__(self):
        """Initialize the metadata handler."""
        ensure_common_directories()
        self.json_file = POSTS_METADATA_JSON
        self.excel_file = POSTS_METADATA_XLSX
        
        print("🔄 VAMA Metadata Handler - Bidirectional Sync")
        print("=" * 60)
        
        if not self.json_file.parent.exists():
            raise Exception(f"Metadata directory not found: {self.json_file.parent}")

    def _excel_safe_value(self, field_name: str, value: Any) -> Any:
        """Convert a JSON value into an Excel-safe scalar or short summary string."""
        if value is None:
            return ''
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return value if len(value) <= self.EXCEL_CELL_LIMIT else f"[omitted: {field_name} text too large for Excel; see JSON source]"

        if field_name == 'cascade_metadata' and isinstance(value, dict):
            images = value.get('images', []) if isinstance(value.get('images'), list) else []
            sort_mode = value.get('sort_mode', '')
            return f"cascade metadata: {len(images)} images" + (f", sort={sort_mode}" if sort_mode else '')

        if isinstance(value, (list, dict)):
            try:
                serialized = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            except Exception:
                serialized = str(value)
            if len(serialized) <= self.EXCEL_CELL_LIMIT:
                return serialized
            if isinstance(value, list):
                return f"[omitted: {field_name} list with {len(value)} items too large for Excel; see JSON source]"
            if isinstance(value, dict):
                return f"[omitted: {field_name} object with {len(value)} keys too large for Excel; see JSON source]"
            return f"[omitted: {field_name} too large for Excel; see JSON source]"

        text = str(value)
        return text if len(text) <= self.EXCEL_CELL_LIMIT else f"[omitted: {field_name} too large for Excel; see JSON source]"
    
    # ========================================================================
    # FUNCTION 1: JSON → EXCEL (JSON is source of truth)
    # ========================================================================
    
    def json_to_excel(self, create_backup: bool = True) -> bool:
        """
        Convert JSON metadata to Excel format.
        JSON is the source of truth - Excel is generated to mirror it.
        
        Args:
            create_backup: Create backup of existing Excel file
            
        Returns:
            True if successful, False otherwise
        """
        print("\n📄 JSON → Excel: Converting JSON to Excel...")
        
        if not self.json_file.exists():
            print(f"❌ JSON file not found: {self.json_file}")
            return False
        
        try:
            # Load JSON
            with open(self.json_file, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            posts = json_data.get('posts', [])
            summary = json_data.get('summary', {})
            
            print(f"📊 Loaded {len(posts)} posts from JSON")
            
            # Detect all fields present in JSON (for forward compatibility)
            all_fields = set()
            for post in posts:
                all_fields.update(post.keys())
            
            # Remove nested structures from field set
            all_fields.discard('zip_files')
            all_fields.discard('profile_images')
            all_fields.discard('html_files')
            all_fields.discard('images')
            all_fields.discard('total_images')
            all_fields.discard('visible_images')
            
            print(f"🔍 Detected {len(all_fields)} unique fields in JSON")
            
            # Generate Excel data
            excel_data = []
            for post in posts:
                row = {}
                
                # Add core fields
                for json_field, excel_field in self.CORE_FIELDS.items():
                    row[excel_field] = post.get(json_field, '')
                    if isinstance(row[excel_field], bool):
                        row[excel_field] = row[excel_field]  # Keep as boolean
                
                # Add ZIP file information (first item + summary)
                if post.get('zip_files'):
                    zip_file = post['zip_files'][0]
                    for json_field, excel_field in self.ZIP_FIELDS.items():
                        value = zip_file.get(json_field, '')
                        if isinstance(value, bool):
                            row[excel_field] = value
                        else:
                            row[excel_field] = value
                    
                    # Add summary fields
                    row['Total ZIP Files'] = len(post['zip_files'])
                    other_zips = [z.get('filename', '') for z in post['zip_files'][1:]]
                    row['Other ZIP Files'] = '; '.join(other_zips) if other_zips else ''
                else:
                    for excel_field in self.ZIP_FIELDS.values():
                        row[excel_field] = '' if 'Downloaded' not in excel_field and 'Extracted' not in excel_field else False
                    row['Total ZIP Files'] = 0
                    row['Other ZIP Files'] = ''
                
                # Add image information (first item only)
                if post.get('profile_images'):
                    img = post['profile_images'][0]
                    for json_field, excel_field in self.IMAGE_FIELDS.items():
                        row[excel_field] = img.get(json_field, '')
                else:
                    for excel_field in self.IMAGE_FIELDS.values():
                        row[excel_field] = ''
                
                # Add any custom fields not in core mappings
                custom_fields = all_fields - set(self.CORE_FIELDS.keys())
                for field_name in sorted(custom_fields):
                    excel_field_name = field_name.replace('_', ' ').title()
                    value = post.get(field_name)
                    
                    # Handle different types
                    if isinstance(value, bool):
                        row[excel_field_name] = value
                    elif value is None:
                        row[excel_field_name] = ''
                    else:
                        row[excel_field_name] = self._excel_safe_value(field_name, value)

                # Add lightweight summaries for nested/generated structures
                single_path = POST_PAGES_DIR / f"{post['post_id']}.html"
                cascade_path = POST_PAGES_DIR / f"{post['post_id']}_cascade.html"
                metadata_path = POST_PAGES_DIR / f"{post['post_id']}_metadata.json"
                row['Generated HTML'] = bool(single_path.exists() or cascade_path.exists())
                row['Single View Path'] = f"/posts/{post['post_id']}.html" if single_path.exists() else ''
                row['Cascade View Path'] = f"/posts/{post['post_id']}_cascade.html" if cascade_path.exists() else ''
                row['Post Metadata Path'] = f"/posts/{post['post_id']}_metadata.json" if metadata_path.exists() else ''

                cascade_metadata = post.get('cascade_metadata') or {}
                cascade_images = cascade_metadata.get('images', []) if isinstance(cascade_metadata, dict) else []
                row['Cascade Metadata Present'] = bool(cascade_metadata)
                row['Cascade Image Count'] = len(cascade_images)
                row['Cascade Sort Mode'] = cascade_metadata.get('sort_mode', '') if isinstance(cascade_metadata, dict) else ''
                row['Cascade Last Updated'] = cascade_metadata.get('last_updated', '') if isinstance(cascade_metadata, dict) else ''
                
                excel_data.append(row)
            
            # Create DataFrame
            df = pd.DataFrame(excel_data)
            
            # Sort by Post Date (descending, NaT at bottom) then Post Name (ascending)
            df['_sort_date'] = pd.to_datetime(df['Post Date'], errors='coerce')
            df = df.sort_values(
                ['_sort_date', 'Post Name'],
                ascending=[False, True],
                na_position='last'
            )
            df = df.drop('_sort_date', axis=1)
            
            # Create summary DataFrame
            summary_df = pd.DataFrame([
                ['Extraction Date', summary.get('extraction_date', '')],
                ['Status', summary.get('status', '')],
                ['Total Posts', summary.get('total_posts', 0)],
                ['Posts with Images', summary.get('posts_with_images', 0)],
                ['Total Images Downloaded', summary.get('total_images_downloaded', 0)],
                ['Posts with ZIP Files', summary.get('posts_with_zip_files', 0)],
                ['Earliest Post Date', summary.get('date_range', {}).get('earliest', '')],
                ['Latest Post Date', summary.get('date_range', {}).get('latest', '')],
                ['Last Update', summary.get('last_update', '')]
            ], columns=['Metric', 'Value'])
            
            # Save Excel file
            safe_save_excel(df, self.excel_file, sheet_name='Posts',
                          summary_df=summary_df, create_backup=create_backup)
            
            print(f"✅ Excel file created: {self.excel_file}")
            print(f"   📊 {len(excel_data)} posts written")
            print(f"   📋 {len(df.columns)} columns created")
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to convert JSON to Excel: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # ========================================================================
    # FUNCTION 2: EXCEL → JSON (Excel changes update JSON)
    # ========================================================================
    
    def excel_to_json(self, create_backup: bool = True) -> bool:
        """
        Update JSON metadata from Excel changes.
        Excel modifications are applied to JSON (useful for manual editing).
        
        Args:
            create_backup: Create backup of existing JSON file
            
        Returns:
            True if successful, False otherwise
        """
        print("\n📊 Excel → JSON: Updating JSON from Excel...")
        
        if not self.excel_file.exists():
            print(f"❌ Excel file not found: {self.excel_file}")
            return False
        
        try:
            # Load Excel
            df = pd.read_excel(self.excel_file, sheet_name='Posts')
            print(f"📊 Loaded {len(df)} rows from Excel")
            
            # Load existing JSON to get template and preserve structure
            json_template = self._load_json_template()
            
            # Load existing JSON data
            if self.json_file.exists():
                with open(self.json_file, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)
                existing_posts = {p.get('post_id'): p for p in json_data.get('posts', [])}
                summary = json_data.get('summary', {})
            else:
                existing_posts = {}
                summary = {}
            
            print(f"📖 Loaded {len(existing_posts)} existing posts from JSON")
            
            # Convert Excel rows to JSON posts
            posts = []
            new_posts_count = 0
            updated_posts_count = 0
            
            for idx, row in df.iterrows():
                post_id = str(row.get('Post ID', '')).strip()
                
                if not post_id:
                    print(f"⚠️  Row {idx + 2}: Skipping row with empty Post ID")
                    continue
                
                # Check if this is a new post or update
                if post_id in existing_posts:
                    # Update existing post
                    post = existing_posts[post_id].copy()
                    updated_posts_count += 1
                else:
                    # Create new post from template
                    post = self._create_post_from_template(json_template, post_id)
                    new_posts_count += 1
                
                # Update core fields from Excel
                post = self._update_post_from_excel_row(post, row)
                
                posts.append(post)
            
            print(f"✅ Processed {len(posts)} posts:")
            print(f"   • New posts created: {new_posts_count}")
            print(f"   • Existing posts updated: {updated_posts_count}")
            
            # Update summary statistics
            summary = self._recalculate_summary(posts, summary)
            
            # Save JSON
            output_data = {
                'summary': summary,
                'posts': posts
            }
            
            safe_save_json(output_data, self.json_file, create_backup=create_backup)
            
            print(f"✅ JSON file updated: {self.json_file}")
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to convert Excel to JSON: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _load_json_template(self) -> Dict[str, Any]:
        """Load JSON to extract template structure for new posts."""
        if not self.json_file.exists():
            # Return minimal template if JSON doesn't exist
            return {
                'post_id': '',
                'post_name': '',
                'revised_post_name': '',
                'display': True,
                'favourite': False,
                'post_date': None,
                'scraped_date': '',
                'description': '',
                'patreon_url': '',
                'post_type': '',
                'has_zip_files': False,
                'profile_images_count': 0,
                'zip_files': [],
                'profile_images': []
            }
        
        with open(self.json_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        posts = json_data.get('posts', [])
        
        if not posts:
            # Return minimal template
            return {
                'post_id': '',
                'post_name': '',
                'revised_post_name': '',
                'display': True,
                'favourite': False,
                'post_date': None,
                'scraped_date': '',
                'description': '',
                'patreon_url': '',
                'post_type': '',
                'has_zip_files': False,
                'profile_images_count': 0,
                'zip_files': [],
                'profile_images': []
            }
        
        # Collect all fields from all posts
        all_fields = {}
        for post in posts:
            for field, value in post.items():
                if field not in all_fields:
                    all_fields[field] = value
        
        # Detect types and create template with defaults
        template = {}
        for field, sample_value in all_fields.items():
            if isinstance(sample_value, bool):
                template[field] = False
            elif isinstance(sample_value, int):
                template[field] = 0
            elif isinstance(sample_value, float):
                template[field] = 0.0
            elif isinstance(sample_value, list):
                template[field] = []
            elif isinstance(sample_value, dict):
                template[field] = {}
            elif sample_value is None:
                template[field] = None
            else:
                template[field] = ''
        
        return template
    
    def _create_post_from_template(self, template: Dict[str, Any], post_id: str) -> Dict[str, Any]:
        """Create a new post entry from template."""
        post = template.copy()
        post['post_id'] = post_id
        post['scraped_date'] = datetime.now(SGT).isoformat()
        return post
    
    def _update_post_from_excel_row(self, post: Dict[str, Any], row: pd.Series) -> Dict[str, Any]:
        """Update post data from Excel row."""
        # Update core fields
        for json_field, excel_field in self.CORE_FIELDS.items():
            if excel_field in row.index:
                value = row[excel_field]
                
                # Handle pandas NaN/NaT
                if pd.isna(value):
                    if json_field in ['display', 'has_zip_files', 'favourite']:
                        post[json_field] = False
                    elif json_field == 'profile_images_count':
                        post[json_field] = 0
                    elif json_field in ['post_date']:
                        post[json_field] = None
                    else:
                        post[json_field] = ''
                else:
                    # Convert boolean types
                    if json_field in ['display', 'has_zip_files', 'favourite']:
                        post[json_field] = bool(value)
                    elif json_field == 'profile_images_count':
                        post[json_field] = int(value) if value != '' else 0
                    else:
                        post[json_field] = str(value) if value != '' else ''
        
        # Update ZIP file information (first item only)
        if 'ZIP Filename' in row.index and pd.notna(row['ZIP Filename']) and row['ZIP Filename'] != '':
            # Ensure zip_files list exists
            if not post.get('zip_files'):
                post['zip_files'] = [{}]
            
            zip_file = post['zip_files'][0] if post['zip_files'] else {}
            
            for json_field, excel_field in self.ZIP_FIELDS.items():
                if excel_field in row.index:
                    value = row[excel_field]
                    if pd.isna(value):
                        if json_field in ['downloaded', 'extracted']:
                            zip_file[json_field] = False
                        elif json_field in ['size_bytes']:
                            zip_file[json_field] = 0
                        elif json_field in ['size_mb']:
                            zip_file[json_field] = 0.0
                        else:
                            zip_file[json_field] = ''
                    else:
                        if json_field in ['downloaded', 'extracted']:
                            zip_file[json_field] = bool(value)
                        elif json_field == 'size_bytes':
                            zip_file[json_field] = int(value) if value != '' else 0
                        elif json_field == 'size_mb':
                            zip_file[json_field] = float(value) if value != '' else 0.0
                        else:
                            zip_file[json_field] = str(value)
            
            if post['zip_files']:
                post['zip_files'][0] = zip_file
            else:
                post['zip_files'] = [zip_file]
            
            post['has_zip_files'] = True if zip_file.get('filename') else False
        
        # Update image information (first item only)
        if 'Image Filename' in row.index and pd.notna(row['Image Filename']) and row['Image Filename'] != '':
            # Ensure profile_images list exists
            if not post.get('profile_images'):
                post['profile_images'] = [{}]
            
            img = post['profile_images'][0] if post['profile_images'] else {}
            
            for json_field, excel_field in self.IMAGE_FIELDS.items():
                if excel_field in row.index:
                    value = row[excel_field]
                    img[json_field] = str(value) if pd.notna(value) and value != '' else ''
            
            if post['profile_images']:
                post['profile_images'][0] = img
            else:
                post['profile_images'] = [img]
            
            post['profile_images_count'] = len(post['profile_images'])
        
        # Handle custom fields (columns not in standard mappings)
        all_standard_columns = set(self.CORE_FIELDS.values()) | set(self.ZIP_FIELDS.values()) | set(self.IMAGE_FIELDS.values())
        all_standard_columns.update(['Total ZIP Files', 'Other ZIP Files'])
        
        for excel_col in row.index:
            if excel_col not in all_standard_columns:
                # Convert Excel column name back to JSON field name
                json_field = excel_col.lower().replace(' ', '_')
                value = row[excel_col]
                
                if pd.isna(value):
                    # Try to infer type from existing value
                    if json_field in post:
                        if isinstance(post[json_field], bool):
                            post[json_field] = False
                        elif isinstance(post[json_field], (int, float)):
                            post[json_field] = 0
                        elif isinstance(post[json_field], list):
                            post[json_field] = []
                        elif isinstance(post[json_field], dict):
                            post[json_field] = {}
                        else:
                            post[json_field] = ''
                else:
                    # Try to detect if it's JSON
                    if isinstance(value, str) and (value.startswith('[') or value.startswith('{')):
                        try:
                            post[json_field] = json.loads(value)
                        except:
                            post[json_field] = value
                    elif isinstance(value, bool):
                        post[json_field] = value
                    else:
                        post[json_field] = value
        
        return post
    
    def _recalculate_summary(self, posts: List[Dict[str, Any]], existing_summary: Dict[str, Any]) -> Dict[str, Any]:
        """Recalculate summary statistics from posts."""
        summary = existing_summary.copy()
        
        summary['last_update'] = datetime.now(SGT).isoformat()
        summary['status'] = 'UPDATED'
        summary['total_posts'] = len(posts)
        summary['posts_with_images'] = len([p for p in posts if p.get('profile_images_count', 0) > 0])
        summary['total_images_downloaded'] = sum([p.get('profile_images_count', 0) for p in posts])
        summary['posts_with_zip_files'] = len([p for p in posts if p.get('has_zip_files', False)])
        
        # Recalculate date range
        valid_dates = [p['post_date'] for p in posts if p.get('post_date')]
        if valid_dates:
            if 'date_range' not in summary:
                summary['date_range'] = {}
            summary['date_range']['earliest'] = min(valid_dates)
            summary['date_range']['latest'] = max(valid_dates)
        else:
            if 'date_range' in summary:
                del summary['date_range']
        
        return summary


def main():
    """Main entry point for testing."""
    print("VAMA Metadata Handler - Bidirectional Sync Tool")
    print("=" * 60)
    print()
    print("Available operations:")
    print("  1. JSON → Excel (Convert JSON to Excel)")
    print("  2. Excel → JSON (Update JSON from Excel)")
    print()
    
    choice = input("Select operation (1 or 2): ").strip()
    
    handler = MetadataHandler()
    
    if choice == '1':
        print("\n🔄 Running JSON → Excel conversion...")
        success = handler.json_to_excel(create_backup=True)
        if success:
            print("\n✅ JSON → Excel conversion completed successfully!")
        else:
            print("\n❌ JSON → Excel conversion failed!")
            sys.exit(1)
    
    elif choice == '2':
        print("\n🔄 Running Excel → JSON update...")
        success = handler.excel_to_json(create_backup=True)
        if success:
            print("\n✅ Excel → JSON update completed successfully!")
        else:
            print("\n❌ Excel → JSON update failed!")
            sys.exit(1)
    
    else:
        print("❌ Invalid choice. Please select 1 or 2.")
        sys.exit(1)


if __name__ == "__main__":
    main()
