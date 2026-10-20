"""Seed an isolated synthetic workspace, then serve it for opt-in browser tests."""

import argparse
from pathlib import Path
from uuid import uuid4

import uvicorn

from backend.config import Settings
from backend.main import create_app
from scripts.validate_installation import main as validate_installation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8049)
    parser.add_argument("--origin", default="http://127.0.0.1:5189")
    parser.add_argument("--output-root", type=Path, default=Path(".pytest_cache/browser"))
    args = parser.parse_args(argv)
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    directory = root / str(uuid4())
    if not directory.is_relative_to(root):
        raise ValueError("Browser evidence must stay inside its output root")
    if validate_installation(["--mode", "test", "--sensors", "--duration", ".15", "--seeds", "7", "--output", str(directory)]):
        return 1
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(directory / 'database' / 'runs.db').as_posix()}",
        storage_dir=directory / "artifacts", api_host="127.0.0.1", api_port=args.port,
        cors_origins=[args.origin], simulation_shutdown_timeout_s=5.)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
