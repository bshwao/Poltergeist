"""Real HTTPS transfers, interruption/restart and owned model storage boundaries."""

from contextlib import contextmanager
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import ssl
import subprocess
import sys
from threading import Event, Thread
from time import monotonic
import urllib.request

import pytest

from backend.persistence_errors import ConflictError
from companion.model_assets import AssetError, AssetStore, CHUNK, DownloadPaused, ModelAsset, _HTTPSRedirects
from test_relay_client import tls

DATA = b"official fixture bytes; not a trained model\n" * 14000


@contextmanager
def asset_server(tls, *, mode="normal", body=DATA):
    observed = []
    release = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append({"range": self.headers.get("Range"), "authorization": self.headers.get("Authorization"),
                             "encoding": self.headers.get("Accept-Encoding")})
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://localhost:1/private-sentinel")
                self.end_headers()
                return
            offset = int(self.headers["Range"].split("=")[1].split("-")[0]) if self.headers.get("Range") else 0
            if mode == "ignore_range":
                offset = 0
            status = 206 if offset else 200
            self.send_response(status)
            if status == 206:
                start = offset + 1 if mode == "bad_range" else offset
                self.send_header("Content-Range", f"bytes {start}-{len(body)-1}/{len(body)}")
            if mode == "encoded":
                self.send_header("Content-Encoding", "gzip")
            if mode != "excess":
                self.send_header("Content-Length", str(len(body) - offset + (1 if mode == "bad_size" else 0)))
            self.end_headers()
            sent = body[offset:] + (b"x" if mode == "excess" else b"")
            if mode == "truncated":
                sent = sent[:CHUNK]
            try:
                if mode == "stall":
                    self.wfile.write(sent[:CHUNK])
                    self.wfile.flush()
                    release.wait(15)
                    sent = sent[CHUNK:]
                self.wfile.write(sent)
                self.wfile.flush()
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.socket = tls[0].wrap_socket(server.socket, server_side=True)
    owner = Thread(target=server.serve_forever, daemon=True)
    owner.start()
    asset = ModelAsset("fixture-model", "weights.ckpt", f"https://localhost:{server.server_port}/weights",
                       len(DATA), hashlib.sha256(DATA).hexdigest())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=tls[1]), _HTTPSRedirects())
    try:
        yield asset, opener.open, observed
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        owner.join(3)
        assert not owner.is_alive()


def test_verified_publication_uses_no_credentials_and_rechecks_existing_bytes(tmp_path, tls):
    with asset_server(tls) as (asset, open_request, observed):
        store = AssetStore(tmp_path, asset, _open=open_request)
        progress = []
        assert not (tmp_path / "models").exists()
        assert store.inspect() == ("absent", 0)
        assert not (tmp_path / "models").exists()
        path = store.download(progress=lambda *row: progress.append(row))
        assert path.read_bytes() == DATA and not store.partial.exists()
        assert {row[0] for row in progress} == {"downloading", "verifying"}
        assert observed == [{"range": None, "authorization": None, "encoding": "identity"}]
        assert store.download() == path
        assert store.inspect() == ("weights_ready", len(DATA)) and len(observed) == 1
        path.write_bytes(b"damaged" + DATA[7:])
        with pytest.raises(AssetError, match="hash"):
            store.download()
        assert path.read_bytes().startswith(b"damaged") and len(observed) == 1
        assert store.download(repair=True).read_bytes() == DATA
        assert len(observed) == 2


@pytest.mark.parametrize("mode", ["normal", "ignore_range"])
def test_pause_reopen_and_resume_or_safe_restart(tmp_path, tls, mode):
    with asset_server(tls, mode=mode) as (asset, open_request, observed):
        store = AssetStore(tmp_path, asset, _open=open_request)
        pause = Event()
        def progress(stage, count, total):
            if stage == "downloading" and count:
                pause.set()
        with pytest.raises(DownloadPaused):
            store.download(cancelled=pause.is_set, progress=progress)
        saved = store.partial.read_bytes()
        assert saved == DATA[:len(saved)] and 0 < len(saved) < len(DATA)
        assert not store.path.exists()
        reopened = AssetStore(tmp_path, asset, _open=open_request)
        assert reopened.inspect() == ("paused", len(saved))
        assert reopened.download().read_bytes() == DATA
        assert observed[-1]["range"] == f"bytes={len(saved)}-"


@pytest.mark.parametrize("mode,match", [("bad_range", "resume range"), ("bad_size", "download size"),
                                        ("encoded", "encoded"), ("excess", "exceeds"),
                                        ("truncated", "interrupted"), ("redirect", "HTTPS")])
