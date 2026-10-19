"""Main FastAPI application entry point."""

import asyncio
import json
import logging
import re
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pythonjsonlogger import jsonlogger
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette_csrf import CSRFMiddleware

from src.api.dependencies import (
    AuthenticationRequired,
    DemoRequestDB,
    get_current_user,
    templates,
)
from src.config import config
from src.db.connection import AsyncSessionLocal, close_db, init_db
from src.db.seed import ensure_default_admin_account
from src.demo.database import DatabaseRoot, DemoError
from src.models import User

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
    enabled=not config.TESTING and config.APP_MODE == "normal",
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
            b"frame-src 'none'; "
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


def demo_seed():
    """Return the verified product (SeedSpec, TourContent) pair.

    #197 owns the product seed and tour content; until it is bound here,
    demo startup refuses instead of serving unverified data.
    """
    raise DemoError("CONFIGURATION_INVALID")


async def _demo_cleanup(visitors, interval):
    while True:
        await asyncio.sleep(interval)
        try:
            result = await visitors.cleanup_tick()
        except Exception as error:
            # Codes only: exception text may carry real root paths.
            logger.error(
                "Demo cleanup failed",
                extra={"code": getattr(error, "code", type(error).__name__)},
            )
            if isinstance(error, DemoError):
                continue  # §8: retried on the next tick; /health reports it.
            raise
        logger.info(
            "Demo cleanup",
            extra={
                "removed": result.removed,
                "pending": result.pending,
                "codes": list(result.failure_codes),
            },
        )


@asynccontextmanager
async def demo_lifespan(app: FastAPI):
    """§4: verified root and seed only; no normal init, bootstrap or orphan scan."""
    from src.demo.lifecycle import Lifecycle
    from src.demo.session import DemoSession
    from src.demo.visitors import VisitorRegistry

    settings = config.demo_settings
    root = None
    try:
        root = DatabaseRoot(
            config.DEMO_DATA_ROOT,
            initialize=True,
            max_db_bytes=settings.max_db_bytes,
        ).open()
        spec, tour_content = demo_seed()
        root.validate_seed(spec)
        lifecycle = Lifecycle()
        visitors = VisitorRegistry(root, lifecycle=lifecycle, settings=settings)
        lifecycle.visitors = visitors
        request_db = DemoRequestDB(
            visitors, lifecycle, seed_refs=tour_content.seed_refs
        )
        session = DemoSession(
            visitors,
            lifecycle,
            request_db,
            tour_content=tour_content,
            drain_seconds=settings.drain_seconds,
        )
    except BaseException as error:
        if root is not None:
            root.close()
        if not isinstance(error, Exception):
            raise
        # Chained OS errors would print the real root path in startup logs.
        raise DemoError(
            error.code
            if isinstance(error, DemoError)
            else "CONFIGURATION_INVALID"
        ) from None
    app.state.demo_request_db = request_db
    app.state.demo_session = session
    app.state.demo_seed = spec
    app.state.demo_cleanup = asyncio.create_task(
        _demo_cleanup(visitors, settings.cleanup_interval_seconds)
    )
    try:
        yield
    finally:
        try:
            app.state.demo_cleanup.cancel()
            await asyncio.gather(app.state.demo_cleanup, return_exceptions=True)
            try:
                await session.shutdown()
            finally:
                # Each retired scope drains for at most DEMO_DRAIN_SECONDS.
                result = await visitors.shutdown()
            logger.info(
                "Demo shutdown",
                extra={
                    "removed": result.removed,
                    "pending": result.pending,
                    "codes": list(result.failure_codes),
                },
            )
        finally:
            root.close()
            app.state.demo_request_db = app.state.demo_session = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    if config.APP_MODE == "demo":
        async with demo_lifespan(app):
            yield
        return
    # Startup - validate config in non-test mode
    if not config.TESTING:
        config.validate()
    await init_db()
    from src.db.connection import AsyncSessionLocal
    from src.services.generation_lifecycle import interrupt_orphans

    await interrupt_orphans(AsyncSessionLocal)
    from src.services.probe_lifecycle import interrupt_probe_orphans

    await interrupt_probe_orphans(AsyncSessionLocal)
    # Admin bootstrap is opt-in: production deployments with read-only DB
    # roles or external seed jobs would otherwise fail to boot. Set
    # BOOTSTRAP_ADMIN_ON_STARTUP=true in .env for dev first-run seeding.
    if not config.TESTING and config.BOOTSTRAP_ADMIN_ON_STARTUP:
        await ensure_default_admin_account()
    print("Database initialized")
    try:
        yield
    finally:
        from src.services.analysis_runs import stop_analyses
        from src.services.probe_execution import stop_probes

        await stop_analyses()
        await stop_probes()
        await close_db()
        print("Database connections closed")


