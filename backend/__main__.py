"""Run the API with the configured host and port: ``python -m backend``."""

import uvicorn

from backend.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("backend.main:app", host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