def test_invalid_responses_never_publish_weights(tmp_path, tls, mode, match):
    with asset_server(tls, mode=mode) as (asset, open_request, _):
        store = AssetStore(tmp_path, asset, _open=open_request)
        store._own()
        store._lease.release()
        if mode == "bad_range":
            store.partial.write_bytes(DATA[:17])
        with pytest.raises(AssetError, match=match):
            store.download()
        assert not store.path.exists()
        if mode == "bad_range":
            assert store.partial.read_bytes() == DATA[:17]


def test_corrupt_resume_prefix_is_rehashed_and_explicit_repair_discards_it(tmp_path, tls):
    with asset_server(tls) as (asset, open_request, observed):
        store = AssetStore(tmp_path, asset, _open=open_request)
        store._own()
        store._lease.release()
        store.partial.write_bytes(b"wrong prefix")
        with pytest.raises(AssetError, match="hash"):
            store.download()
        assert store.partial.stat().st_size == len(DATA) and not store.path.exists()
        with pytest.raises(AssetError, match="hash"):
            store.download()  # Full corrupt partial does not issue a spurious Range request.
        assert len(observed) == 1
        assert store.download(repair=True).read_bytes() == DATA
        assert observed[-1]["range"] is None


def test_pause_during_full_file_verification_keeps_unpublished_bytes_for_recheck(tmp_path, tls):
    with asset_server(tls) as (asset, open_request, observed):
        store = AssetStore(tmp_path, asset, _open=open_request)
        pause = Event()
        def progress(stage, count, total):
            if stage == "verifying":
                pause.set()
        with pytest.raises(DownloadPaused):
            store.download(cancelled=pause.is_set, progress=progress)
        assert store.partial.read_bytes() == DATA and not store.path.exists()
        assert store.inspect() == ("paused", len(DATA))
        assert store.download().read_bytes() == DATA and len(observed) == 1


def test_full_partial_after_process_exit_is_verified_without_network(tmp_path, tls):
    with asset_server(tls) as (asset, open_request, observed):
        store = AssetStore(tmp_path, asset, _open=open_request)
        store._own()
        store._lease.release()
        store.partial.write_bytes(DATA)
        assert store.download().read_bytes() == DATA and observed == []


