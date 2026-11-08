import uvicorn

from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=settings.port,
        reload=not settings.is_production,
        # X-Forwarded-For is only trusted when the app really runs behind a proxy.
        proxy_headers=settings.trust_proxy,
    )
