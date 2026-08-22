from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import SQLAlchemyError
from starlette.staticfiles import StaticFiles

from hoardarr import __version__
from hoardarr.api.metrics import create_api_metrics
from hoardarr.api.problem import Problem, handle_problem, handle_validation_error, problem_response
from hoardarr.api.rate_limit import AttemptLimiter
from hoardarr.api.routes import (
    accounts,
    addons,
    auth,
    connectivity,
    hardware,
    integrations,
    networking,
    onboarding,
    operations,
    storage,
    system,
    telemetry,
    updates,
    wizard,
)
from hoardarr.core.config import Settings, get_settings
from hoardarr.core.secrets import SecretBox
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import database_is_current
from hoardarr.telemetry.service import TelemetryService

LOGGER = logging.getLogger(__name__)


class RequestBodyTooLarge(RuntimeError):
    pass


def create_app(settings: Settings | None = None) -> FastAPI:
    effective_settings = settings or get_settings()
    engine = create_database_engine(effective_settings.database_url)
    database_ready = False
    try:
        database_ready = database_is_current(engine, effective_settings.database_url)
    except SQLAlchemyError:
        LOGGER.exception("Could not inspect the Hoardarr database migration state")

    secret_box = SecretBox.from_file(
        effective_settings.secret_key_file,
        create=effective_settings.environment in {"development", "test"},
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Hoardarr API",
        summary="Storage lifecycle control plane",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = effective_settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.database_ready = database_ready
    app.state.frontend_available = (effective_settings.frontend_dir / "index.html").is_file()
    app.state.secret_box = secret_box
    app.state.login_limiter = AttemptLimiter(attempts=5)
    app.state.login_ip_limiter = AttemptLimiter(attempts=20)
    app.state.setup_limiter = AttemptLimiter(attempts=5)
    app.state.authentication_slots = threading.BoundedSemaphore(
        effective_settings.authentication_concurrency
    )
    app.state.metrics = create_api_metrics()
    app.state.telemetry_service = TelemetryService(effective_settings)

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = str(uuid.uuid4())
        started = time.monotonic()
        maximum = app.state.settings.max_request_body_bytes
        content_length = request.headers.get("content-length")
        try:
            declared_size = int(content_length) if content_length is not None else None
        except ValueError:
            declared_size = None
        try:
            if declared_size is not None and declared_size > maximum:
                raise RequestBodyTooLarge
            payload = bytearray()
            async for chunk in request.stream():
                payload.extend(chunk)
                if len(payload) > maximum:
                    raise RequestBodyTooLarge
            # Starlette's cached request replays this bounded body to FastAPI's
            # parser. Pre-buffering is intentional: it enforces the same limit
            # for Content-Length and chunked requests before validation begins.
            request._body = bytes(payload)
            response = await call_next(request)
        except RequestBodyTooLarge:
            response = problem_response(
                request,
                Problem(
                    413,
                    "request_too_large",
                    "Request too large",
                    f"Request bodies are limited to {maximum} bytes.",
                ),
            )
        except Exception as exc:
            LOGGER.error(
                "Unhandled API error request_id=%s exception=%s",
                request.state.request_id,
                type(exc).__name__,
            )
            response = problem_response(
                request,
                Problem(
                    500,
                    "internal_error",
                    "Internal server error",
                    "The request could not be completed.",
                ),
            )
        route = request.scope.get("route")
        route_name = getattr(route, "path", "unmatched")
        app.state.metrics.requests.labels(
            request.method,
            route_name,
            str(response.status_code),
        ).inc()
        app.state.metrics.duration.labels(request.method, route_name).observe(
            time.monotonic() - started
        )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path != "/api/docs":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
            )
        return response

    app.add_exception_handler(Problem, handle_problem)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]

    @app.get("/health/live", include_in_schema=False)
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", include_in_schema=False)
    def ready(request: Request) -> dict[str, str]:
        try:
            request.app.state.database_ready = database_is_current(
                request.app.state.engine,
                request.app.state.settings.database_url,
            )
        except SQLAlchemyError:
            request.app.state.database_ready = False
        if not request.app.state.database_ready:
            raise Problem(
                503,
                "database_not_ready",
                "Service not ready",
                "Database migrations have not been applied.",
            )
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(
            generate_latest(app.state.metrics.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    prefix = "/api/v1"
    app.include_router(auth.setup_router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(accounts.router, prefix=prefix)
    app.include_router(addons.router, prefix=prefix)
    app.include_router(system.router, prefix=prefix)
    app.include_router(telemetry.router, prefix=prefix)
    app.include_router(onboarding.router, prefix=prefix)
    app.include_router(operations.router, prefix=prefix)
    app.include_router(hardware.router, prefix=prefix)
    app.include_router(storage.router, prefix=prefix)
    app.include_router(connectivity.router, prefix=prefix)
    app.include_router(networking.router, prefix=prefix)
    app.include_router(integrations.router, prefix=prefix)
    app.include_router(wizard.router, prefix=prefix)
    app.include_router(updates.router, prefix=prefix)
    if app.state.frontend_available:
        app.mount(
            "/",
            StaticFiles(directory=effective_settings.frontend_dir, html=True),
            name="frontend",
        )
    return app
