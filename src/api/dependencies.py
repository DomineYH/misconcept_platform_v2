"""FastAPI dependency injection for database and auth."""

import asyncio
import hmac
import logging
import math
import secrets
import time
from datetime import timezone
from functools import wraps
from typing import AsyncGenerator
from urllib.parse import urlencode, urlsplit

import markdown as md_lib
from fastapi import Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates
from itsdangerous import BadData, URLSafeTimedSerializer
from markupsafe import Markup, escape
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.util import await_only
from starlette.datastructures import FormData, QueryParams
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config import config
from src.db.connection import AsyncSessionLocal
from src.demo.database import DemoError
from src.demo.session import CONTENT_VERSION, CONTRACT_VERSION, FIXTURE_VERSION
from src.demo.visitors import ControlContext, DemoContext
from src.i18n import display_language, t, translate, ui_strings


def demo_template_context(request: Request) -> dict:
    """§24 `demo_config`: display-safe fields only, never factories/paths/secrets."""
    public_url = config.DEMO_PUBLIC_URL or None
    if config.APP_MODE == "normal":
        return {"demo_config": {"mode": "normal", "public_url": public_url}}
    ctx = getattr(request.state, "demo_context", None)
    ready = isinstance(ctx, DemoContext)
    return {
        "demo_config": {
            "mode": "demo",
            "contract_version": CONTRACT_VERSION,
            "content_version": CONTENT_VERSION,
            "fixture_version": FIXTURE_VERSION,
            "state": "ready" if ready else None,
            "browser_scope_id": ctx.browser_scope_id if ready else None,
            "generation": ctx.generation if ready else None,
            "auth_epoch": ctx.auth_epoch if ready else None,
            "role": ctx.role if ready else None,
            "expires_at": (
                ctx.expires_at.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
                if ready
                else None
            ),
            "idle_ttl_seconds": config.DEMO_IDLE_TTL_SECONDS,
            "absolute_ttl_seconds": config.DEMO_ABSOLUTE_TTL_SECONDS,
            "active": False,
            "last_error": None,
            "start_url": "/demo/start",
            "role_url": "/demo/role",
            "reset_url": "/demo/reset",
            "state_url": "/demo/state",
            "public_url": public_url,
            "csrf_cookie_name": "demo_csrftoken",
            "csrf_header_name": "x-csrf-token",
            "ui_language": display_language(request),
            "tour_facts": None,
        }
    }


# Shared Jinja2Templates instance
templates = Jinja2Templates(
    directory="src/templates", context_processors=[demo_template_context]
)
templates.env.globals.update(
    t=t, display_language=display_language, ui_strings=ui_strings
)


def md_filter(text: str | None) -> Markup:
    """Convert markdown text to safe HTML.

    Escapes raw HTML first to prevent XSS, then converts
    markdown syntax to HTML tags.
    """
    if not text:
        return Markup("")
    escaped = str(escape(text))
    html = md_lib.markdown(escaped, extensions=["nl2br"])
    return Markup(html)


templates.env.filters["md"] = md_filter


class AuthenticationRequired(Exception):  # noqa: N818
    """Exception raised when authentication is required."""

    def __init__(self, redirect_url: str = "/login"):
        self.redirect_url = redirect_url
        super().__init__("Authentication required")


