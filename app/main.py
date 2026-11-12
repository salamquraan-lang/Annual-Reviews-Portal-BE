import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.errors import register_error_handlers
from app.mailer import Mailer, create_mailer
from app.rate_limit import rate_limit_middleware
from app.routers import auth, cycles, departments, users


async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    return response


def create_app(mailer: Mailer | None = None) -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="Annual Reviews Portal API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.mailer = mailer or create_mailer()

    register_error_handlers(app)
    app.middleware("http")(rate_limit_middleware)
    app.middleware("http")(security_headers)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.app_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(departments.router)
    app.include_router(cycles.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
app = create_app()
