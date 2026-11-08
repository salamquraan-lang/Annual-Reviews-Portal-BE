import math
import threading
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from app.config import get_settings
from app.errors import AppError, error_response


class RateLimiter:
    """Fixed-window counters kept in memory. Good for a single server process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[float, int]] = {}

    def retry_after(self, key: str, limit: int, window: float) -> int | None:
        with self._lock:
            started, count = self._hits.get(key, (0.0, 0))
            if time.monotonic() - started >= window or count < limit:
                return None
            return max(1, math.ceil(window - (time.monotonic() - started)))

    def hit(self, key: str, window: float) -> None:
        with self._lock:
            now = time.monotonic()
            started, count = self._hits.get(key, (now, 0))
            if now - started >= window:
                started, count = now, 0
            self._hits[key] = (started, count + 1)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def reset_prefix(self, prefix: str) -> None:
        with self._lock:
            for key in [k for k in self._hits if k.startswith(prefix)]:
                del self._hits[key]


limiter = RateLimiter()


def too_many_requests(retry_after: int) -> AppError:
    return AppError(
        429,
        "TOO_MANY_REQUESTS",
        "Too many requests. Try again later.",
        headers={"Retry-After": str(retry_after)},
    )


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


AUTH_LIMITED_PATHS = (
    "/api/auth/signup",
    "/api/auth/verify-email",
    "/api/auth/resend-verification",
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/change-password",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
)


async def rate_limit_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    path = request.url.path
    if not path.startswith("/api/") or path.startswith("/api/docs") or path == "/api/health":
        return await call_next(request)

    settings = get_settings()
    window = settings.rate_limit_window_minutes * 60
    ip = client_ip(request)
    api_key = f"api:{ip}"
    auth_key = f"auth:{ip}"
    is_auth = path.startswith(AUTH_LIMITED_PATHS)

    for key, limit in ((api_key, settings.rate_limit_max), (auth_key, settings.auth_rate_limit_max)):
        if key == auth_key and not is_auth:
            continue
        wait = limiter.retry_after(key, limit, window)
        if wait:
            return error_response(too_many_requests(wait))

    limiter.hit(api_key, window)
    response = await call_next(request)
    # Only failed auth requests count, so users who log in correctly are never blocked.
    if is_auth and response.status_code >= 400:
        limiter.hit(auth_key, window)
    return response