def test_killed_owned_downloader_releases_lease_and_real_https_resume_keeps_saved_prefix(tmp_path, tls):
    ca = tmp_path / "public-test-ca.pem"
    ca.write_text("".join(ssl.DER_cert_to_PEM_cert(cert) for cert in tls[1].get_ca_certs(binary_form=True)))
    program = """
import hashlib, ssl, sys, urllib.request
from companion.model_assets import AssetStore, ModelAsset
asset = ModelAsset('fixture-model', 'weights.ckpt', sys.argv[2], int(sys.argv[3]), sys.argv[4])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=sys.argv[5])))
AssetStore(sys.argv[1], asset, _open=opener.open).download()
"""
    with asset_server(tls, mode="stall") as (asset, open_request, _):
        store = AssetStore(tmp_path, asset, _open=open_request)
        child = subprocess.Popen([sys.executable, "-B", "-c", program, str(tmp_path), asset.url,
            str(asset.size), asset.sha256, str(ca)], cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = monotonic() + 10
            while monotonic() < deadline:
                if store.partial.exists() and store.partial.stat().st_size > 0:
                    break
                if child.poll() is not None:
                    raise AssertionError("Owned downloader exited before the interruption boundary")
                Event().wait(.01)
            else:
                raise AssertionError("Owned downloader did not retain partial bytes")
            with pytest.raises(ConflictError):
                store.remove()
            child.kill()  # Only the Popen child created immediately above; it spawns no descendants.
            assert child.wait(10) != 0
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(10)
            child.stderr.close()
        prefix = store.partial.read_bytes()
        assert prefix and DATA.startswith(prefix) and len(prefix) < len(DATA)
    with asset_server(tls) as (fresh, open_request, observed):
        # Same pinned identity, a fresh listener after the original process died.
        reopened = AssetStore(tmp_path, fresh, _open=open_request)
        assert reopened.inspect() == ("paused", len(prefix))
        assert reopened.download().read_bytes() == DATA
        assert observed[0]["range"] == f"bytes={len(prefix)}-"


def test_disk_capacity_failure_preserves_partial_and_never_contacts_server(tmp_path, tls, monkeypatch):
    from types import SimpleNamespace
    with asset_server(tls) as (asset, open_request, observed):
        store = AssetStore(tmp_path, asset, _open=open_request)
        monkeypatch.setattr("companion.model_assets.shutil.disk_usage", lambda path: SimpleNamespace(free=0))
        with pytest.raises(AssetError, match="free space"):
            store.download()
        assert not store.partial.exists() and observed == []


def test_owned_storage_lease_excludes_another_writer_and_removal_preserves_other_data(tmp_path, tls):
    with asset_server(tls) as (asset, open_request, _):
        first = AssetStore(tmp_path, asset, _open=open_request)
        other = AssetStore(tmp_path, asset, _open=open_request)
        first._own()
        try:
            with pytest.raises(ConflictError):
                other.remove()
        finally:
            first._lease.release()
        first.download()
        unrelated = tmp_path / "imported.ckpt"
        unrelated.write_bytes(b"preserve")
        first.remove()
        assert first.inspect() == ("absent", 0)
        assert unrelated.read_bytes() == b"preserve" and first.marker.is_file()


@pytest.mark.parametrize("kind", ["unowned", "wrong_owner", "unknown_file", "hardlink", "oversized"])
def test_storage_anomalies_fail_without_changing_user_bytes(tmp_path, kind):
    asset = ModelAsset("fixture", "weights.ckpt", "https://example.invalid/model", len(DATA), hashlib.sha256(DATA).hexdigest())
    store = AssetStore(tmp_path, asset)
    if kind == "unowned":
        store.directory.mkdir(parents=True)
    else:
        store._own()
        store._lease.release()
    sentinel = tmp_path / "original"
    sentinel.write_bytes(b"preserve")
    if kind == "wrong_owner":
        store.marker.write_text('{}')
    elif kind == "unknown_file":
        (store.directory / "user-note.txt").write_bytes(b"preserve")
    elif kind == "hardlink":
        os.link(sentinel, store.partial)
    elif kind == "oversized":
        store.partial.write_bytes(DATA + b"excess")
    before = {p.name: p.read_bytes() for p in store.directory.iterdir()}
    with pytest.raises(AssetError):
        store.download()
    assert {p.name: p.read_bytes() for p in store.directory.iterdir()} == before
    assert sentinel.read_bytes() == b"preserve"


def test_model_folder_link_is_never_followed(tmp_path):
    store = AssetStore(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    store.directory.parent.mkdir()
    try:
        store.directory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating a native symbolic link is unavailable")
    for action in (store.inspect, store.download, store.remove):
        with pytest.raises(AssetError, match="Linked"):
            action()
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
def test_windows_junction_without_developer_mode_is_never_followed(tmp_path):
    store = AssetStore(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "user-data"
    sentinel.write_bytes(b"preserve")
    store.directory.parent.mkdir()
    environment = dict(os.environ, POLTERGEIST_TEST_JUNCTION=str(store.directory), POLTERGEIST_TEST_TARGET=str(outside))
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "$ErrorActionPreference = 'Stop'; New-Item -ItemType Junction -Path $env:POLTERGEIST_TEST_JUNCTION -Target $env:POLTERGEIST_TEST_TARGET | Out-Null"],
        env=environment, capture_output=True, timeout=30)
    assert result.returncode == 0, "Owned test junction could not be created"
    try:
        assert store.directory.is_junction()
        for action in (store.inspect, store.download, store.remove):
            with pytest.raises(AssetError, match="Linked"):
                action()
        assert sentinel.read_bytes() == b"preserve"
    finally:
        store.directory.rmdir()  # Remove this owned junction itself; never recurse into its target.
    assert sentinel.read_bytes() == b"preserve"


def test_production_opener_requires_certificate_trust_and_hostname(tmp_path, tls):
    with asset_server(tls) as (asset, open_request, observed):
        with pytest.raises(AssetError, match="connection"):
            AssetStore(tmp_path, asset).download()
        with pytest.raises(AssetError, match="connection"):
            AssetStore(tmp_path, replace(asset, url=asset.url.replace("localhost", "127.0.0.1")), _open=open_request).download()
        assert observed == []


@pytest.mark.parametrize("changes", [{"url": "http://example.invalid/m"}, {"url": "https://name:secret@example.invalid/m"},
    {"id": "../outside"}, {"filename": ".."}, {"filename": "owner.json"}, {"size": True}, {"sha256": "abcd"}])
def test_catalogue_rejects_unsafe_or_unpinned_asset_metadata(changes):
    valid = dict(id="fixture", filename="weights.ckpt", url="https://example.invalid/m", size=1, sha256="a" * 64)
    with pytest.raises(ValueError):
        ModelAsset(**(valid | changes))
