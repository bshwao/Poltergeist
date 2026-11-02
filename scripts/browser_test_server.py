"""Seed an isolated synthetic workspace, then serve it for opt-in browser tests."""

import argparse
import json
import os
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
    assistant_fixture = os.environ.get("POLTERGEIST_ASSISTANT_FIXTURE") == "1"
    if not assistant_fixture and validate_installation(["--mode", "test", "--sensors", "--duration", ".15", "--seeds", "7", "--output", str(directory)]):
        return 1
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(directory / 'database' / 'runs.db').as_posix()}",
        storage_dir=directory / "artifacts", api_host="127.0.0.1", api_port=args.port,
        cors_origins=[args.origin], simulation_shutdown_timeout_s=5.)
    if assistant_fixture:
        # S18 reviews saved baseline evidence; its fixture does not require a
        # separate SDK package or the installation benchmark to succeed first.
        from fastapi.testclient import TestClient
        from threading import Event
        from time import monotonic
        with TestClient(create_app(settings)) as seed:
            launched = seed.post("/simulations", json={"mode": "test", "scenario": {
                "name": "S18 saved baseline evidence", "duration_s": .15,
                "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 2.}]}})
            launched.raise_for_status()
            identity = launched.json()["simulation"]["id"]
            deadline = monotonic() + 30
            while True:
                state = seed.get(f"/simulations/{identity}").json()["simulation"]["status"]
                if state == "completed":
                    break
                if state in {"failed", "cancelled"} or monotonic() >= deadline:
                    raise RuntimeError("S18 baseline fixture did not complete")
                Event().wait(.01)
    application = create_app(settings)
    if assistant_fixture:
        # Explicit test-only provider: real disclosure, journal and plan routes,
        # without a credential, external API call or paid model request.
        from pydantic import SecretStr
        from backend.routes.assistant import service
        from backend.services.assistant_service import AssistantService
        settings.openai_enabled = True
        settings.openai_api_key = SecretStr("local-browser-fixture-never-sent")
        settings.openai_model = "browser-fixture"
        def provider(payload, key):
            body = json.loads(payload)
            disclosed = json.loads(body["input"])
            name = body["tools"][0]["name"]
            arguments = ({"template": disclosed["current_template"], "failure_kind": disclosed["current_failure_kind"]}
                         if name == "draft_discovery" else {"observation_ids": [disclosed["observations"][0]["id"]]})
            return {"status": "completed", "output": [{"type": "function_call", "name": name, "arguments": json.dumps(arguments)}],
                    "usage": {"input_tokens": 40, "output_tokens": 16}}
        application.dependency_overrides[service] = lambda: AssistantService(settings, application.state.runtime.persistence, provider=provider)
    uvicorn.run(application, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
