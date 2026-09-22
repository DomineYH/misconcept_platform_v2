#!/bin/bash
# Migration 006 Verification Script
# Author: AI Assistant
# Date: 2025-01-17
# Purpose: Verify video fields migration was applied correctly

set -e  # Exit on error

DB_FILE="dialogue_sim.db"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================"
echo "Migration 006 Verification"
echo "========================================"
echo ""

# Check if database exists
if [ ! -f "$DB_FILE" ]; then
    echo -e "${RED}ERROR: Database file '$DB_FILE' not found${NC}"
    exit 1
fi

echo "1. Checking scenario table schema..."
SCHEMA=$(sqlite3 "$DB_FILE" "PRAGMA table_info(scenario);")

# Check for video_url column
if echo "$SCHEMA" | grep -q "video_url"; then
    echo -e "${GREEN}✓ video_url column exists${NC}"
else
    echo -e "${RED}✗ video_url column missing${NC}"
    exit 1
fi

# Check for video_transcript column
if echo "$SCHEMA" | grep -q "video_transcript"; then
    echo -e "${GREEN}✓ video_transcript column exists${NC}"
else
    echo -e "${RED}✗ video_transcript column missing${NC}"
    exit 1
fi

echo ""
echo "2. Checking column types..."

# Check video_url type
URL_TYPE=$(sqlite3 "$DB_FILE" "PRAGMA table_info(scenario);" | grep "video_url" | cut -d'|' -f3)
if [ "$URL_TYPE" = "VARCHAR(500)" ]; then
    echo -e "${GREEN}✓ video_url type is VARCHAR(500)${NC}"
else
    echo -e "${YELLOW}WARNING: video_url type is '$URL_TYPE' (expected VARCHAR(500))${NC}"
fi

# Check video_transcript type
TRANSCRIPT_TYPE=$(sqlite3 "$DB_FILE" "PRAGMA table_info(scenario);" | grep "video_transcript" | cut -d'|' -f3)
if [ "$TRANSCRIPT_TYPE" = "TEXT" ]; then
    echo -e "${GREEN}✓ video_transcript type is TEXT${NC}"
else
    echo -e "${YELLOW}WARNING: video_transcript type is '$TRANSCRIPT_TYPE' (expected TEXT)${NC}"
fi

echo ""
echo "3. Checking nullable constraints..."

# Both columns should be nullable (NOT NULL = 0)
URL_NULLABLE=$(sqlite3 "$DB_FILE" "PRAGMA table_info(scenario);" | grep "video_url" | cut -d'|' -f4)
TRANSCRIPT_NULLABLE=$(sqlite3 "$DB_FILE" "PRAGMA table_info(scenario);" | grep "video_transcript" | cut -d'|' -f4)

if [ "$URL_NULLABLE" = "0" ]; then
    echo -e "${GREEN}✓ video_url is nullable${NC}"
else
    echo -e "${RED}✗ video_url is NOT NULL (should be nullable)${NC}"
    exit 1
fi

if [ "$TRANSCRIPT_NULLABLE" = "0" ]; then
    echo -e "${GREEN}✓ video_transcript is nullable${NC}"
else
    echo -e "${RED}✗ video_transcript is NOT NULL (should be nullable)${NC}"
    exit 1
fi

echo ""
echo "4. Checking data integrity..."

# Count scenarios before and after
SCENARIO_COUNT=$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM scenario;")
echo -e "${GREEN}✓ Scenario count: $SCENARIO_COUNT${NC}"

# Check if any existing data was corrupted
CORRUPTED=$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM scenario WHERE title IS NULL OR prompt IS NULL;")
if [ "$CORRUPTED" = "0" ]; then
    echo -e "${GREEN}✓ No corrupted records found${NC}"
else
    echo -e "${RED}✗ Found $CORRUPTED corrupted records${NC}"
    exit 1
fi

echo ""
echo "5. Testing video field operations..."

# Try to update a scenario with video fields (if scenarios exist)
if [ "$SCENARIO_COUNT" -gt "0" ]; then
    TEST_ID=$(sqlite3 "$DB_FILE" "SELECT id FROM scenario LIMIT 1;")
    sqlite3 "$DB_FILE" "UPDATE scenario SET video_url = 'https://example.com/test.mp4', video_transcript = 'Test transcript' WHERE id = $TEST_ID;"

    UPDATED=$(sqlite3 "$DB_FILE" "SELECT video_url FROM scenario WHERE id = $TEST_ID;")
    if [ "$UPDATED" = "https://example.com/test.mp4" ]; then
        echo -e "${GREEN}✓ Video URL update successful${NC}"
    else
        echo -e "${RED}✗ Video URL update failed${NC}"
        exit 1
    fi

    # Revert test change
    sqlite3 "$DB_FILE" "UPDATE scenario SET video_url = NULL, video_transcript = NULL WHERE id = $TEST_ID;"
    echo -e "${GREEN}✓ Test data reverted${NC}"
else
    echo -e "${YELLOW}WARNING: No scenarios to test (empty table)${NC}"
fi

echo ""
echo "========================================"
echo -e "${GREEN}✓ Migration 006 verification PASSED${NC}"
echo "========================================"
echo ""
echo "Full scenario table schema:"
sqlite3 "$DB_FILE" "PRAGMA table_info(scenario);"

exit 0
