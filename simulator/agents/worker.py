"""Child-process entry point for trusted local autonomous-agent packages.

Only protocol messages go to stdout. Agent imports, prints, and tracebacks use
stderr. The worker never connects to CARLA or advances the simulation clock.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter
import traceback

from shared.models import AgentObservation
from simulator.timing import high_resolution_timer

from .package import inspect_agent_package, load_agent_package
from .process_protocol import (
    AgentProcessRequest, AgentWorkerError,
    DEFAULT_MAX_MESSAGE_BYTES, decode_request, encode_response,
)
from .sdk import AgentLifecycleError, AgentProtocolError, AgentSession


def _empty_payload(request: AgentProcessRequest) -> None:
    if request.payload:
        raise AgentProtocolError(f"{request.operation} payload must be empty")


def _error_text(error: BaseException) -> str:
    notes = getattr(error, "__notes__", ())
    return "\n".join([str(error), *notes])


def serve(package_path: str | Path, input_stream, output_stream, *, max_message_bytes=DEFAULT_MAX_MESSAGE_BYTES) -> int:
    """Serve binary streams until close, EOF, or the first protocol/agent error.

    ``main`` redirects Python stdout before calling this function. Supplying
    streams separately also allows tests to exercise the transport without a
    simulator or the parent client implementation.
    """
    if type(max_message_bytes) is not int or max_message_bytes < 1:
        raise ValueError("max_message_bytes must be a positive integer")
    session = None
    last_request_id = -1
    hello_received = False

    def cleanup():
        nonlocal session
        current, session = session, None
        if current is not None:
            current.close()

    def respond(request, *, payload=None, error=None):
        output_stream.write(encode_response(
            request.request_id, request.operation, payload=payload, error=error,
            max_message_bytes=max_message_bytes,
        ))
        output_stream.flush()

    try:
        while True:
            request = None
            try:
                raw = input_stream.readline(max_message_bytes + 1)
                if not raw:
                    cleanup()
                    return 0
                if not raw.endswith(b"\n"):
                    raise AgentProtocolError("Agent process request is truncated or exceeds the message limit")
                request = decode_request(raw, max_message_bytes)
                if request.request_id <= last_request_id:
                    raise AgentProtocolError("Agent process request IDs must be strictly increasing")
                last_request_id = request.request_id

                if request.operation == "hello":
                    _empty_payload(request)
                    if hello_received or session is not None:
                        raise AgentProtocolError("hello is allowed only once before loading the agent")
                    hello_received = True
                    payload = {"worker_pid": os.getpid(), "python_version": platform.python_version()}
                elif request.operation == "load":
                    if session is not None:
                        raise AgentLifecycleError("Agent is already loaded")
                    if set(request.payload) not in ({"manifest"}, {"manifest", "snapshot"}):
                        raise AgentProtocolError("load payload must contain manifest and an optional registered snapshot")
                    snapshot = None
                    if "snapshot" in request.payload:
                        from shared.models.registry import AgentSnapshot
                        from .fingerprints import verify_agent_snapshot
                        snapshot = AgentSnapshot.model_validate(request.payload["snapshot"])
                        verify_agent_snapshot(snapshot, worker=True)
                        if str(Path(package_path).resolve()) != snapshot.package_path:
                            raise AgentProtocolError("Worker package path does not match registered snapshot")
                    package = inspect_agent_package(package_path)
                    declared = json.dumps(request.payload["manifest"], sort_keys=True, allow_nan=False)
                    inspected = json.dumps(package.manifest.model_dump(mode="json"), sort_keys=True, allow_nan=False)
                    if declared != inspected:
                        raise AgentProtocolError("Agent package manifest does not match the inspected parent manifest")
                    effective = package.manifest
                    if snapshot is not None:
                        effective = type(effective).model_validate(effective.model_dump(mode="json") | {
                            "configuration": snapshot.configuration,
                        })
                        if json.dumps(effective.model_dump(mode="json"), sort_keys=True, allow_nan=False) != json.dumps(
                            snapshot.manifest.model_dump(mode="json"), sort_keys=True, allow_nan=False,
                        ):
                            raise AgentProtocolError("Effective worker manifest does not match registered snapshot")
                    agent = load_agent_package(package)
                    try:
                        session = AgentSession(agent, effective)
                    except BaseException as error:
                        try:
                            agent.close()
                        except BaseException as cleanup_error:
                            error.add_note(f"Agent cleanup also failed: {type(cleanup_error).__name__}: {cleanup_error}")
                        raise
                    session.load()
                    payload = {"worker_pid": os.getpid(), "python_version": platform.python_version()}
                elif request.operation == "close":
                    _empty_payload(request)
                    cleanup()
                    respond(request)
                    return 0
                else:
                    if session is None:
                        raise AgentLifecycleError("Agent must be loaded before reset or step")
                    if request.operation == "reset":
                        session.reset(request.payload)
                        payload = {}
                    else:
                        observation = AgentObservation.model_validate(request.payload)
                        started = perf_counter()
                        # Materializing a typed observation lets AgentSession
                        # isolate it through its compiled dump/revalidation
                        # path, instead of deep-copying a mapping that could
                        # contain caller-owned model instances.
                        result = session.step(observation)
                        elapsed = perf_counter() - started
                        payload = {"result": result.model_dump(mode="json"), "worker_step_wall_time_s": elapsed}
                respond(request, payload=payload)
            except BaseException as error:
                try:
                    cleanup()
                except BaseException as cleanup_error:
                    error.add_note(f"Agent cleanup also failed: {type(cleanup_error).__name__}: {cleanup_error}")
                traceback.print_exception(error, file=sys.stderr)
                if request is not None:
                    # A large exception must not turn into an unbounded frame.
                    # Keep enough room for the envelope and JSON escaping.
                    message = _error_text(error)
                    while True:
                        try:
                            respond(request, error=AgentWorkerError(type=type(error).__name__, message=message))
                            break
                        except ValueError:
                            if not message:
                                break
                            message = message[:len(message) // 2]
                        except (OSError, BrokenPipeError):
                            break
                return 1
    finally:
        cleanup()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--max-message-bytes", type=int, default=DEFAULT_MAX_MESSAGE_BYTES)
    options = parser.parse_args(argv)
    if options.max_message_bytes < 1:
        parser.error("--max-message-bytes must be positive")
    # Keep the text wrapper alive too: discarding it would close its buffer.
    protocol_stdout = sys.stdout
    protocol_output = protocol_stdout.buffer
    # Preserve only this local reference to the wire. User Python prints,
    # including explicit sys.__stdout__ prints, are diagnostics on stderr.
    sys.stdout = sys.__stdout__ = sys.stderr
    # Windows timer resolution is process-local; the parent's request does not
    # cover this worker's accelerator/library waits. Always restore on exit.
    with high_resolution_timer():
        return serve(options.package, sys.stdin.buffer, protocol_output, max_message_bytes=options.max_message_bytes)


if __name__ == "__main__":
    raise SystemExit(main())
