"""Database connection and session management."""

import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from src.config import config
from src.demo.database import DemoError

logger = logging.getLogger(__name__)


# Configure SQLite for better concurrency with WAL mode
def set_sqlite_pragma(dbapi_conn, connection_record):
    """Set SQLite pragmas for better performance and concurrency."""
    cursor = dbapi_conn.cursor()
    # Enable WAL mode for better concurrent read/write performance
    cursor.execute("PRAGMA journal_mode=WAL")
    # Set synchronous mode to NORMAL for better performance
    cursor.execute("PRAGMA synchronous=NORMAL")
    # Increase cache size to 10MB (default is 2MB)
    cursor.execute("PRAGMA cache_size=-10000")
    # Enable foreign key constraints
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _forbid_normal_database(*args, **kwargs):
    raise DemoError("ACTION_FORBIDDEN")


engine: AsyncEngine | None = None
if config.APP_MODE == "normal":
    engine = create_async_engine(
        config.DATABASE_URL,
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
else:
    AsyncSessionLocal = _forbid_normal_database

# Declarative base for models
Base = declarative_base()


async def init_db():
    """Development boot uses the official installer; production upgrades explicitly."""
    if config.APP_MODE != "normal":
        _forbid_normal_database()
    if config.is_production:
        return
    from src.db.migrations.migrate import run_all_migrations

    await run_all_migrations()


async def close_db():
    """Close database connections."""
    if config.APP_MODE != "normal":
        _forbid_normal_database()
    await engine.dispose()
