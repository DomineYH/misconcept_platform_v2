"""Main FastAPI application entry point."""

import json
import logging
import re
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pythonjsonlogger import jsonlogger
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette_csrf import CSRFMiddleware

from src.api.dependencies import AuthenticationRequired
from src.config import config
from src.db.connection import close_db, init_db
from src.db.seed import ensure_default_admin_account

# Configure structured JSON logging
logger = logging.getLogger()


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter for structured logging."""

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = record.created
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        if hasattr(record, "request_id"):
            log_record["request_id"] = record.request_id


# Setup JSON logging
json_handler = logging.StreamHandler(sys.stdout)
formatter = CustomJsonFormatter("%(timestamp)s %(level)s %(name)s %(message)s")
json_handler.setFormatter(formatter)
logger.addHandler(json_handler)

# Log level from environment (DEBUG for troubleshooting OpenAI responses)
import os  # noqa: E402

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logger.setLevel(getattr(logging, log_level, logging.INFO))

# Initialize rate limiter (disabled in test mode)
limiter = Limiter(
    key_func=get_remote_address,
    enabled=not config.TESTING,
)


class LoggingMiddleware:
    """Pure ASGI middleware for logging requests with timing.

    Uses pure ASGI instead of BaseHTTPMiddleware to avoid
    ContextVar propagation issues with SessionMiddleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.time()
        request = Request(scope)
        request_id = f"{time.time()}"
        path = request.url.path
        method = request.method
        client = request.client.host if request.client else None

        logger.info(
            "Request started",
            extra={
                "request_id": request_id,
                "method": method,
                "path": path,
                "client": client,
            },
        )

        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            duration = time.time() - start_time
            logger.info(
                "Request completed",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration": round(duration, 3),
                },
            )
        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                "Request failed",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "error": str(e),
                    "duration": round(duration, 3),
                },
                exc_info=True,
            )
            raise


class SecurityHeadersMiddleware:
    """Pure ASGI middleware for adding security headers.

    Uses pure ASGI instead of BaseHTTPMiddleware to avoid
    ContextVar propagation issues with SessionMiddleware.
    """

    HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"x-xss-protection", b"1; mode=block"),
        (
            b"strict-transport-security",
            b"max-age=31536000; includeSubDomains",
        ),
        (
            b"content-security-policy",
            b"default-src 'self'; "
            b"script-src 'self' 'unsafe-inline' "
            b"https://unpkg.com; "
            b"style-src 'self' 'unsafe-inline'; "
            b"img-src 'self' data:; "
            b"connect-src 'self'; "
            b"frame-src 'self' "
            b"https://www.youtube.com "
            b"https://www.youtube-nocookie.com; "
            b"object-src 'none'; "
            b"base-uri 'self'",
        ),
        (
            b"referrer-policy",
            b"strict-origin-when-cross-origin",
        ),
        (
            b"permissions-policy",
            b"geolocation=(), microphone=(), camera=()",
        ),
    ]

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self.HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup - validate config in non-test mode
    if not config.TESTING:
        config.validate()
    await init_db()
    # Admin bootstrap is opt-in: production deployments with read-only DB
    # roles or external seed jobs would otherwise fail to boot. Set
    # BOOTSTRAP_ADMIN_ON_STARTUP=true in .env for dev first-run seeding.
    if not config.TESTING and config.BOOTSTRAP_ADMIN_ON_STARTUP:
        await ensure_default_admin_account()
    print("Database initialized")
    yield
    # Shutdown
    await close_db()
    print("Database connections closed")


# Create FastAPI application
app = FastAPI(
    title="Misconception Dialogue Simulator",
    description=("Three-party dialogue simulator for teacher training"),
    version="0.1.0",
    lifespan=lifespan,
)

# Add rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Add authentication redirect handler
@app.exception_handler(AuthenticationRequired)
async def auth_required_handler(
    request: Request, exc: AuthenticationRequired
) -> Response:
    """Redirect to login page when authentication is required.

    For chat-related HTMX requests, return 401 and a custom HX trigger
    so the frontend can stop polling and show an explicit session-expired
    notice instead of forcing an immediate page navigation.

    Other HTMX requests keep existing HX-Redirect behavior.
    """
    is_htmx = request.headers.get("HX-Request") == "true"
    request_path = request.url.path

    is_chat_path = request_path.startswith("/sessions/") and (
        "/messages" in request_path
        or request_path.endswith("/close")
        or request_path.endswith("/end")
        or request_path.endswith("/analyze")
        or request_path.endswith("/analysis_modal")
    )

    if is_htmx and is_chat_path:
        event_payload = {
            "auth-expired": {
                "redirect_url": exc.redirect_url,
                "code": "AUTH_EXPIRED",
            }
        }
        body = {
            "code": "AUTH_EXPIRED",
            "detail": "Authentication required",
            "redirect_url": exc.redirect_url,
        }
        return Response(
            content=json.dumps(body),
            status_code=401,
            media_type="application/json",
            headers={
                "HX-Trigger": json.dumps(event_payload),
                "Cache-Control": "no-store",
            },
        )

    if is_htmx:
        return Response(
            status_code=200,
            headers={"HX-Redirect": exc.redirect_url},
        )
    return RedirectResponse(url=exc.redirect_url, status_code=303)


# Add CORS middleware
# In development, allow localhost. In production, require FRONTEND_URL.
def _get_cors_origins() -> list[str]:
    """Get allowed CORS origins based on environment."""
    if config.FRONTEND_URL:
        # Support comma-separated origins
        return [o.strip() for o in config.FRONTEND_URL.split(",") if o.strip()]
    if config.is_production:
        # In production without FRONTEND_URL, only allow same-origin
        return []
    # In development, allow common localhost ports
    return [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


cors_origins = _get_cors_origins()
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

# Add security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Add logging middleware
app.add_middleware(LoggingMiddleware)

# Add session middleware for cookie-based auth (T112: Security hardening)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie="session_id",
    max_age=28800,  # 8 hours
    same_site="lax",
    https_only=config.is_production,  # Enable HTTPS-only in production
    # HttpOnly flag is enforced by SessionMiddleware internally.
)

# Add CSRF protection middleware (Double Submit Cookie)
# Disabled in testing mode (same pattern as rate limiting).
if not config.TESTING:
    app.add_middleware(
        CSRFMiddleware,
        secret=config.SESSION_SECRET,
        exempt_urls=[
            re.compile(r"/health"),
            re.compile(r"/login"),
        ],
        header_name="x-csrf-token",
        cookie_name="csrftoken",
        cookie_secure=config.is_production,
    )

# Mount static files
app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)

# Register route blueprints
from src.api.routes import (  # noqa: E402
    about,
    admin,
    admin_analysis,
    admin_api_usage,
    admin_prompts,
    admin_session_actions,
    admin_session_export,
    admin_session_stats,
    auth,
    health,
    scenarios,
    sessions,
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(scenarios.router)
app.include_router(sessions.router)
app.include_router(about.router)
app.include_router(admin.router)
app.include_router(admin_analysis.router)
app.include_router(admin_api_usage.router)
app.include_router(admin_prompts.router)
app.include_router(admin_session_export.router)
app.include_router(admin_session_actions.router)
app.include_router(admin_session_stats.router)
