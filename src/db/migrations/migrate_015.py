"""Migration 015: Convert labels_json to {name, criteria} format.

Converts old format: ["label1", "label2"]
To new format: [{"name": "label1", "criteria": ""}, ...]

Already-migrated rows (where first element is a dict) are skipped.
"""

import json
import sqlite3
import sys


def migrate(db_path: str = "dialogue_sim.db"):
    """Convert labels_json from string[] to object[]."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT id, labels_json FROM analysis_framework")
    rows = cursor.fetchall()

    converted = 0
    skipped = 0

    for row_id, labels_json in rows:
        if not labels_json:
            skipped += 1
            continue

        parsed = json.loads(labels_json)

        if not parsed:
            skipped += 1
            continue

        # Skip if already in new format
        if isinstance(parsed[0], dict):
            skipped += 1
            continue

        # Convert old string[] to {name, criteria}[]
        new_labels = [{"name": label, "criteria": ""} for label in parsed]
        cursor.execute(
            "UPDATE analysis_framework " "SET labels_json = ? WHERE id = ?",
            (
                json.dumps(new_labels, ensure_ascii=False),
                row_id,
            ),
        )
        converted += 1

    conn.commit()
    conn.close()

    print(
        f"Migration 015 complete: " f"{converted} converted, {skipped} skipped"
    )


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "dialogue_sim.db"
    migrate(db_path)
