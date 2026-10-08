"""Per-request password checks, with five failures per five minutes."""

import asyncio

from fastapi import HTTPException
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

reauth_limiter = MovingWindowRateLimiter(MemoryStorage())
FAILURES = parse("5/5minutes")


async def reauthenticate(request, user, password):
    limited = HTTPException(
        429,
        detail={"code": "reauthentication_limited"},
        headers={"Retry-After": "300"},
    )
    identities = (
        ("admin", str(user.id)),
        ("address", request.client.host if request.client else "unknown"),
    )
    if not all(
        reauth_limiter.test(FAILURES, "provider-reauth", *identity)
        for identity in identities
    ):
        raise limited
    try:
        valid = await asyncio.to_thread(
            user.verify_password, password.get_secret_value()
        )
    except ValueError:
        valid = False
    if not valid:
        allowed = [
            reauth_limiter.hit(FAILURES, "provider-reauth", *identity)
            for identity in identities
        ]
        if not all(allowed):
            raise limited
        raise HTTPException(422, detail={"code": "reauthentication_failed"})