async def get_db_session(
    request: Request,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Provide database session for request scope.

    Yields:
        AsyncSession: Database session with transaction handling.
    """
    if config.APP_MODE == "demo":
        if not getattr(request.state, "demo_boundary", False):
            raise DemoError("CONFIGURATION_INVALID")
        security = get_demo_security(request)
        bootstrap = await security.verified_bootstrap(request, issue=False)
        ctx = await security.context(request, bootstrap, None, None)
        # The handler boundary owns cleanup so failures are resolved before send.
        yield ctx.db
        return
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Get current user from session.

    Args:
        request: FastAPI Request object
        db: Database session

    Returns:
        User: User model instance

    Raises:
        HTTPException: If not authenticated
    """
    from src.models.user import User

    if config.APP_MODE == "demo":
        ctx = getattr(request.state, "demo_context", None)
        if ctx is None or ctx.db is not db:
            raise DemoError("CONFIGURATION_INVALID")
        await ctx._request_db.check()
        return ctx.user

    # Get user_id from session
    user_id = request.session.get("user_id")

    # Defensive: touch session to force cookie refresh
    # Ensures itsdangerous timestamp is updated on every request
    if user_id:
        request.session["_refreshed_at"] = int(time.time())

    if not user_id:
        raise AuthenticationRequired(redirect_url="/login")

    # Query user from database
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=translate(
                "teacher.user_not_found", display_language(request)
            ),
        )

    return user


async def get_admin_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Get current user and verify admin role.

    Args:
        request: FastAPI Request object
        db: Database session

    Returns:
        User: User model instance with admin role

    Raises:
        HTTPException: If not authenticated or not admin
    """
    user = await get_current_user(request, db)
    if not user.is_admin:
        if config.APP_MODE == "demo":
            raise DemoError("ACTION_FORBIDDEN")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=translate(
                "admin.access_required", display_language(request)
            ),
        )
    return user


async def _namespace(request: Request):
    """Remove only transport metadata before FastAPI parses strict bodies."""
    if hasattr(request.state, "demo_namespace"):
        return request.state.demo_namespace
    names = ("ds", "dg", "da")
    selectors = {"visitor_id", "user_id", "db_path", "database_url"}
    values = {name: [] for name in names}
    query = request.query_params
    request.state.demo_invalid_selector = bool(
        selectors.intersection(query)
    ) or any(
        name in request.headers
        for name in (
            "x-demo-visitor-id",
            "x-demo-user-id",
            "x-demo-db-path",
            "x-demo-database-url",
        )
    )
    for name, header in zip(
        names, ("x-demo-scope", "x-demo-generation", "x-demo-auth-epoch")
    ):
        values[name].extend(query.getlist(name))
        values[name].extend(request.headers.getlist(header))
    if request.url.path != "/demo":
        request.scope["query_string"] = urlencode(
            [
                (key, value)
                for key, value in query.multi_items()
                if key not in names
            ]
        ).encode()
        request._query_params = QueryParams(request.scope["query_string"])
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type in (
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    ):
        try:
            form = await request.form()
        except StarletteHTTPException:
            raise DemoError("INVALID_REQUEST") from None
        request.state.demo_invalid_selector |= bool(
            selectors.intersection(form)
        )
        form_names = ("_demo_scope", "_demo_generation", "_demo_auth_epoch")
        for name, field in zip(names, form_names):
            values[name].extend(form.getlist(field))
        request._form = FormData(
            [
                (key, value)
                for key, value in form.multi_items()
                if key not in form_names
            ]
        )
    request.state.demo_namespace = values
    return values


class _RequestSession:
    """Keep the generation factory's session, including its engine guards."""

    def __init__(self, state):
        self._state = state

    def __getattr__(self, name):
        self._state.ensure_open()
        return getattr(self._state.session, name)


