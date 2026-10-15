"""Limit comparison bodies before parsing and never echo submitted input."""

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from src.api.dependencies import AuthenticationRequired
from src.i18n import display_language, translate

REQUEST_LIMIT = 2 * 1024 * 1024


class ComparisonRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request):
            if request.method == "POST":
                chunks, size = [], 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > REQUEST_LIMIT:
                        return JSONResponse(
                            {"detail": {"code": "request_too_large"}},
                            status_code=413,
                        )
                    chunks.append(chunk)
                request._body = b"".join(chunks)
                try:
                    request._body.decode("utf-8")
                except UnicodeError:
                    return JSONResponse(
                        {"detail": {"code": "invalid_utf8"}}, status_code=422
                    )
            try:
                return await handler(request)
            except AuthenticationRequired:
                return JSONResponse(
                    {"detail": {"code": "authentication_required"}},
                    status_code=401,
                )
            except HTTPException as exc:
                if isinstance(exc.detail, dict) and "errors" in exc.detail:
                    exc.detail = {
                        **exc.detail,
                        "errors": [
                            {
                                **item,
                                "message": translate(
                                    "admin.comparison_input_error",
                                    display_language(request),
                                ),
                            }
                            for item in exc.detail["errors"]
                        ],
                    }
                raise
            except RequestValidationError as error:
                return JSONResponse(
                    {
                        "detail": {
                            "code": "invalid_input",
                            "errors": [
                                dict(
                                    field=".".join(
                                        str(part)
                                        for part in item["loc"]
                                        if part != "body"
                                    ),
                                    message=translate(
                                        "admin.input_format",
                                        display_language(request),
                                    ),
                                )
                                for item in error.errors()
                            ],
                        }
                    },
                    status_code=422,
                )

        return bounded
