import logging
from contextlib import asynccontextmanager
from time import perf_counter
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.auth import router as auth_router
from app.api.domains import router as domains_router
from app.api.scans import router as scans_router
from app.api.assets import router as assets_router
from app.core.config import Settings, get_settings
from app.core.logging_config import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        logger.info(
            "Application startup complete: %s",
            app_settings.app_name,
            extra={"event": "application.start", "app_name": app_settings.app_name},
        )
        yield
        logger.info(
            "Application shutdown complete: %s",
            app_settings.app_name,
            extra={"event": "application.stop", "app_name": app_settings.app_name},
        )

    application = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        lifespan=lifespan,
    )
    application.include_router(auth_router)
    application.include_router(domains_router)
    application.include_router(scans_router)
    application.include_router(assets_router)

    @application.middleware("http")
    async def log_request(request: Request, call_next):
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:
            logger.exception(
                "Unhandled request failure",
                extra={
                    "event": "http.request.error",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                    "error_type": type(error).__name__,
                },
            )
            raise
        logger.info(
            "HTTP request completed",
            extra={
                "event": "http.request",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            },
        )
        return response

    @application.exception_handler(HTTPException)
    async def handle_http_error(
        request: Request,
        error: HTTPException,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": error.detail},
            headers=error.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        del request
        sanitized_errors = [
            {
                "loc": list(item.get("loc", ())),
                "msg": "Invalid request field",
                "type": item.get("type", "value_error"),
            }
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Request validation failed",
                "errors": sanitized_errors,
            },
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        logger.error(
            "Unhandled application error",
            extra={
                "event": "application.error",
                "method": request.method,
                "path": request.url.path,
                "error_type": type(error).__name__,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    @application.get("/health", tags=["health"])
    def health_check(db: Session = Depends(get_db)) -> dict[str, str]:
        try:
            db.execute(text("SELECT 1"))
        except SQLAlchemyError as error:
            logger.warning("Readiness check failed: database unavailable")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database unavailable",
            ) from error
        return {
            "status": "ok",
            "database": "connected",
        }

    return application


app = create_app()
