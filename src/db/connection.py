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

logger = logging.getLogger(__name__)

# Create async engine with SQLite
engine: AsyncEngine = create_async_engine(
    config.DATABASE_URL,
    echo=False,  # Set to True for SQL query logging
    future=True,
    connect_args={
        "check_same_thread": False,  # Allow multi-threaded access
    },
)


# Configure SQLite for better concurrency with WAL mode
@event.listens_for(engine.sync_engine, "connect")
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

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Declarative base for models
Base = declarative_base()


async def init_db():
    """Initialize database. Skip create_all in production."""
    if config.is_production:
        logger.info("Production mode: skipping create_all")
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Close database connections."""
    await engine.dispose()
