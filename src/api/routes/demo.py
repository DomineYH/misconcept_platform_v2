"""Standalone demo controls. #194 owns registration and verified adapters."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.dependencies import templates
from src.demo.database import ERRORS, DemoError
from src.demo.session import (
    ControlContext,
    ControlResult,
    DemoContext,
    DemoSession,
    ResetCommand,
    RoleCommand,
    Schema,
    StartCommand,
)


@dataclass(frozen=True)
class CookieValues:
    session_id: str
    csrf_token: str
    secure: bool


class DemoSecurity(Protocol):
    async def verified_bootstrap(
        self, request: Request, *, issue: bool
    ) -> object:
        """Verify signature first; only safe GETs may create anonymous state."""
        ...

    async def verify_csrf(self, request: Request, bootstrap: object) -> None:
        """Verify origin, double-submit signature/nonce/current revision."""
        ...

    async def cookies(self, bootstrap: object) -> CookieValues:
        """Signed current revision; Secure=false only for validated loopback dev."""
        ...

    async def context(
        self,
        request: Request,
        bootstrap: object,
        expected_generation: int | None,
        expected_auth_epoch: int | None,
    ) -> DemoContext | ControlContext:
        """Acquire a verified lease, or DB-free ControlContext for reset_failed."""
        ...


def get_demo_security() -> DemoSecurity:
    raise DemoError("CONFIGURATION_INVALID")


def get_demo_session() -> DemoSession:
    raise DemoError("CONFIGURATION_INVALID")


def error_response(error: DemoError) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if error.retry_after is not None:
        headers["Retry-After"] = str(error.retry_after)
    return JSONResponse({"detail": error.detail}, error.status_code, headers)


class DemoRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                response = await handler(request)
            except DemoError as exc:
                response = error_response(exc)
            except (ValidationError, RequestValidationError, ValueError):
                response = error_response(DemoError("INVALID_REQUEST"))
            except HTTPException as exc:
                detail = exc.detail
                if (
                    isinstance(detail, dict)
                    and detail.get("code", "").removeprefix("DEMO_") in ERRORS
                ):
                    response = error_response(
                        DemoError(
                            detail["code"],
                            generation=detail.get("generation"),
                            auth_epoch=detail.get("auth_epoch"),
                            retry_after=(
                                int(exc.headers["Retry-After"])
                                if exc.headers and "Retry-After" in exc.headers
                                else None
                            ),
                        )
                    )
                else:
                    response = error_response(
                        DemoError("CONFIGURATION_INVALID")
                    )
            response.headers["Cache-Control"] = "no-store"
            if (
                request.method != "GET"
                or response.status_code != 200
                or "request_id" in request.query_params
            ):
                if "set-cookie" in response.headers:
                    del response.headers["set-cookie"]
            return response

        return guarded


router = APIRouter(route_class=DemoRoute, tags=["Demo"])


async def command_body(request: Request, schema: type[Schema]) -> Schema:
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type == "application/json":
        value = await request.json()
    elif content_type in (
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    ):
        try:
            form = await request.form()
        except StarletteHTTPException:
            raise DemoError("INVALID_REQUEST") from None
        if len(form.multi_items()) != len(form):
            raise DemoError("INVALID_REQUEST")
        value = dict(form)
        for key in ("expected_generation", "expected_auth_epoch"):
            if key in value and isinstance(value[key], str):
                if not value[key].isascii() or not value[key].isdecimal():
                    raise DemoError("INVALID_REQUEST")
                value[key] = int(value[key])
        if "confirm" in value:
            if value["confirm"] != "true":
                raise DemoError("INVALID_REQUEST")
            value["confirm"] = True
    else:
        raise DemoError("INVALID_REQUEST")
    return schema.model_validate(value)


async def synchronized(
    response: Response,
    security: DemoSecurity,
    bootstrap: object,
) -> Response:
    values = await security.cookies(bootstrap)
    response.set_cookie(
        "demo_session_id",
        values.session_id,
        max_age=28800,
        path="/",
        secure=values.secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        "demo_csrftoken",
        values.csrf_token,
        path="/",
        secure=values.secure,
        httponly=False,
        samesite="lax",
    )
    return response


def control_response(result: ControlResult) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if result.retry_after is not None:
        headers["Retry-After"] = str(result.retry_after)
    return JSONResponse(result.body.model_dump(), result.status, headers)


@router.get("/demo")
async def demo_entry(
    request: Request,
    security: DemoSecurity = Depends(get_demo_security),
):
    if request.query_params:
        raise DemoError("INVALID_REQUEST")
    bootstrap = await security.verified_bootstrap(request, issue=True)
    response = templates.TemplateResponse("demo.html", {"request": request})
    return await synchronized(response, security, bootstrap)


@router.get("/demo/state")
async def demo_state(
    request: Request,
    security: DemoSecurity = Depends(get_demo_security),
    session: DemoSession = Depends(get_demo_session),
):
    params = request.query_params
    request_id = params.get("request_id")
    bootstrap = await security.verified_bootstrap(
        request, issue=request_id is None
    )
    if set(params) - {"request_id", "include_tour"} or len(
        params.multi_items()
    ) != len(params):
        raise DemoError("INVALID_REQUEST")
    if request_id is not None:
        StartCommand(request_id=request_id, role="admin")
    include = params.get("include_tour", "false")
    if include not in ("true", "false"):
        raise DemoError("INVALID_REQUEST")
    result = await session.state(
        bootstrap,
        request_id,
        include_tour=include == "true",
        context=lambda: security.context(request, bootstrap, None, None),
    )
    response = control_response(result)
    if request_id is None:
        return await synchronized(response, security, bootstrap)
    return response


@router.post("/demo/start")
async def demo_start(
    request: Request,
    security: DemoSecurity = Depends(get_demo_security),
    session: DemoSession = Depends(get_demo_session),
):
    bootstrap = await security.verified_bootstrap(request, issue=False)
    await security.verify_csrf(request, bootstrap)
    command = await command_body(request, StartCommand)
    return control_response(await session.start(bootstrap, command))


@router.post("/demo/role")
async def demo_role(
    request: Request,
    security: DemoSecurity = Depends(get_demo_security),
    session: DemoSession = Depends(get_demo_session),
):
    bootstrap = await security.verified_bootstrap(request, issue=False)
    await security.verify_csrf(request, bootstrap)
    command = await command_body(request, RoleCommand)
    return control_response(
        await session.role(
            bootstrap,
            command,
            lambda: security.context(
                request,
                bootstrap,
                command.expected_generation,
                command.expected_auth_epoch,
            ),
        )
    )


@router.post("/demo/reset")
async def demo_reset(
    request: Request,
    security: DemoSecurity = Depends(get_demo_security),
    session: DemoSession = Depends(get_demo_session),
):
    bootstrap = await security.verified_bootstrap(request, issue=False)
    await security.verify_csrf(request, bootstrap)
    command = await command_body(request, ResetCommand)
    return control_response(
        await session.reset(
            bootstrap,
            command,
            lambda: security.context(
                request,
                bootstrap,
                command.expected_generation,
                command.expected_auth_epoch,
            ),
        )
    )
