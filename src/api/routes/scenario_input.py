"""Bound and safely report scenario input at the existing HTTP boundary."""

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from src.api.dependencies import AuthenticationRequired
from src.i18n import display_language, translate
from src.i18n.admin import review_notice

REQUEST_LIMIT = 1024 * 1024


class ScenarioRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request):
            if request.method == "POST":
                chunks, size = [], 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > REQUEST_LIMIT:
                        return JSONResponse(
                            {
                                "detail": [
                                    {
                                        "path": "",
                                        "code": "request_too_large",
                                        "message": translate(
                                            "admin.request_size",
                                            display_language(request),
                                        ),
                                    }
                                ]
                            },
                            status_code=422,
                        )
                    chunks.append(chunk)
                request._body = b"".join(chunks)
            try:
                return await handler(request)
            except AuthenticationRequired:
                if request.method != "POST":
                    raise
                return JSONResponse(
                    {
                        "detail": translate(
                            "admin.authentication_required",
                            display_language(request),
                        )
                    },
                    status_code=401,
                )
            except HTTPException as exc:
                if isinstance(exc.detail, list):
                    exc.detail = [
                        review_notice(item, display_language(request))
                        for item in exc.detail
                    ]
                elif exc.status_code == 404 and isinstance(exc.detail, str):
                    exc.detail = translate(
                        "admin.scenario_not_found", display_language(request)
                    )
                raise
            except RequestValidationError as exc:
                return JSONResponse(
                    {
                        "detail": [
                            {
                                "path": ".".join(
                                    str(part)
                                    for part in error["loc"]
                                    if part not in ("body", "draft", "legacy")
                                ),
                                "code": error["type"],
                                "message": translate(
                                    "admin.input_format",
                                    display_language(request),
                                ),
                            }
                            for error in exc.errors()
                        ]
                    },
                    status_code=422,
                )

        return bounded
