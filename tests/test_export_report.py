"""Report CLI streams atomically and leaves existing files alone by default."""

from email.message import Message
import gc
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from uuid import uuid4

import pytest

from scripts import export_report as cli


class Response(BytesIO):
    def __init__(self, content=b'{"schema":"poltergeist-run-report-v1"}', *, content_type="application/json", length=None, status=200, schema=None):
        super().__init__(content)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(content)) if length is None else length
        if schema is not None:
            self.headers["X-Poltergeist-Report-Schema"] = schema


@pytest.fixture
def download(monkeypatch, tmp_path):
    requests = []
    responses = []
    class Opener:
        def open(self, request, **kwargs):
            requests.append((request, kwargs))
            response = responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
    monkeypatch.setattr(cli, "build_opener", lambda *handlers: Opener())
    defaults = {"simulation_id": uuid4(), "api_url": "http://127.0.0.1:8000", "format": "json", "output": tmp_path / "result.json"}
    def run(**kwargs):
        return cli.download_report(**(defaults | kwargs))
    return run, defaults, requests, responses


@pytest.mark.parametrize("format,media_type,payload", [("json", "application/json", b'{"test":null}'), ("csv", "text/csv", b'key,value\r\nx,null\r\n'), ("html", "text/html", b'<!doctype html><h1>Report</h1>')])
def test_download_preserves_bytes_and_format_without_overwrite(download, format, media_type, payload):
    run, defaults, requests, responses = download
    responses.append(Response(payload, content_type=media_type))
    output = run(format=format)
    assert output.read_bytes() == payload
    request, options = requests[0]
    assert request.full_url == f"http://127.0.0.1:8000/reports/simulations/{defaults['simulation_id']}?format={format}"
    assert request.get_header("Accept") == media_type
    assert options["timeout"] == 30.0
    assert not list(output.parent.glob(".poltergeist-report-*.tmp"))


def test_api_prefix_and_opt_in_trace_are_preserved(download):
    run, defaults, requests, responses = download
    responses.append(Response())
    run(api_url="https://example.test/api/v1/", include_telemetry=True, timeout=7.5)
    assert requests[0][0].full_url == f"https://example.test/api/v1/reports/simulations/{defaults['simulation_id']}?format=json&include_telemetry=true"
    assert requests[0][1]["timeout"] == 7.5


def test_behavior_export_option_is_forwarded_for_every_format(download):
    run, defaults, requests, responses = download
    for format, content_type in [("json", "application/json"), ("csv", "text/csv"), ("html", "text/html")]:
        responses.append(Response(b"behavior report", content_type=content_type, schema="poltergeist-run-report-v2"))
        run(format=format, include_behavior=True, output=defaults["output"].with_suffix("." + format))
        assert requests[-1][0].full_url.endswith(f"?format={format}&include_behavior=true")


@pytest.mark.parametrize("schema", [None, "poltergeist-run-report-v1", "unknown"])
@pytest.mark.parametrize("format,content_type", [("json", "application/json"), ("csv", "text/csv"), ("html", "text/html")])
def test_behavior_export_rejects_silent_legacy_response_without_replacing_output(download, schema, format, content_type):
    run, defaults, _, responses = download
    defaults["output"].write_bytes(b"existing report")
    responses.append(Response(b"legacy report", content_type=content_type, schema=schema))
    with pytest.raises(cli.ExportError, match="behavior exports"):
        run(format=format, include_behavior=True, force=True)
    assert defaults["output"].read_bytes() == b"existing report"
    assert not list(defaults["output"].parent.glob(".poltergeist-report-*.tmp"))


def test_existing_output_is_preserved_before_making_a_request(download):
    run, defaults, requests, _ = download
    defaults["output"].write_bytes(b"keep me")
    with pytest.raises(cli.ExportError, match="--force"):
        run()
    assert defaults["output"].read_bytes() == b"keep me"
    assert requests == []


def test_force_atomically_replaces_an_existing_file(download, monkeypatch):
    run, defaults, _, responses = download
    defaults["output"].write_bytes(b"old")
    responses.append(Response(b"new"))
    real_replace = os.replace
    observed = []
    def replace(source, target):
        observed.append((Path(source).read_bytes(), Path(target).read_bytes()))
        real_replace(source, target)
    monkeypatch.setattr(cli.os, "replace", replace)
    output = run(force=True)
    assert observed == [(b"new", b"old")]
    assert output.read_bytes() == b"new"


def test_competing_file_publication_cannot_overwrite_racing_writer(download, monkeypatch):
    run, defaults, _, responses = download
    responses.append(Response(b"new"))
    publish = cli._publish_new_file
    def race(source, target):
        Path(target).write_bytes(b"concurrent writer")
        publish(source, target)
    monkeypatch.setattr(cli, "_publish_new_file", race)
    with pytest.raises(cli.ExportError, match="already exists"):
        run()
    assert defaults["output"].read_bytes() == b"concurrent writer"
    assert not list(defaults["output"].parent.glob(".poltergeist-report-*.tmp"))


@pytest.mark.parametrize("response", [
    Response(b"", length="0"), Response(b"short", length="900"), Response(b"x", length="invalid"),
    Response(b"x", length="-1"), Response(b"<!doctype html>", content_type="text/html"), Response(status=202),
])
def test_bad_or_incomplete_transfer_never_publishes_a_destination(download, response):
    run, defaults, _, responses = download
    responses.append(response)
    with pytest.raises(cli.ExportError):
        run()
    assert not defaults["output"].exists()
    assert not list(defaults["output"].parent.glob(".poltergeist-report-*.tmp"))


