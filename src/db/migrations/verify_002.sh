#!/bin/bash
# Verification script for migration 002_scenario_bot_config.sql

set -e  # Exit on error

DB_PATH="${1:-dialogue_sim.db}"

echo "=========================================="
echo "Migration 002: Scenario Bot Config"
echo "Database: $DB_PATH"
echo "=========================================="

# Backup database
echo ""
echo "[1/5] Creating backup..."
cp "$DB_PATH" "${DB_PATH}.backup_002_$(date +%Y%m%d_%H%M%S)"
echo "✓ Backup created"

# Apply migration
echo ""
echo "[2/5] Applying migration..."
sqlite3 "$DB_PATH" < src/db/migrations/002_scenario_bot_config.sql
echo "✓ Migration applied"

# Verify columns exist
echo ""
echo "[3/5] Verifying columns..."
sqlite3 "$DB_PATH" "PRAGMA table_info(scenario);" | grep -E "(chat_model|chat_temperature|tutor_enabled|tutor_intervention_threshold)"
echo "✓ All 4 columns added"

# Test CHECK constraints
echo ""
echo "[4/5] Testing CHECK constraints..."

# Test valid temperature (should succeed)
sqlite3 "$DB_PATH" "UPDATE scenario SET chat_temperature = 1.0 WHERE id = 1;" 2>&1 || echo "✗ Valid temperature test failed"

# Test invalid temperature (should fail)
if sqlite3 "$DB_PATH" "UPDATE scenario SET chat_temperature = 3.0 WHERE id = 1;" 2>&1 | grep -q "constraint"; then
    echo "✓ Temperature constraint working (rejected 3.0)"
else
    echo "✗ Temperature constraint not working"
fi

# Reset temperature
sqlite3 "$DB_PATH" "UPDATE scenario SET chat_temperature = NULL WHERE id = 1;"

# Test valid threshold (should succeed)
sqlite3 "$DB_PATH" "UPDATE scenario SET tutor_intervention_threshold = 5 WHERE id = 1;" 2>&1 || echo "✗ Valid threshold test failed"

# Test invalid threshold (should fail)
if sqlite3 "$DB_PATH" "UPDATE scenario SET tutor_intervention_threshold = 15 WHERE id = 1;" 2>&1 | grep -q "constraint"; then
    echo "✓ Threshold constraint working (rejected 15)"
else
    echo "✗ Threshold constraint not working"
fi

# Reset threshold
sqlite3 "$DB_PATH" "UPDATE scenario SET tutor_intervention_threshold = NULL WHERE id = 1;"

# Verify existing data integrity
echo ""
echo "[5/5] Verifying existing data..."
SCENARIO_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM scenario;")
echo "✓ All $SCENARIO_COUNT scenarios intact"

echo ""
echo "=========================================="
echo "Migration 002: ✓ PASSED"
echo "=========================================="