class _RequestDBState:
    def __init__(self, runtime, lease, session):
        self.runtime, self.lease, self.session = runtime, lease, session
        self.ctx = None
        self.handed_off = self.released = False
        self.lock = asyncio.Lock()
        self.guard = self.handle = None
        self.guard_owner = None
        self.wrote = False

    def ensure_open(self):
        if self.handed_off or self.released:
            raise DemoError("STALE_GENERATION")

    async def check(self, *, write=False):
        self.ensure_open()
        if self.handle is not None:
            if self.guard_owner is not asyncio.current_task():
                raise DemoError("INVALID_REQUEST")
            self.handle.check(self.ctx)
        elif write:
            guard = self.runtime.visitors.write_guard(self.ctx)
            handle = await guard.__aenter__()
            self.guard, self.handle = guard, handle
            self.guard_owner = asyncio.current_task()
        else:
            await self.runtime.visitors.check(self.ctx)
        if write:
            self.wrote = True

    async def end_guard(self):
        if self.guard is not None:
            guard = self.guard
            try:
                await guard.__aexit__(None, None, None)
            finally:
                self.guard = self.handle = self.guard_owner = None

    async def watch_transactions(self):
        # Events also cover ORM autoflush and run_sync; callers cannot bypass DML guards.
        sync = self.session.sync_session

        def watch_connection(connection):
            @event.listens_for(connection, "before_cursor_execute")
            def before_cursor_execute(*args):
                self.ensure_open()

            @event.listens_for(connection, "commit")
            def before_connection_commit(*args):
                self.ensure_open()

        # The User lookup already began a transaction before these listeners exist.
        watch_connection((await self.session.connection()).sync_connection)

        @event.listens_for(sync, "after_begin")
        def after_begin(session, transaction, connection):
            if transaction.parent is None:
                watch_connection(connection)

        @event.listens_for(sync, "before_flush")
        def before_flush(*args):
            await_only(self.check(write=True))

        @event.listens_for(sync, "do_orm_execute")
        def before_execute(execution):
            statement = execution.statement
            readonly = execution.is_select or (
                isinstance(statement, TextClause)
                and str(statement).lstrip().upper().startswith("SELECT ")
            )
            await_only(self.check(write=not readonly))

        @event.listens_for(sync, "before_commit")
        def before_commit(*args):
            await_only(self.check(write=True))

        @event.listens_for(sync, "after_transaction_end")
        def after_transaction_end(session, transaction):
            if transaction.parent is None:
                await_only(self.end_guard())
                self.wrote = False


