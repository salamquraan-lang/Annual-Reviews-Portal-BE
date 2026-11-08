import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self, status: int, code: str, message: str, details: Any = None, headers: dict | None = None
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def bad_request(code: str, message: str) -> AppError:
    return AppError(400, code, message)


def unauthorized(message: str = "Authentication required.") -> AppError:
    return AppError(401, "UNAUTHORIZED", message)


def forbidden(code: str = "FORBIDDEN", message: str = "You are not allowed to do this.") -> AppError:
    return AppError(403, code, message)


def not_found(message: str = "Not found.") -> AppError:
    return AppError(404, "NOT_FOUND", message)


def error_response(err: AppError) -> JSONResponse:
    body: dict[str, Any] = {"code": err.code, "message": err.message}
    if err.details:
        body["details"] = err.details
    return JSONResponse({"error": body}, status_code=err.status, headers=err.headers)


def _field_errors(exc: RequestValidationError) -> list[dict[str, str]]:
    fields = []
    for e in exc.errors():
        loc = [str(p) for p in e["loc"] if p not in ("body", "query", "path")]
        message = str(e["msg"]).removeprefix("Value error, ")
        if e["type"] == "missing":
            message = "This field is required."
        fields.append({"field": ".".join(loc), "message": message})
    return fields


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        if any(e["type"] == "json_invalid" for e in exc.errors()):
            return error_response(bad_request("INVALID_JSON", "Request body is not valid JSON."))
        return error_response(
            AppError(400, "VALIDATION_ERROR", "Some fields are invalid.", _field_errors(exc))
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return error_response(not_found())
        if exc.status_code == 405:
            return error_response(AppError(405, "METHOD_NOT_ALLOWED", "Method not allowed."))
        return error_response(AppError(exc.status_code, "HTTP_ERROR", str(exc.detail)))

    @app.exception_handler(Exception)
    async def handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return error_response(AppError(500, "INTERNAL_ERROR", "Something went wrong."))
