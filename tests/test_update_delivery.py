"""Delivery uses fixture signatures/HTTP streams; no production signer or feed."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from threading import Event
from uuid import UUID

import httpx
import pytest

from companion.update_delivery import (check_update, download_update, remove_download,
    delivery_origin, DownloadCancelled, rollout_eligible)
from companion.updates import ReleaseManifest

PIN = "a" * 64
ORIGIN = "https://updates.example.test"
POLICY = {"schema_version": "poltergeist-release-policy-v1", "channel": "private-beta",
    "publishers_sha256": [PIN], "feed_url": ORIGIN + "/release.ps1", "max_installer_bytes": 1024**2}


class Stream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


@pytest.fixture
def candidate(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    payload = bytes(range(256)) * 700
    now = datetime.now(UTC)
    release = ReleaseManifest(version="0.2.0", created_at=now - timedelta(minutes=1), expires_at=now + timedelta(days=1),
        installer_bytes=len(payload), installer_sha256=sha256(payload).hexdigest(), bundle_manifest_sha256="b" * 64,
        installer_url=ORIGIN + "/Setup.exe")
    state = {"release": release, "payload": payload, "headers": {}, "status": 200, "yielded": 0}
    requests, signatures = [], []

    def handler(request):
        requests.append(request)
        if request.url.path == "/release.ps1":
            data = ("# Poltergeist release metadata v1\n# " + state["release"].model_dump_json() + "\n").encode()
            data = state.get("metadata", data)
            return httpx.Response(200, stream=Stream([data]))
        data = state["payload"]
        def chunks():
            for offset in range(0, len(data), 65536):
                state["yielded"] += 1
                yield data[offset:offset + 65536]
        return httpx.Response(state["status"], stream=Stream(chunks()), headers=state["headers"])

    def client():
        return httpx.Client(transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False)

    def verify(path, pins):
        assert pins == [PIN]
        signatures.append(path.name)
        if path.suffix != ".ps1" and state.get("reject_installer"):
            raise ValueError("Fixture native signature rejection")

    options = dict(current_version="0.1.0", _policy=POLICY, _verify=verify, _client_factory=client)
    return profile, state, requests, signatures, options


def test_check_is_metadata_only_and_download_requires_a_separate_explicit_action(candidate):
    profile, state, requests, signatures, options = candidate
    offer = check_update(profile, **options)
    assert offer.release == state["release"]
    assert [r.url.path for r in requests] == ["/release.ps1"]
    assert signatures == ["release.ps1"]
    assert not (offer.manifest.parent / "Setup.exe").exists()
    progress = []
    installer = download_update(profile, offer.manifest, progress=lambda n, total: progress.append((n, total)), **options)
    assert installer.read_bytes() == state["payload"]
    assert [r.url.path for r in requests] == ["/release.ps1", "/Setup.exe"]
    assert signatures[-3:] == ["Setup.download.exe", "release.ps1", "Setup.exe"]
    assert progress[-1] == (len(state["payload"]), len(state["payload"])) and len(progress) == 3
    # Reusing a verified candidate does not redownload it or install anything.
    assert download_update(profile, offer.manifest, **options) == installer and len(requests) == 2
    for request in requests:
        assert not any(name in request.headers for name in ("authorization", "cookie", "x-poltergeist-cohort", "x-device-id"))
        assert request.headers["accept-encoding"] == "identity"


def test_disabled_publisher_or_feed_never_contacts_a_service_or_creates_update_files(candidate):
    profile, state, requests, signatures, options = candidate
    for policy, message in ((POLICY | {"publishers_sha256": []}, "disabled"), (POLICY | {"feed_url": None}, "feed")):
        with pytest.raises(ValueError, match=message):
            check_update(profile, **(options | {"_policy": policy}))
    assert not requests and not signatures and not (profile / "updates").exists()


@pytest.mark.parametrize("change", ["old", "rollout_zero", "cross_origin", "bad_metadata", "large_metadata"])
def test_ineligible_or_invalid_manifest_never_downloads_an_installer(candidate, change):
    profile, state, requests, signatures, options = candidate
    if change == "old": state["release"] = state["release"].model_copy(update={"version": "0.1.0"})
    if change == "rollout_zero": state["release"] = state["release"].model_copy(update={"rollout_percent": 0})
    if change == "cross_origin": state["release"] = state["release"].model_copy(update={"installer_url": "https://other.example.test/Setup.exe"})
    if change == "bad_metadata": state["metadata"] = b"Not signed release metadata"
    if change == "large_metadata": state["metadata"] = b"x" * 65537
    if change in {"old", "rollout_zero"}:
        assert check_update(profile, **options) is None
    else:
        with pytest.raises(ValueError): check_update(profile, **options)
    assert len(requests) == 1 and requests[0].url.path == "/release.ps1"
    assert list((profile / "updates/downloads").iterdir()) == []


@pytest.mark.parametrize("change", ["truncated", "oversized", "corrupt", "compressed", "length", "redirect", "signature"])
def test_failed_download_leaves_the_offer_and_removes_only_its_partial_installer(candidate, change):
    profile, state, requests, signatures, options = candidate
    offer = check_update(profile, **options)
    if change == "truncated": state["payload"] = state["payload"][:-1]
    if change == "oversized": state["payload"] += b"extra"
    if change == "corrupt": state["payload"] = b"z" * len(state["payload"])
    if change == "compressed": state["headers"] = {"Content-Encoding": "gzip"}
    if change == "length": state["headers"] = {"Content-Length": "1"}
    if change == "redirect": state["status"], state["headers"] = 302, {"Location": "https://other.example.test/Setup.exe"}
    if change == "signature": state["reject_installer"] = True
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        download_update(profile, offer.manifest, **options)
    assert list(offer.manifest.parent.iterdir()) == [offer.manifest]
    assert len(requests) == 2


def test_cancel_and_competing_download_do_not_publish_or_delete_another_transfers_file(candidate):
    profile, state, requests, signatures, options = candidate
    offer = check_update(profile, **options)
    cancel = Event()
    with pytest.raises(DownloadCancelled):
        download_update(profile, offer.manifest, cancel=cancel, progress=lambda *_: cancel.set(), **options)
    assert state["yielded"] <= 2 and list(offer.manifest.parent.iterdir()) == [offer.manifest]
    partial = offer.manifest.parent / "Setup.download.exe"
    partial.write_bytes(b"Another active transfer owns this file")
    with pytest.raises(FileExistsError): download_update(profile, offer.manifest, **options)
    assert partial.read_bytes() == b"Another active transfer owns this file"


def test_removal_cannot_target_a_backup_or_unknown_files(candidate):
    profile, state, requests, signatures, options = candidate
    offer = check_update(profile, **options)
    download_update(profile, offer.manifest, **options)
    backup = profile / "updates/backup"
    backup.mkdir()
    (backup / "database.sqlite").write_bytes(b"Preserve this backup")
    with pytest.raises(ValueError): remove_download(profile, backup / "release.ps1")
    foreign = offer.manifest.parent / "receipt.json"
    foreign.write_text("Preserve unexpected files")
    with pytest.raises(ValueError): remove_download(profile, offer.manifest)
    assert offer.manifest.exists()
    foreign.unlink()
    remove_download(profile, offer.manifest)
    assert not offer.manifest.parent.exists() and (backup / "database.sqlite").read_bytes() == b"Preserve this backup"


def test_staged_population_is_stable_local_and_reaches_zero_and_full(candidate):
    profile, state, requests, signatures, options = candidate
    release = state["release"]
    cohorts = [str(UUID(int=n)) for n in range(1000)]
    partial = release.model_copy(update={"rollout_percent": 25, "rollout_salt": "first-beta"})
    eligible = [rollout_eligible(partial, value) for value in cohorts]
    assert eligible == [rollout_eligible(partial, value) for value in cohorts]
    assert 180 < sum(eligible) < 320
    assert all(rollout_eligible(release, value) for value in cohorts)
    assert not any(rollout_eligible(release.model_copy(update={"rollout_percent": 0}), value) for value in cohorts)


@pytest.mark.parametrize("url", ["http://updates.example.test/a", "https://user:secret@example.test/a", "https://example.test/a?token=x",
    "https://example.test/a#x", "https://example.test:bad/a", "https://example.test/a\\b", "https://example.test/\na"])
def test_delivery_url_rejects_credentials_insecure_transport_and_ambiguous_locations(url):
    with pytest.raises(ValueError, match="HTTPS"): delivery_origin(url)


def test_disk_reserve_is_checked_before_an_installer_request(candidate, monkeypatch):
    from types import SimpleNamespace
    profile, state, requests, signatures, options = candidate
    offer = check_update(profile, **options)
    monkeypatch.setattr("companion.update_delivery.shutil.disk_usage", lambda _: SimpleNamespace(free=512 * 1024**2 + len(state["payload"]) - 1))
    with pytest.raises(ValueError, match="disk space"):
        download_update(profile, offer.manifest, **options)
    assert len(requests) == 1 and list(offer.manifest.parent.iterdir()) == [offer.manifest]


def test_deadline_checks_each_slow_partial_transport_read(tmp_path):
    from companion.update_delivery import _fetch
    elapsed, sent = [0], []
    def chunks():
        for _ in range(10):
            elapsed[0] += 100
            sent.append(1)
            yield b"x"
    target = tmp_path / "partial.exe"
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream(chunks())))) as client:
        with pytest.raises(ValueError, match="deadline"):
            _fetch(client, ORIGIN + "/Setup.exe", target, maximum=10, expected=10, clock=lambda: elapsed[0])
    assert len(sent) == 6 and not target.exists()


def test_large_download_memory_stays_bounded_by_chunks_and_hashing(candidate):
    import tracemalloc
    profile, state, requests, signatures, options = candidate
    state["payload"] = b"a" * (8 * 1024**2)
    state["release"] = state["release"].model_copy(update={"installer_bytes": len(state["payload"]),
        "installer_sha256": sha256(state["payload"]).hexdigest()})
    options = options | {"_policy": POLICY | {"max_installer_bytes": 10 * 1024**2}}
    offer = check_update(profile, **options)
    tracemalloc.start()
    try:
        installer = download_update(profile, offer.manifest, **options)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert installer.stat().st_size == 8 * 1024**2 and peak < 4 * 1024**2
