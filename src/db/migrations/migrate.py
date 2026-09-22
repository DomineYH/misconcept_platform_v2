"""Database migration runner script."""

import asyncio
import os
from pathlib import Path
from sqlalchemy import text

from src.db.connection import engine


async def ensure_migrations_table():
    """Create the migration tracking table if it doesn't exist."""
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL UNIQUE,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))


async def is_migration_applied(migration_filename: str) -> bool:
    """Check if a migration has already been applied.

    Args:
        migration_filename: Name of migration file

    Returns:
        True if migration has been applied, False otherwise
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT 1 FROM _migrations WHERE filename = :filename"),
            {"filename": migration_filename}
        )
        return result.fetchone() is not None


async def record_migration(migration_filename: str):
    """Record that a migration has been applied.

    Args:
        migration_filename: Name of migration file
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO _migrations (filename) VALUES (:filename)"),
            {"filename": migration_filename}
        )


async def run_migration(migration_file: Path):
    """Execute a single migration SQL file.

    Args:
        migration_file: Path to SQL migration file
    """
    print(f"Running migration: {migration_file.name}")

    # Read SQL file
    with open(migration_file, 'r', encoding='utf-8') as f:
        migration_sql = f.read()

    # Execute migration
    async with engine.begin() as conn:
        # Split SQL by semicolon and execute each statement.
        # Skip only chunks where every line is a comment or blank — chunks
        # that mix leading comments with real SQL must still execute.
        statements = []
        for stmt in migration_sql.split(";"):
            stripped = stmt.strip()
            if not stripped:
                continue
            if all(
                line.strip().startswith("--") or not line.strip()
                for line in stripped.split("\n")
            ):
                continue
            statements.append(stripped)

        for i, stmt in enumerate(statements, 1):
            try:
                # Skip empty or comment-only statements
                if not stmt or all(line.strip().startswith("--") or not line.strip()
                                  for line in stmt.split("\n")):
                    continue

                await conn.execute(text(stmt))
                print(f"  Statement {i}/{len(statements)} executed")
            except Exception as e:
                print(f"Error executing statement {i}:")
                print(f"  SQL: {stmt[:100]}...")
                print(f"  Error: {e}")
                raise

    # Record successful migration
    await record_migration(migration_file.name)
    print(f"✓ Migration {migration_file.name} completed successfully")


async def run_all_migrations():
    """Run all migration files in order."""
    migrations_dir = Path(__file__).parent

    # Ensure tracking table exists
    await ensure_migrations_table()

    # Get all .sql files sorted by name (001_, 002_, etc.).
    # Exclude *_down.sql rollback scripts — they are not upgrade migrations.
    migration_files = sorted(
        f
        for f in migrations_dir.glob("*.sql")
        if not f.name.endswith("_down.sql")
    )

    if not migration_files:
        print("No migration files found")
        return

    # Check which migrations need to be applied
    pending_migrations = []
    applied_migrations = []

    for migration_file in migration_files:
        if await is_migration_applied(migration_file.name):
            applied_migrations.append(migration_file.name)
        else:
            pending_migrations.append(migration_file)

    # Log status
    print(f"Migration status:")
    print(f"  Total migrations: {len(migration_files)}")
    print(f"  Already applied: {len(applied_migrations)}")
    print(f"  Pending: {len(pending_migrations)}")

    if applied_migrations:
        print(f"\nAlready applied:")
        for filename in applied_migrations:
            print(f"  ✓ {filename}")

    if not pending_migrations:
        print("\n✓ All migrations are up to date")
        return

    print(f"\nRunning {len(pending_migrations)} pending migration(s):")

    for migration_file in pending_migrations:
        await run_migration(migration_file)

    print("\n✓ All pending migrations completed successfully")


if __name__ == "__main__":
    asyncio.run(run_all_migrations())
