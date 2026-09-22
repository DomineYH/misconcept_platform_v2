# Migration 002: Scenario Bot Configuration Override

## Overview
This migration adds 4 columns to the `scenario` table to support per-scenario chatbot configuration overrides.

## Date
2025-11-07

## Changes

### Added Columns

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `chat_model` | VARCHAR(50) | YES | NULL | Override StudentBot LLM model (e.g., 'gpt-4-turbo') |
| `chat_temperature` | REAL | YES | NULL | Override StudentBot temperature (0.0-2.0) |
| `tutor_enabled` | BOOLEAN | NO | 1 | Enable/disable TutorBot for this scenario |
| `tutor_intervention_threshold` | INTEGER | YES | NULL | Override TutorBot intervention frequency (1-10) |

### Constraints

1. **chat_temperature**: `CHECK (chat_temperature IS NULL OR (chat_temperature >= 0 AND chat_temperature <= 2))`
   - Ensures temperature is within valid LLM range
   - NULL indicates use of global configuration

2. **tutor_intervention_threshold**: `CHECK (tutor_intervention_threshold IS NULL OR (tutor_intervention_threshold BETWEEN 1 AND 10))`
   - Ensures threshold is within valid range
   - NULL indicates use of global configuration

### NULL Semantics
- **NULL values** = Use global `chatbot_config` settings
- **Non-NULL values** = Override global settings for this specific scenario

## Backward Compatibility
- ✓ All columns are NULL-able or have defaults
- ✓ Existing scenario data is unaffected
- ✓ No data migration required

## Application Instructions

### Using Python migrate.py
```bash
# Applies all pending migrations including 002
python -m src.db.migrations.migrate
```

### Manual Application
```bash
# Create backup first
cp dialogue_sim.db dialogue_sim.db.backup

# Apply migration
sqlite3 dialogue_sim.db < src/db/migrations/002_scenario_bot_config.sql

# Verify
sqlite3 dialogue_sim.db "PRAGMA table_info(scenario);"
```

### Verification Script
```bash
# Automated testing with constraint validation
bash src/db/migrations/verify_002.sh
```

## Usage Examples

### Example 1: Override StudentBot Model
```sql
-- Use GPT-4 for high-stakes scenarios
UPDATE scenario
SET chat_model = 'gpt-4-turbo'
WHERE id = 1;
```

### Example 2: Adjust Temperature
```sql
-- More creative responses for brainstorming scenarios
UPDATE scenario
SET chat_temperature = 1.2
WHERE title LIKE '%Creative%';

-- More deterministic for assessment scenarios
UPDATE scenario
SET chat_temperature = 0.3
WHERE title LIKE '%Assessment%';
```

### Example 3: Disable TutorBot
```sql
-- Disable tutor for independent practice scenarios
UPDATE scenario
SET tutor_enabled = 0
WHERE title LIKE '%Independent%';
```

### Example 4: Adjust Intervention Frequency
```sql
-- More frequent tutor interventions for struggling students
UPDATE scenario
SET tutor_intervention_threshold = 2
WHERE title LIKE '%Remedial%';

-- Less frequent for advanced students
UPDATE scenario
SET tutor_intervention_threshold = 8
WHERE title LIKE '%Advanced%';
```

### Example 5: Query Scenarios with Overrides
```sql
-- Find scenarios with custom configurations
SELECT
    id,
    title,
    chat_model,
    chat_temperature,
    tutor_enabled,
    tutor_intervention_threshold
FROM scenario
WHERE
    chat_model IS NOT NULL
    OR chat_temperature IS NOT NULL
    OR tutor_enabled = 0
    OR tutor_intervention_threshold IS NOT NULL;
```

## Rollback (if needed)
```sql
-- Remove added columns (CAUTION: data loss)
ALTER TABLE scenario DROP COLUMN chat_model;
ALTER TABLE scenario DROP COLUMN chat_temperature;
ALTER TABLE scenario DROP COLUMN tutor_enabled;
ALTER TABLE scenario DROP COLUMN tutor_intervention_threshold;
```

**Note**: SQLite requires creating a new table without these columns and copying data for true rollback. See `rollback_002.sql` if provided.

## Testing Checklist
- [x] Migration applies cleanly
- [x] CHECK constraints work (reject invalid values)
- [x] Existing scenario data intact
- [x] NULL semantics correct
- [x] Default values applied
- [x] Foreign key relationships preserved

## Related Files
- **Migration Script**: `002_scenario_bot_config.sql`
- **Verification Script**: `verify_002.sh`
- **Python Runner**: `migrate.py`

## Next Steps
1. Update `src/models/scenario.py` to include new fields
2. Update `src/services/session_mgr.py` to use scenario overrides
3. Update admin UI to allow editing these fields
4. Add API endpoints for scenario configuration management
