"""FastAPI application factory and ASGI entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API without connecting to external services or creating files."""

    settings = settings if settings is not None else get_settings()
    application = FastAPI(
        title="Poltergeist",
        description="Driving-safety simulation and failure discovery.",
        version="0.1.0",
    )
    application.state.settings = settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Process liveness only; CARLA and database readiness are not checked."""

        return {"status": "ok", "service": "poltergeist"}

    return application


app = create_app()