# Create FastAPI application
# Demo serves no unwrapped API docs; every visitor response is no-store.
docs = (
    {}
    if config.APP_MODE == "normal"
    else dict(docs_url=None, redoc_url=None, openapi_url=None)
)
app = FastAPI(
    title="Misconception Dialogue Simulator",
    description=("Three-party dialogue simulator for teacher training"),
    version="0.1.0",
    lifespan=lifespan,
    **docs,
)

# Add rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(HTTPException)
async def html_http_error_handler(
    request: Request, exc: HTTPException
) -> Response:
    """Render safe document errors while preserving the default API contract."""
    demo = config.APP_MODE == "demo"
    if (
        exc.status_code not in (403, 404)
        or request.method not in ("GET", "HEAD")
        or "HX-Request" in request.headers
        or not any(
            value.partition(";")[0].strip().lower() == "text/html"
            for value in request.headers.get("accept", "").split(",")
        )
    ):
        if demo and exc.status_code in (403, 404):
            from src.api.routes.demo import error_response

            return error_response(
                DemoError(
                    "ACTION_FORBIDDEN"
                    if exc.status_code == 403
                    else "RESOURCE_NOT_FOUND"
                )
            )
        response = await http_exception_handler(request, exc)
        if demo:
            response.headers["Cache-Control"] = "no-store"
        return response

    user = None
    if demo:
        # Only this request's current visitor context may name the user.
        db = getattr(getattr(request.state, "demo_context", None), "db", None)
        if db is not None:
            try:
                user = await get_current_user(request, db)
            except DemoError:
                user = None
    else:
        user_id = request.session.get("user_id")
        if user_id:
            async with AsyncSessionLocal() as db:
                user = await db.get(User, user_id)
    response = templates.TemplateResponse(
        "error.html",
        {"request": request, "user": user, "status_code": exc.status_code},
        status_code=exc.status_code,
        headers=exc.headers,
    )
    if demo:
        response.headers["Cache-Control"] = "no-store"
    return response


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
    if request.url.path.startswith("/runs/") or (
        request.url.path.startswith("/sessions/")
        and (
            "/turns/" in request.url.path or request.url.path.endswith("/runs")
        )
    ):
        return Response(
            content=json.dumps({"code": "AUTH_EXPIRED"}),
            status_code=401,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
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
# Demo serves only its own origin; normal session/CSRF cookies never apply.
if cors_origins and config.APP_MODE == "normal":
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
if config.APP_MODE == "normal":
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
if not config.TESTING and config.APP_MODE == "normal":
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
    admin_comparison_conversations,
    admin_comparisons,
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
app.include_router(admin_comparisons.router)
app.include_router(admin_comparison_conversations.router)
app.include_router(admin_api_usage.router)
app.include_router(admin_session_export.router)
app.include_router(admin_session_actions.router)
app.include_router(admin_session_stats.router)

if config.APP_MODE == "demo":
    from fastapi.routing import APIRoute, request_response

    from src.api import dependencies
    from src.api.routes import (
        demo,
        session_analysis,
        session_messages,
    )
    from src.api.routes.demo import error_response

    # §8: shared IPs are not visitor identities; the demo boundary limits instead.
    for module in (
        auth,
        sessions,
        session_messages,
        session_analysis,
        admin_session_actions,
    ):
        module.limiter.enabled = False

    app.include_router(demo.router)
    app.dependency_overrides[demo.get_demo_security] = (
        dependencies.get_demo_security
    )
    app.dependency_overrides[demo.get_demo_session] = (
        dependencies.get_demo_session
    )

    async def _forbidden(request: Request) -> Response:
        return error_response(DemoError("ACTION_FORBIDDEN"))

    # Every visitor route resolves its DB at this boundary; /health is visit-free.
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path == "/health":
            continue
        if route.path in ("/login", "/logout") and "POST" in route.methods:
            # §4: refused before session, CSRF or password parsing.
            route.app = request_response(_forbidden)
        else:
            route.app = request_response(
                dependencies.demo_request_handler(route.get_route_handler())
            )