class DemoRequestDB:
    """Stage 3 binds this singleton at app.state.demo_request_db."""

    def __init__(self, visitors, lifecycle, *, seed_refs, settings=config):
        if (
            settings.APP_MODE != "demo"
            or lifecycle.visitors is not visitors
            or visitors.lifecycle is not lifecycle
            or any(
                type(seed_refs.get(role)) is not int
                for role in ("admin", "teacher")
            )
        ):
            raise DemoError("CONFIGURATION_INVALID")
        self.visitors, self.lifecycle = visitors, lifecycle
        self.seed_refs, self.settings = dict(seed_refs), settings
        # Unstarted bootstrap references must expire across registry restarts too.
        self._bootstrap_prefix = secrets.token_urlsafe(16) + "."
        self.session_signer = URLSafeTimedSerializer(
            settings.DEMO_SESSION_SECRET.get_secret_value(),
            salt="demo-session-v1",
        )
        self.csrf_signer = URLSafeTimedSerializer(
            settings.DEMO_CSRF_SECRET.get_secret_value(), salt="demo-csrf-v1"
        )
        self._windows = {}

    def rate_limit(self, key, limit):
        """§8 fixed 60-second window; Retry-After is the window's remainder."""
        now = self.visitors.clock.monotonic()
        if key not in self._windows:
            for stale in [
                k for k, v in self._windows.items() if now >= v[0] + 60
            ]:
                del self._windows[stale]
        start, count = self._windows.get(key, (now, 0))
        if now >= start + 60:
            start, count = now, 0
        if count >= limit:
            raise DemoError(
                "RATE_LIMITED", retry_after=max(1, math.ceil(start + 60 - now))
            )
        self._windows[key] = (start, count + 1)

    async def validate_namespace(
        self, request, bootstrap, generation=None, epoch=None
    ):
        visitor = self.visitors.visitor_for_bootstrap(bootstrap)
        if visitor is None:
            raise DemoError("SESSION_REQUIRED")
        snapshot = await self.visitors.snapshot(visitor)

        def reject(code):
            raise DemoError(
                code,
                generation=snapshot.generation,
                auth_epoch=snapshot.auth_epoch,
            )

        if snapshot.state in ("expired", "closing"):
            reject("EXPIRED")
        values = await _namespace(request)
        if request.state.demo_invalid_selector:
            reject("INVALID_REQUEST")
        values = {key: list(items) for key, items in values.items()}
        for key, value in (("dg", generation), ("da", epoch)):
            if value is not None:
                if type(value) is not int or value < 1:
                    reject("INVALID_REQUEST")
                values[key].append(str(value))
        supplied = any(values.values())
        if request.path_params and not all(values.values()):
            reject("STALE_GENERATION")
        if supplied and not all(values.values()) and generation is None:
            reject("STALE_GENERATION")
        expected = {
            "ds": snapshot.browser_scope_id,
            "dg": str(snapshot.generation),
            "da": str(snapshot.auth_epoch),
        }
        for key, items in values.items():
            for value in items:
                if not isinstance(value, str) or (
                    key != "ds"
                    and (
                        not value.isascii()
                        or not value.isdecimal()
                        or not value.strip("0")
                    )
                ):
                    reject("INVALID_REQUEST")
                if value != expected[key]:
                    reject("STALE_ROLE" if key == "da" else "STALE_GENERATION")
        return visitor, snapshot

    async def context(self, request, bootstrap, generation, epoch):
        existing = getattr(request.state, "demo_context", None)
        if existing is not None:
            await existing._request_db.check()
            return existing
        visitor, snapshot = await self.validate_namespace(
            request, bootstrap, generation, epoch
        )
        if (
            snapshot.state == "reset_failed"
            and request.url.path == "/demo/reset"
        ):
            return ControlContext(
                visitor, snapshot.generation, snapshot.auth_epoch
            )
        ref = await self.visitors.resolve_visit(visitor, snapshot.generation)
        lease = await self.visitors.acquire_lease(ref, snapshot.auth_epoch)
        state = _RequestDBState(self, lease, None)
        try:
            state.session = ref.factory()
            from src.models.user import User

            user = (
                await state.session.execute(
                    select(User).where(User.id == self.seed_refs[snapshot.role])
                )
            ).scalar_one_or_none()
            if user is None or user.role != snapshot.role:
                raise DemoError(
                    "ROLE_DELETED",
                    generation=snapshot.generation,
                    auth_epoch=snapshot.auth_epoch,
                )
            ctx = await self.visitors.make_context(
                visitor, lease, user_id=user.id
            )
            state.ctx = ctx
            object.__setattr__(ctx, "_request_db", state)
            object.__setattr__(ctx, "db", _RequestSession(state))
            object.__setattr__(ctx, "user", user)
            await state.watch_transactions()
            request.state.demo_context = ctx
            return ctx
        except BaseException:
            await self._release(state)
            raise

    def _state(self, ctx):
        state = getattr(ctx, "_request_db", None)
        if state is None or state.runtime is not self or state.ctx is not ctx:
            raise DemoError("CONFIGURATION_INVALID")
        return state

    def handoff_request_db(self, ctx):
        self._state(ctx).handed_off = True

    async def _release(self, state):
        async with state.lock:
            if state.released:
                return
            try:
                if state.session is not None:
                    await state.session.rollback()
            finally:
                if state.session is not None:
                    await state.session.close()
                await self.visitors.release_lease(state.lease)
                state.released = True

    async def release_request_db(self, ctx):
        if isinstance(ctx, ControlContext):
            return
        await self._release(self._state(ctx))

    async def finish(self, ctx):
        state = self._state(ctx)
        await state.check()
        if (
            state.wrote
            or state.session.new
            or state.session.dirty
            or state.session.deleted
        ):
            await state.session.commit()
        else:
            await state.session.rollback()
        await state.check()
        await self.visitors.touch(ctx)


