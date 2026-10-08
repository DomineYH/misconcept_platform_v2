"""Rotate disclosed bulk passwords; print credentials once after commit."""

import argparse
import asyncio
import json
import secrets

from sqlalchemy import select, text

from src.db.connection import AsyncSessionLocal, engine
from src.models import User


async def rotate_default_passwords(dry_run: bool = False) -> None:
    credentials = []
    try:
        async with AsyncSessionLocal() as db:
            if not dry_run:
                # Keep a concurrent password reset from being overwritten.
                await db.execute(text("BEGIN IMMEDIATE"))
            users = await db.scalars(select(User).order_by(User.id))
            for user in users:
                if not user.verify_password("00000000"):
                    continue
                credential = {
                    "username": user.username,
                    "nickname": user.nickname,
                }
                if not dry_run:
                    password = secrets.token_hex(16)
                    user.set_password(password)
                    credential["initial_password"] = password
                credentials.append(credential)
            await db.commit()
        print(json.dumps(credentials, ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="List targets without changes."
    )
    asyncio.run(rotate_default_passwords(parser.parse_args().dry_run))
