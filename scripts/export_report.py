"""Download a finalized run report without installing development tools."""

import argparse
from http.client import HTTPException
import math
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID


class ExportError(Exception):
    """An actionable report download or publication error."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def _publish_new_file(staged: Path, output: Path, *, windows: bool = os.name == "nt") -> None:
    # Windows rename is atomic and raises FileExistsError for any existing
    # destination. POSIX rename replaces destinations, so use an exclusive
    # atomic hard link there. Both files reside in the same directory.
    if windows:
        os.rename(staged, output)
    else:
        os.link(staged, output)


def _base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise argparse.ArgumentTypeError("API URL must be a valid HTTP(S) URL") from error
    if (
        parsed.scheme not in ("http", "https") or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or any(char.isspace() for char in value)
        or any(char in value for char in ("\\", "\x00"))
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise argparse.ArgumentTypeError("API URL must use HTTP(S) without credentials, query, or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _positive_number(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return parsed


def _http_error(error: HTTPError) -> ExportError:
    descriptions = {
        404: "Run was not found; check --simulation-id and --api-url.",
        409: "Run output is not finalized yet; wait for completion and retry.",
        422: "The server rejected the report options or stored evidence.",
        503: "Persistence or a required stored artifact is unavailable; check the server logs.",
    }
    return ExportError(f"HTTP {error.code}: {descriptions.get(error.code, 'The API did not provide a report; check the server and API URL.')}")


def download_report(
    simulation_id: UUID, *, api_url: str, format: str, output: Path,
    include_telemetry: bool = False, force: bool = False, timeout: float = 30.0,
) -> Path:
    """Stream to a sibling temporary file, then atomically publish once complete.

    A Windows no-clobber rename or POSIX exclusive hard link publishes a new
    file without replacing any existing pathname, including a file created
    concurrently after the initial check. --force uses atomic replacement.
    """
    if format not in ("json", "csv", "html"):
        raise ExportError("Report format must be json, csv, or html")
    if include_telemetry and format != "json":
        raise ExportError("--include-telemetry requires --format json")
    try:
        base_url = _base_url(api_url)
    except argparse.ArgumentTypeError as error:
        raise ExportError(str(error)) from error
    output = Path(output).expanduser().absolute()
    if not output.parent.is_dir():
        raise ExportError(f"Output directory does not exist: {output.parent}. Create it first.")
    if output.is_dir():
        raise ExportError(f"Output is a directory: {output}. Choose a file path.")
    if output.exists() and not force:
        raise ExportError(f"Output already exists: {output}. Choose a new file or pass --force.")
    query = {"format": format}
    if include_telemetry:
        query["include_telemetry"] = "true"
    url = f"{base_url}/reports/simulations/{simulation_id}?{urlencode(query)}"
    request = Request(url, headers={"Accept": {"json": "application/json", "csv": "text/csv", "html": "text/html"}[format]})
    temporary = None
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            if response.status != 200:
                raise ExportError(f"The API returned HTTP {response.status}; no report was saved.")
            expected_type = {"json": "application/json", "csv": "text/csv", "html": "text/html"}[format]
            if response.headers.get_content_type() != expected_type:
                raise ExportError(f"The API returned an unexpected content type; expected {expected_type}. Check --api-url.")
            length = response.headers.get("Content-Length")
            try:
                expected_length = None if length is None else int(length)
                if expected_length is not None and expected_length < 0:
                    raise ValueError
            except ValueError as error:
                raise ExportError("The API returned an invalid report length; no report was saved.") from error
            with NamedTemporaryFile(mode="wb", prefix=".poltergeist-report-", suffix=".tmp", dir=output.parent, delete=False) as stream:
                temporary = Path(stream.name)
                size = 0
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
                    size += len(chunk)
                if size == 0 or (expected_length is not None and size != expected_length):
                    raise ExportError("The report transfer was empty or incomplete; retry the export.")
                stream.flush()
                os.fsync(stream.fileno())
        if force:
            os.replace(temporary, output)
        else:
            try:
                _publish_new_file(temporary, output)
            except FileExistsError as error:
                raise ExportError(f"Output already exists: {output}. Choose a new file or pass --force.") from error
        return output
    except HTTPError as error:
        try:
            failure = _http_error(error)
        finally:
            error.close()
        raise failure from error
    except (URLError, HTTPException, TimeoutError) as error:
        raise ExportError("Could not download a complete report. Check that the API is running, verify --api-url, and retry (or increase --timeout).") from error
    except OSError as error:
        raise ExportError(f"Could not save the report to {output}: {error.strerror or str(error)}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export finalized Poltergeist evidence as JSON, CSV, or printable HTML.")
    parser.add_argument("--simulation-id", type=UUID, required=True, help="Persisted terminal run UUID")
    parser.add_argument("--api-url", type=_base_url, default="http://127.0.0.1:8000", help="API base URL (default: %(default)s)")
    parser.add_argument("--format", choices=("json", "csv", "html"), default="json")
    parser.add_argument("--output", type=Path, required=True, help="Destination file; its parent directory must exist")
    parser.add_argument("--include-telemetry", action="store_true", help="Include the finalized trace (JSON only)")
    parser.add_argument("--force", action="store_true", help="Atomically replace an existing output file")
    parser.add_argument("--timeout", type=_positive_number, default=30.0, help="HTTP socket timeout in seconds (default: %(default)s)")
    arguments = parser.parse_args(argv)
    if arguments.include_telemetry and arguments.format != "json":
        parser.error("--include-telemetry requires --format json")
    try:
        output = download_report(**vars(arguments))
    except KeyboardInterrupt:
        print("Report export interrupted; no incomplete report was published.", file=sys.stderr)
        return 130
    except ExportError as error:
        print(f"Report export failed: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Report export failed: Could not finish writing or cleaning up the export: {error.strerror or str(error)}", file=sys.stderr)
        return 1
    print(f"Saved {arguments.format.upper()} report to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