class _DemoSecurity:
    def __init__(self, request, runtime):
        self.request, self.runtime = request, runtime
        self.bootstrap = None
        self.may_issue = False

    async def verified_bootstrap(self, request, *, issue):
        allowed = (
            request.method == "GET"
            and request.url.path in ("/demo", "/demo/state")
            and "request_id" not in request.query_params
        )
        if issue and not allowed:
            raise DemoError("INVALID_REQUEST")
        if self.bootstrap is not None:
            self.may_issue = self.may_issue or issue
            return self.bootstrap
        value = request.cookies.get("demo_session_id")
        prefix = self.runtime._bootstrap_prefix
        if value is None:
            if not issue:
                raise DemoError("SESSION_REQUIRED")
            nonce = prefix + secrets.token_urlsafe(32)
        else:
            try:
                nonce = self.runtime.session_signer.loads(value, max_age=28800)
                if (
                    not isinstance(nonce, str)
                    or not 32 <= len(nonce) <= 256
                    or not nonce.isascii()
                ):
                    raise ValueError
            except (BadData, ValueError, TypeError):
                raise DemoError("SESSION_REQUIRED") from None
            if not nonce.startswith(prefix):
                if not (issue and request.url.path == "/demo"):
                    raise DemoError("EXPIRED")
                nonce = prefix + secrets.token_urlsafe(32)
        self.bootstrap = self.runtime.visitors.verify_bootstrap(nonce)
        self.may_issue = issue
        return self.bootstrap

    async def revision(self, bootstrap):
        visitor = self.runtime.visitors.visitor_for_bootstrap(bootstrap)
        if visitor is None:
            return 0
        return (await self.runtime.visitors.snapshot(visitor)).csrf_revision

    async def verify_csrf(self, request, bootstrap):
        if bootstrap is not self.bootstrap:
            raise DemoError("SESSION_REQUIRED")
        origin = request.headers.getlist("origin")
        origin_ok = origin == [self.runtime.settings.DEMO_ORIGIN]
        if not origin and request.headers.get("content-type", "").split(";", 1)[
            0
        ] in ("application/x-www-form-urlencoded", "multipart/form-data"):
            try:
                referers = request.headers.getlist("referer")
                parsed = urlsplit(referers[0]) if len(referers) == 1 else None
                origin_ok = (
                    parsed is not None
                    and not parsed.username
                    and not parsed.password
                    and (
                        f"{parsed.scheme}://{parsed.netloc}"
                        == self.runtime.settings.DEMO_ORIGIN
                    )
                )
            except ValueError:
                origin_ok = False
        cookie = request.cookies.get("demo_csrftoken", "")
        headers = request.headers.getlist("x-csrf-token")
        try:
            if (
                not origin_ok
                or len(headers) != 1
                or not cookie
                or not hmac.compare_digest(cookie, headers[0])
            ):
                raise ValueError
            value = self.runtime.csrf_signer.loads(cookie, max_age=28800)
            if value != [
                bootstrap.bootstrap_nonce,
                await self.revision(bootstrap),
            ]:
                raise ValueError
        except (BadData, ValueError, TypeError):
            raise DemoError("CSRF_INVALID") from None

    async def cookies(self, bootstrap):
        from src.api.routes.demo import CookieValues

        if not self.may_issue or bootstrap is not self.bootstrap:
            raise DemoError("INVALID_REQUEST")
        settings = self.runtime.settings
        secure = not (
            settings.ENV == "development"
            and urlsplit(settings.DEMO_ORIGIN).scheme == "http"
        )
        return CookieValues(
            self.runtime.session_signer.dumps(bootstrap.bootstrap_nonce),
            self.runtime.csrf_signer.dumps(
                [bootstrap.bootstrap_nonce, await self.revision(bootstrap)]
            ),
            secure,
        )

    async def context(
        self, request, bootstrap, expected_generation, expected_auth_epoch
    ):
        if bootstrap is not self.bootstrap:
            raise DemoError("SESSION_REQUIRED")
        return await self.runtime.context(
            request, bootstrap, expected_generation, expected_auth_epoch
        )


def _demo_runtime(request):
    runtime = getattr(request.app.state, "demo_request_db", None)
    if config.APP_MODE != "demo" or not isinstance(runtime, DemoRequestDB):
        raise DemoError("CONFIGURATION_INVALID")
    return runtime


