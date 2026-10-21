"""FastAPI application factory and ASGI entry point."""

from contextlib import asynccontextmanager
import logging

from anyio import to_thread
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from backend.config import Settings, get_settings
from backend.dependencies import ApiRuntime
from backend.persistence_errors import ConflictError, DatabaseInitializationError, NotFoundError, PersistenceError
from backend.services.simulation_service import QueueFullError, QueueUnavailableError
from backend.storage import ArtifactError
from backend.routes import agents, analytics, behavior, failures, scenarios, simulations, optimizations, reports, readiness
from optimization.bayesian_search import OptimizationDependencyError

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, runner_factory=None) -> FastAPI:
    """Build the API without connecting to external services or creating files."""

    settings = settings if settings is not None else get_settings()
    runtime = ApiRuntime(settings, runner_factory=runner_factory)

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            await to_thread.run_sync(runtime.close)

    application = FastAPI(
        title="Poltergeist",
        description="Driving-safety simulation and failure discovery.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.runtime = runtime
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Poltergeist-Report-Schema"],
    )

    def handler(code, message=None):
        async def respond(request: Request, error: Exception):
            if code >= 500:
                logger.error("API service error: %s", error, exc_info=error)
            return JSONResponse(status_code=code, content={"detail": str(error) if message is None else message})
        return respond

    for error_type, code, message in (
        (NotFoundError, 404, None), (ConflictError, 409, None),
        (QueueFullError, 429, None), (QueueUnavailableError, 503, None),
        (OptimizationDependencyError, 503, None),
        (DatabaseInitializationError, 503, "Persistence initialization failed"),
        (PersistenceError, 422, None), (ArtifactError, 503, "Stored artifact is unavailable or invalid"),
        (OSError, 503, "Persistence is unavailable"),
        (ValidationError, 500, "Stored data is invalid"), (ValueError, 422, None),
    ):
        application.add_exception_handler(error_type, handler(code, message))

    for router in (scenarios.router, simulations.router, failures.router, analytics.router, optimizations.router, reports.router, agents.router, behavior.router, readiness.router):
        application.include_router(router)

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Process liveness only; CARLA and database readiness are not checked."""

        return {"status": "ok", "service": "poltergeist"}

    return application


app = create_app()