def test_stream_failure_preserves_existing_file_even_with_force(download):
    run, defaults, _, responses = download
    class Failing(Response):
        def read(self, size=-1):
            raise ConnectionResetError("connection lost")
    responses.append(Failing())
    defaults["output"].write_bytes(b"old")
    with pytest.raises(cli.ExportError):
        run(force=True)
    assert defaults["output"].read_bytes() == b"old"
    assert not list(defaults["output"].parent.glob(".poltergeist-report-*.tmp"))


def test_keyboard_interrupt_removes_staging_file_and_does_not_publish(download, monkeypatch, capsys):
    _, defaults, _, responses = download
    class Interrupted(Response):
        def read(self, size=-1):
            raise KeyboardInterrupt
    responses.append(Interrupted())
    code = cli.main(["--simulation-id", str(defaults["simulation_id"]), "--output", str(defaults["output"])])
    assert code == 130
    assert "interrupted" in capsys.readouterr().err
    assert not defaults["output"].exists()
    assert not list(defaults["output"].parent.glob(".poltergeist-report-*.tmp"))


@pytest.mark.parametrize("status,message", [(404, "not found"), (409, "not finalized"), (422, "rejected"), (503, "unavailable"), (302, "did not provide"), (500, "did not provide")])
def test_http_errors_are_actionable_without_echoing_untrusted_server_payload(download, status, message, capsys):
    _, defaults, _, responses = download
    class TrackedHTTPError(HTTPError):
        close_observed = False
        def close(self):
            self.close_observed = True
            return super().close()
    body = BytesIO(b"<html>SECRET_TRACEBACK</html>")
    http_error = TrackedHTTPError("http://localhost", status, "error", {}, body)
    responses.append(http_error)
    code = cli.main(["--simulation-id", str(defaults["simulation_id"]), "--output", str(defaults["output"])])
    assert code == 1
    stderr = capsys.readouterr().err
    assert message in stderr and f"HTTP {status}" in stderr
    assert "SECRET_TRACEBACK" not in stderr and "Traceback" not in stderr
    assert not defaults["output"].exists()
    assert body.closed and http_error.close_observed
    del http_error
    gc.collect()


def test_connection_error_identifies_api_and_timeout_options(download, capsys):
    _, defaults, _, responses = download
    responses.append(URLError("connection refused"))
    assert cli.main(["--simulation-id", str(defaults["simulation_id"]), "--output", str(defaults["output"])]) == 1
    stderr = capsys.readouterr().err
    assert "--api-url" in stderr and "--timeout" in stderr and "Traceback" not in stderr


@pytest.mark.parametrize("url", ["ftp://example.test", "http://user:pass@example.test", "http://example.test?q=x", "http://example.test#x", "http://example.test:99999", "http://bad host", "http://[bad", "http://example.test\\bad"])
def test_invalid_base_urls_fail_before_network(download, url):
    run, _, requests, _ = download
    with pytest.raises(cli.ExportError, match="API URL"):
        run(api_url=url)
    assert requests == []


@pytest.mark.parametrize("change", [{"format": "pdf"}, {"format": "html", "include_telemetry": True}])
def test_invalid_format_combinations_fail_before_network(download, change):
    run, _, requests, _ = download
    with pytest.raises(cli.ExportError):
        run(**change)
    assert requests == []


def test_missing_output_directory_and_directory_target_have_useful_errors(download):
    run, defaults, requests, _ = download
    with pytest.raises(cli.ExportError, match="Create it first"):
        run(output=defaults["output"].parent / "missing" / "result.json")
    with pytest.raises(cli.ExportError, match="Choose a file path"):
        run(output=defaults["output"].parent)
    assert requests == []


def test_permission_or_atomic_link_error_removes_staged_file(download, monkeypatch):
    run, defaults, _, responses = download
    responses.append(Response())
    def fail(*args):
        raise PermissionError("not writable")
    monkeypatch.setattr(cli, "_publish_new_file", fail)
    with pytest.raises(cli.ExportError, match="Could not save"):
        run()
    assert not defaults["output"].exists()
    assert not list(defaults["output"].parent.glob(".poltergeist-report-*.tmp"))


@pytest.mark.parametrize("windows,expected", [(True, "rename"), (False, "link")])
def test_new_file_publication_uses_platform_specific_no_clobber_operation(monkeypatch, tmp_path, windows, expected):
    calls = []
    staged, output = tmp_path / "staged.tmp", tmp_path / "result.json"
    monkeypatch.setattr(cli.os, "rename", lambda *args: calls.append(("rename", args)))
    monkeypatch.setattr(cli.os, "link", lambda *args: calls.append(("link", args)))
    cli._publish_new_file(staged, output, windows=windows)
    assert calls == [(expected, (staged, output))]


@pytest.mark.parametrize("args", [["--simulation-id", "bad"], ["--timeout", "nan"], ["--timeout", "0"], ["--format", "html", "--include-telemetry"]])
def test_cli_usage_errors_are_nonzero_without_traceback(download, args, capsys):
    _, defaults, _, _ = download
    with pytest.raises(SystemExit) as error:
        cli.main(["--simulation-id", str(defaults["simulation_id"]), "--output", str(defaults["output"]), *args])
    assert error.value.code == 2
    assert "Traceback" not in capsys.readouterr().err


def test_cli_success_prints_the_final_path(download, capsys):
    _, defaults, _, responses = download
    responses.append(Response())
    assert cli.main(["--simulation-id", str(defaults["simulation_id"]), "--output", str(defaults["output"])]) == 0
    assert str(defaults["output"]) in capsys.readouterr().out


def test_module_help_is_available_using_only_runtime_standard_library():
    result = subprocess.run([sys.executable, "-m", "scripts.export_report", "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "--simulation-id" in result.stdout and "--include-telemetry" in result.stdout
    assert "Traceback" not in result.stderr