def get_demo_security(request: Request):
    runtime = _demo_runtime(request)
    if not hasattr(request.state, "demo_security"):
        request.state.demo_security = _DemoSecurity(request, runtime)
    return request.state.demo_security


def get_demo_session(request: Request):
    runtime = _demo_runtime(request)
    session = getattr(request.app.state, "demo_session", None)
    if (
        session is None
        or session.visitors is not runtime.visitors
        or session.lifecycle is not runtime.lifecycle
        or session.request_db is not runtime
    ):
        raise DemoError("CONFIGURATION_INVALID")
    return session


def demo_request_handler(handler):
    """Stage 3 wraps all demo route handlers before body/dependency parsing."""

    @wraps(handler)
    async def guarded(request):
        if config.APP_MODE != "demo":
            return await handler(request)
        from src.api.routes.demo import error_response

        runtime = None
        response = None
        request.state.demo_boundary = True
        try:
            runtime = _demo_runtime(request)
            security = get_demo_security(request)
            bootstrap = await security.verified_bootstrap(
                request,
                issue=(
                    request.method == "GET"
                    and request.url.path in ("/demo", "/demo/state")
                    and "request_id" not in request.query_params
                ),
            )
            visitor = runtime.visitors.visitor_for_bootstrap(bootstrap)
            if visitor is not None:
                runtime.rate_limit(
                    ("visitor", visitor.visitor_id),
                    runtime.settings.DEMO_REQUESTS_PER_MINUTE,
                )
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                await security.verify_csrf(request, bootstrap)
            await _namespace(request)
            if request.state.demo_invalid_selector:
                raise DemoError("INVALID_REQUEST")
            if request.method == "POST" and request.url.path == "/demo/start":
                # Counts signed, CSRF-valid starts by the ASGI client, which only
                # a trusted proxy (server --forwarded-allow-ips) may rewrite.
                runtime.rate_limit(
                    ("ip", request.client.host if request.client else ""),
                    runtime.settings.DEMO_STARTS_PER_IP_PER_MINUTE,
                )
            # Controls resolve namespace only after their receipt/replay lookup.
            if request.url.path != "/demo" and not request.url.path.startswith(
                "/demo/"
            ):
                await runtime.validate_namespace(request, bootstrap)
            response = await handler(request)
            ctx = getattr(request.state, "demo_context", None)
            if ctx is not None and not request.url.path.startswith("/demo/"):
                if response.status_code < 400:
                    await runtime.finish(ctx)
            response.headers["Cache-Control"] = "no-store"
        except DemoError as error:
            response = error_response(error)
        except RequestValidationError:
            response = error_response(DemoError("INVALID_REQUEST"))
        except StarletteHTTPException as error:
            # Render while this request's lease is live; error pages read no other DB.
            response = await request.app.exception_handlers[
                StarletteHTTPException
            ](request, error)
        except Exception as error:
            # §11: unknown failures become a safe code; text may carry real paths.
            logging.getLogger(__name__).error(
                "Demo request failed", extra={"code": type(error).__name__}
            )
            response = error_response(DemoError("CONFIGURATION_INVALID"))
        finally:
            ctx = getattr(request.state, "demo_context", None)
            if ctx is not None and runtime is not None:
                try:
                    await runtime.release_request_db(ctx)
                except Exception:
                    # A failed close keeps its lease; never send success for it.
                    if response is None or response.status_code < 400:
                        response = error_response(
                            DemoError("CONFIGURATION_INVALID")
                        )
        if (
            request.method != "GET"
            or request.url.path not in ("/demo", "/demo/state")
            or response.status_code != 200
            or "request_id" in request.query_params
        ):
            if "set-cookie" in response.headers:
                del response.headers["set-cookie"]
        return response

    return guarded


class DemoRequestRoute(APIRoute):
    def get_route_handler(self):
        return demo_request_handler(super().get_route_handler())
