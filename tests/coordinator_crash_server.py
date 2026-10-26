"""Private stdio-controlled coordinator process for native restart fault tests.

No fixture control endpoints are added to the production ASGI application.
Hard exits happen in the actual Python process, including on Windows venvs.
"""

import json
import os
from pathlib import Path
import sys
from threading import Thread
from time import monotonic, sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coordinator.app import create_app
from coordinator.settings import CoordinatorSettings
import uvicorn


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    config = json.loads(sys.stdin.readline())
    app = create_app(CoordinatorSettings(Path(config["database"]), config["origin"]))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=config["port"],
        log_level="critical", access_log=False, proxy_headers=False,
        ssl_certfile=config["cert"], ssl_keyfile=config["key"], ws_max_size=262144,
        ws_ping_interval=None, timeout_graceful_shutdown=5))
    thread = Thread(target=server.run, daemon=True)
    try:
        thread.start()
        deadline = monotonic() + 25
        while not server.started:
            if not thread.is_alive() or monotonic() >= deadline:
                raise RuntimeError("The fixture coordinator did not start")
            sleep(.01)
        original = app.state.commands.reply

        def reply(credential, identity, message):
            selected = message.kind == config.get("kind")
            if selected and config.get("fault") == "before_commit":
                emit({"event": "fault", "boundary": "before_commit", "reply": message.model_dump(mode="json")})
                os._exit(79)
            result = original(credential, identity, message)
            if selected and config.get("fault") == "after_commit":
                emit({"event": "fault", "boundary": "after_commit", "reply": message.model_dump(mode="json")})
                os._exit(79)
            return result

        app.state.commands.reply = reply
        emit({"event": "ready", "pid": os.getpid()})
        for line in sys.stdin:
            command = json.loads(line)
            if command == {"action": "crash"}:
                os._exit(79)
            if command == {"action": "stop"}:
                break
    finally:
        server.should_exit = True
        if thread.is_alive():
            thread.join(15)
        assert not thread.is_alive(), "Fixture coordinator shutdown did not finish"


if __name__ == "__main__":
    main()
