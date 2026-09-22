# Migration 006: Add Video Fields to Scenario Table

## Overview
This migration adds 2 columns to the `scenario` table to support video-based learning scenarios with transcripts.

## Date
2025-01-17

## Changes

### Added Columns

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `video_url` | VARCHAR(500) | YES | NULL | URL to video resource (YouTube, Vimeo, etc.) |
| `video_transcript` | TEXT | YES | NULL | Full transcript of the video content |

### Constraints
- **video_url**: Length limited to 500 characters (sufficient for most URLs)
- **video_transcript**: TEXT type for unlimited length transcripts
- Both columns are nullable (not all scenarios require videos)

### NULL Semantics
- **NULL values** = Scenario does not have an associated video
- **Non-NULL values** = Scenario includes video content for student context

## Backward Compatibility
- ✓ All columns are NULL-able
- ✓ Existing scenario data is unaffected
- ✓ No data migration required

## Application Instructions

### Using Python migrate.py
```bash
# Applies all pending migrations including 006
python -m src.db.migrations.migrate
```

### Manual Application
```bash
# Create backup first
cp dialogue_sim.db dialogue_sim.db.backup

# Apply migration
sqlite3 dialogue_sim.db < src/db/migrations/006_add_video_fields.sql

# Verify
sqlite3 dialogue_sim.db "PRAGMA table_info(scenario);"
```

## Usage Examples

### Example 1: Add Video to Scenario
```sql
-- Add YouTube video to a scenario
UPDATE scenario
SET
    video_url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    video_transcript = 'Full transcript of the video content here...'
WHERE id = 1;
```

### Example 2: Create Scenario with Video
```sql
INSERT INTO scenario (
    title,
    prompt,
    video_url,
    video_transcript,
    framework_id
) VALUES (
    'Photosynthesis Misconception',
    'Student believes plants breathe like animals...',
    'https://youtu.be/video_id',
    'Narrator: Today we will discuss photosynthesis...',
    1
);
```

### Example 3: Query Scenarios with Videos
```sql
-- Find all scenarios that have videos
SELECT
    id,
    title,
    video_url
FROM scenario
WHERE video_url IS NOT NULL;
```

### Example 4: Remove Video from Scenario
```sql
-- Remove video fields from a scenario
UPDATE scenario
SET
    video_url = NULL,
    video_transcript = NULL
WHERE id = 1;
```

## Rollback (if needed)
```sql
-- Remove added columns (CAUTION: data loss)
ALTER TABLE scenario DROP COLUMN video_url;
ALTER TABLE scenario DROP COLUMN video_transcript;
```

**Note**: SQLite supports DROP COLUMN starting from version 3.35.0 (2021-03-12). For older versions, you need to recreate the table without these columns.

## Testing Checklist
- [ ] Migration applies cleanly
- [ ] Existing scenario data intact
- [ ] NULL values allowed for both columns
- [ ] Foreign key relationships preserved
- [ ] ORM model matches database schema
- [ ] API schemas include video fields

## Related Files
- **Migration Script**: `006_add_video_fields.sql`
- **ORM Model**: `src/models/scenario.py` (lines 40-46)
- **API Schemas**: `src/api/schemas/__init__.py` (ScenarioCreate, ScenarioUpdate, ScenarioRead)
- **Init Schema**: `src/db/init_schema.py` (updated)

## Three-Way Synchronization Status
✓ **ORM Model** (`src/models/scenario.py`): Already includes video fields
✓ **Migration SQL** (`006_add_video_fields.sql`): Created
✓ **Init Schema** (`src/db/init_schema.py`): Updated

## Next Steps
1. Apply migration to database
2. Test video field updates via API
3. Update admin UI to allow editing video fields
4. Consider adding video player component to chat interface
5. Implement transcript search functionality (future enhancement)
