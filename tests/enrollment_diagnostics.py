"""Private crash-fixture diagnostics; never record request data or error text."""

from pathlib import Path

from companion.pairing_client import PairingClientError


def approve_for_crash_fixture(controller, policy, events):
    """Prepare the crash using normal review/resume, with three bounded attempts."""
    reviewed = None
    network_errors = {
        "The enrollment request exceeded its time limit": "operation_timeout",
        "The coordinator could not be reached securely": "secure_connection",
    }
    for attempt in range(3):
        try:
            state = controller.pairing_status()["state"]
            if state == "attempt":
                if reviewed is None:
                    reviewed = controller.pairing_status(refresh=True)["claim"]
                # Keep the original reviewed claim. The real client rejects a
                # changed owner/revision rather than approving a new display.
                return controller.approve_pairing(reviewed, policy)
            if state in ("pending_approval", "paired"):
                # Resume the saved request/credential; never create another.
                return controller.pairing_status(refresh=True)
            raise AssertionError("Unexpected crash-fixture pairing state")
        except PairingClientError as error:
            category = network_errors.get(str(error))
            if category is None or attempt == 2:
                raise
            events.append(category)


def diagnostic_worker(directory):
    directory.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[1] / "companion/enrollment_worker.py"
    output = directory / "events.jsonl"
    worker = directory / "worker.py"
    worker.write_text(
        "SOURCE = " + repr(str(source)) + "\nOUTPUT = " + repr(str(output)) + "\n" + r'''
import importlib.util
import json
from pathlib import Path
from time import monotonic

spec = importlib.util.spec_from_file_location("private_enrollment_source", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = module.exchange
KINDS = {"URLError", "SSLError", "SSLCertVerificationError", "SSLEOFError", "SSLZeroReturnError", "TimeoutError",
    "ConnectionResetError", "ConnectionAbortedError", "ConnectionRefusedError",
    "RemoteDisconnected", "IncompleteRead", "BrokenPipeError", "gaierror", "JSONDecodeError", "ValueError", "OSError", "Failure"}

def kind(error):
    name = type(error).__name__
    return name if name in KINDS else "other"

def observed(value):
    started = monotonic()
    event = {"outcome": "returned"}
    try:
        return original(value)
    except Exception as error:
        event.update(outcome="error", kind=kind(error), reason_kind=kind(getattr(error, "reason", None)))
        for field in ("errno", "winerror"):
            number = getattr(error, field, None)
            if type(number) is int:
                event[field] = number
        raise
    finally:
        event["elapsed_s"] = round(monotonic() - started, 3)
        with Path(OUTPUT).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")

module.exchange = observed
raise SystemExit(module.main())
''', encoding="utf-8")
    return worker, output
