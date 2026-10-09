"""Bound and safely report scenario input at the existing HTTP boundary."""

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from src.api.dependencies import AuthenticationRequired

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
                                        "message": "요청은 1 MiB 이하여야 합니다.",
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
                    {"detail": "Authentication required"}, status_code=401
                )
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
                                "message": error["msg"],
                            }
                            for error in exc.errors()
                        ]
                    },
                    status_code=422,
                )

        return bounded
