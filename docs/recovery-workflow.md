# Recovery workflow

## Purpose
Recover posts that were previously extracted on the old machine using the saved pre-reset recovery manifest.

## Default first batch
The recovery script targets the **first 4 posts** from:

- `data/metadata/backups/pre_reset_recovery_manifest_20260530T135607Z.json`

## Script
- `scripts/recover_previously_extracted_posts.py`

## Behaviour
For each targeted post, the script:

1. downloads missing ZIPs
2. clears the extracted folder and derived files for that post
3. extracts the ZIPs
4. regenerates:
   - `app/webapp/posts/{post_id}.html`
   - `app/webapp/posts/{post_id}_cascade.html`
   - `app/webapp/posts/{post_id}_metadata.json`
5. updates progress in:
   - `data/metadata/backups/recovery_progress.json`

## Usage
Preview the first 4 posts without running:

```bash
python scripts/recover_previously_extracted_posts.py
```

Run the first 4 posts:

```bash
python scripts/recover_previously_extracted_posts.py --run
```

Run a different batch size:

```bash
python scripts/recover_previously_extracted_posts.py --limit 10 --run
```
