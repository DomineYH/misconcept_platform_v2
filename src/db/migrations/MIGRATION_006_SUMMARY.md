# Migration 006: Add Video Fields - Summary

## Overview
This migration adds video URL and transcript support to the scenario table, enabling video-based learning scenarios.

## Date Created
2025-01-17

## Status
✅ **Ready for Application**

All three synchronization points are complete:
- ✓ ORM Model (`src/models/scenario.py`) - Already had video fields
- ✓ Migration SQL (`006_add_video_fields.sql`) - Created
- ✓ Init Schema (`src/db/init_schema.py`) - Updated

## Files Created/Modified

### Created Files
1. `/mnt/d/dev/misconcept_platform/src/db/migrations/006_add_video_fields.sql`
   - SQLite ALTER TABLE statements to add video_url and video_transcript columns

2. `/mnt/d/dev/misconcept_platform/src/db/migrations/README_006.md`
   - Comprehensive migration documentation with examples

3. `/mnt/d/dev/misconcept_platform/src/db/migrations/verify_006.sh`
   - Automated verification script to test migration success

4. `/mnt/d/dev/misconcept_platform/src/db/migrations/MIGRATION_006_SUMMARY.md`
   - This summary document

### Modified Files
1. `/mnt/d/dev/misconcept_platform/src/db/init_schema.py`
   - Added video_url and video_transcript to scenario table definition (lines 39-40)

## Changes Summary

### Database Schema Changes
```sql
-- Added to scenario table:
video_url VARCHAR(500) NULL
video_transcript TEXT NULL
```

### Existing Code Support
The following files already support video fields:
- `src/models/scenario.py` (ORM model, lines 40-46)
- `src/api/schemas/__init__.py` (Pydantic schemas)

## How to Apply Migration

### Option 1: Automated (Recommended)
```bash
# Create backup
cp dialogue_sim.db dialogue_sim.db.backup_$(date +%Y%m%d_%H%M%S)

# Apply all pending migrations
python -m src.db.migrations.migrate

# Verify
bash src/db/migrations/verify_006.sh
```

### Option 2: Manual
```bash
# Create backup
cp dialogue_sim.db dialogue_sim.db.backup

# Apply migration directly
sqlite3 dialogue_sim.db < src/db/migrations/006_add_video_fields.sql

# Verify
sqlite3 dialogue_sim.db "PRAGMA table_info(scenario);"

# Run verification script
bash src/db/migrations/verify_006.sh
```

## Verification Checklist

Before applying:
- [ ] Database backup created
- [ ] All three sync points reviewed (ORM, SQL, init_schema)
- [ ] No conflicting migrations pending

After applying:
- [ ] Migration SQL executed without errors
- [ ] Verification script passes (`verify_006.sh`)
- [ ] All existing tests pass (`pytest tests/`)
- [ ] Scenario table has 14 columns (was 12)
- [ ] video_url column type is VARCHAR(500)
- [ ] video_transcript column type is TEXT
- [ ] Both columns are nullable

## Testing Commands

```bash
# Run verification script
bash src/db/migrations/verify_006.sh

# Check scenario table schema
sqlite3 dialogue_sim.db "PRAGMA table_info(scenario);"

# Test video field update (if scenarios exist)
sqlite3 dialogue_sim.db "
  UPDATE scenario
  SET video_url = 'https://example.com/test.mp4'
  WHERE id = 1;
"

# Query scenarios with videos
sqlite3 dialogue_sim.db "
  SELECT id, title, video_url
  FROM scenario
  WHERE video_url IS NOT NULL;
"
```

## Rollback Instructions

If migration needs to be reverted:

```bash
# Restore from backup
cp dialogue_sim.db.backup dialogue_sim.db

# OR drop columns manually (SQLite 3.35.0+)
sqlite3 dialogue_sim.db "
  ALTER TABLE scenario DROP COLUMN video_url;
  ALTER TABLE scenario DROP COLUMN video_transcript;
"
```

**Note**: For SQLite versions < 3.35.0, table recreation is required for rollback.

## Impact Analysis

### Breaking Changes
- **None** - All changes are additive and backward compatible

### Affected Components
- ✓ **ORM Models**: Already supports video fields
- ✓ **API Schemas**: Already includes video fields in request/response models
- ✓ **Database**: Will have new columns after migration
- ? **Admin UI**: May need updates to edit video fields (future work)
- ? **Chat Interface**: May need video player component (future work)

### Performance Impact
- **Minimal** - No indices added initially
- Future consideration: Add index on video_url if filtering by video presence

## Security Considerations

### Input Validation
- video_url: Should validate URL format in application layer
- video_url: Length limited to 500 chars at database level
- video_transcript: No length limit (TEXT type)

### Recommended Validations (Application Layer)
```python
# Example validation in Pydantic schema
from pydantic import HttpUrl, validator

class ScenarioCreate(BaseModel):
    video_url: Optional[HttpUrl] = None  # Validates URL format
    video_transcript: Optional[str] = None

    @validator('video_url')
    def validate_video_url(cls, v):
        if v and len(str(v)) > 500:
            raise ValueError('URL too long (max 500 chars)')
        return v
```

## Next Steps

1. **Apply Migration** (This step)
   ```bash
   python -m src.db.migrations.migrate
   bash src/db/migrations/verify_006.sh
   ```

2. **Test API Endpoints**
   - Test creating scenarios with video fields
   - Test updating video fields
   - Verify API responses include video fields

3. **Admin UI Updates** (Future)
   - Add video URL input field
   - Add video transcript textarea
   - Add video preview/player

4. **Chat Interface** (Future)
   - Display video if scenario has video_url
   - Show transcript toggle
   - Implement video playback tracking

5. **Documentation Updates** (Future)
   - Update API documentation
   - Add video scenario examples
   - Document video URL requirements

## Questions or Issues?

See detailed documentation:
- Migration details: `README_006.md`
- Migration SQL: `006_add_video_fields.sql`
- Verification: `verify_006.sh`

Refer to CLAUDE.md for schema migration best practices.
