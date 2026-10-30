"""Generated artifact ownership, atomic publication, and raw sensor provenance."""

from concurrent.futures import ThreadPoolExecutor
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import struct
from uuid import uuid4

import pytest

from backend.storage import ArtifactError, ArtifactStore
from shared.models import ActorState, ArtifactReference, TelemetryFrame, TrafficRule
from simulator.protocols import SensorSample


@pytest.fixture
def store(tmp_path):
    value = ArtifactStore(tmp_path / "storage")
    value.initialize()
    return value


def frames():
    return [
        TelemetryFrame(frame=10, time_s=0, ego=ActorState(actor_id="ego")),
        TelemetryFrame(
            frame=11, time_s=0.05, ego=ActorState(actor_id="ego"), actors=[],
            collisions=[], traffic_violations=[], rule_checks=[TrafficRule.SPEEDING],
        ),
    ]


def camera(frame=10, timestamp=1.5, **changes):
    values = dict(kind="camera", frame=frame, timestamp=timestamp, data=b"\x00\x01\x02\xff" * 2,
                  metadata={"encoding": "bgra8", "width": 2, "height": 1})
    values.update(changes)
    return SensorSample(**values)


def lidar(frame=10, timestamp=1.5, **changes):
    values = dict(kind="lidar", frame=frame, timestamp=timestamp, data=struct.pack("ffff", 1, 2, 3, 0.5),
                  metadata={"encoding": "float32_xyzi", "channels": 32})
    values.update(changes)
    return SensorSample(**values)


def stages(store):
    return list(store.root.glob("*/*/.*.staging"))


def test_constructor_and_capture_are_inert_and_initialize_is_explicit(tmp_path):
    root = tmp_path / "absent" / "storage"
    store = ArtifactStore(root)
    capture = store.capture(uuid4())
    assert not root.exists()
    assert capture.commit() is None
    with pytest.raises(FileNotFoundError, match="initialized"):
        store.write_telemetry(uuid4(), [])
    assert not root.exists()
    store.initialize()
    store.initialize()
    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_telemetry_roundtrip_preserves_unknowns_empty_feeds_and_partial_rules(store):
    run_id = uuid4()
    ref = store.write_telemetry(run_id, frames())
    assert ref.kind == "telemetry" and ref.media_type == "application/x-ndjson"
    assert store.validate_reference(ref, simulation_id=run_id).is_file()
    restored = store.read_telemetry(ref)
    assert restored == tuple(frames())
    assert restored[0].actors is None and restored[1].actors == []
    assert restored[0].rule_checks is None and restored[1].rule_checks == [TrafficRule.SPEEDING]
    assert store.read_bytes(ref).endswith(b"\n")
    assert not stages(store)


def test_empty_trace_is_a_real_empty_artifact(store):
    ref = store.write_telemetry(uuid4(), [])
    assert store.read_bytes(ref) == b""
    assert store.read_telemetry(ref) == ()


@pytest.mark.parametrize("mutation", ["frame_order", "time_order", "changed_ego", "invalid_mutated_model"])
def test_invalid_trace_is_rejected_before_any_artifact_is_written(store, mutation):
    trace = frames()
    if mutation == "frame_order":
        trace[1].frame = 10
    elif mutation == "time_order":
        trace[1].time_s = 0
    elif mutation == "changed_ego":
        trace[1].ego.actor_id = "other"
    else:
        trace[1].time_s = float("nan")
    with pytest.raises(ValueError):
        store.write_telemetry(uuid4(), trace)
    assert list(store.root.iterdir()) == []


def test_telemetry_read_revalidates_disk_content(store):
    ref = store.write_telemetry(uuid4(), frames())
    path = store.validate_reference(ref)
    path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="increase"):
        store.read_telemetry(ref)


def test_report_roundtrip_and_owner_validation(store):
    run_id = uuid4()
    ref = store.write_report(run_id, {"summary": "雪", "unknown": None, "observed": [True, 0]})
    assert json.loads(store.read_bytes(ref)) == {"summary": "雪", "unknown": None, "observed": [True, 0]}
    with pytest.raises(ArtifactError, match="another simulation"):
        store.validate_reference(ref, simulation_id=uuid4())


@pytest.mark.parametrize("payload", [{"bad": float("inf")}, {"bad": float("nan")}, {1: "coercion"}, {"object": object()}, []])
def test_reports_require_finite_json_objects(store, payload):
    with pytest.raises(ArtifactError):
        store.write_report(uuid4(), payload)
    assert list(store.root.iterdir()) == []


@pytest.mark.parametrize("path", [
    "/reports/whatever", "../outside.json", "reports/../outside.json", "C:/file.json",
    "//server/share/file.json", "reports\\run\\artifact\\report.json", "reports/a:b/file.json",
    "reports//artifact/report.json", "reports/./artifact/report.json", "reports/CON/a/report.json",
    "reports/aux.txt/a/report.json", "reports/NUL/a/report.json", "reports/trailing./a/report.json",
    "reports/trailing /a/report.json", "reports/a/a/report.json\x00", "reports/a/a/report.json ",
])
def test_reference_path_tricks_cannot_access_storage(store, path):
    # model_construct also covers references tampered after contract validation.
    ref = ArtifactReference.model_construct(kind="report", path=path, media_type="application/json")
    with pytest.raises((ArtifactError, ValueError)):
        store.validate_reference(ref)


def test_reference_requires_kind_filename_media_type_and_canonical_ids(store):
    ref = store.write_report(uuid4(), {})
    for changes in (
        {"kind": "telemetry"}, {"media_type": "text/plain"},
        {"path": ref.path.replace("report.json", "other.json")},
        {"path": ref.path.replace(ref.path.split("/")[1], "not-a-uuid")},
        {"path": ref.path.upper()},
    ):
        altered = ref.model_copy(update=changes)
        with pytest.raises((ArtifactError, ValueError)):
            store.validate_reference(altered)


def test_missing_reference_and_wrong_read_kind_are_rejected(store):
    ref = store.write_report(uuid4(), {})
    with pytest.raises(ArtifactError, match="telemetry"):
        store.read_telemetry(ref)
    with pytest.raises(ArtifactError, match="recording"):
        store.read_capture_manifest(ref)
    store.remove(ref)
    with pytest.raises(FileNotFoundError):
        store.read_bytes(ref)


def test_remove_owns_one_artifact_directory_without_touching_other_attempts(store):
    run_id = uuid4()
    first = store.write_report(run_id, {"attempt": 1})
    second = store.write_report(run_id, {"attempt": 2})
    first_directory = store.validate_reference(first).parent
    store.remove(first)
    assert not first_directory.exists()
    assert store.validate_reference(second).is_file()
    assert json.loads(store.read_bytes(second))["attempt"] == 2


def test_atomic_publish_failure_removes_owned_partial_directory(store, monkeypatch):
    def fail(*args):
        raise KeyboardInterrupt("interrupted publish")
    monkeypatch.setattr(store, "_publish", fail)
    with pytest.raises(KeyboardInterrupt):
        store.write_telemetry(uuid4(), frames())
    assert not stages(store)
    assert not list(store.root.glob("*/*/*/frames.ndjson"))


@pytest.mark.parametrize("written", [0, 3])
@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_failed_write_releases_reserved_quota_without_discounting_other_artifacts(store, monkeypatch, written, failure):
    existing = store.write_report(uuid4(), {"preserved": True})
    used = store.capacity()["used_bytes"]
    original = Path.open

    class FailingStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, data):
            self.stream.write(data[:written])
            self.stream.flush()
            raise failure("interrupted evidence write")

    def open_stream(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        return FailingStream(stream) if args and args[0] == "xb" else stream

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", open_stream)
        with pytest.raises(failure, match="interrupted evidence"):
            store._write("report", uuid4(), [b"new evidence"])

    assert store.capacity()["used_bytes"] == used
    assert not stages(store)
    assert json.loads(store.read_bytes(existing)) == {"preserved": True}
    store.remove(existing)
    assert store.capacity()["used_bytes"] == 0


def test_initialize_retries_a_failed_scan_without_losing_or_double_counting_usage(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    for namespace in ("telemetry", "reports"):
        directory = root / namespace
        directory.mkdir(parents=True)
        (directory / "existing").write_bytes(b"12345")
    value = ArtifactStore(root)
    contained = value._contained

    def fail_scan(path, **kwargs):
        if path.name == "existing" and path.parent.name == "reports":
            raise PermissionError("evidence temporarily unavailable")
        return contained(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(value, "_contained", fail_scan)
        with pytest.raises(PermissionError, match="temporarily unavailable"):
            value.initialize()
    with pytest.raises(FileNotFoundError, match="initialized"):
        value.write_report(uuid4(), {})
    value.initialize()
    value.initialize()
    assert value.capacity()["used_bytes"] == 10


def test_initialize_does_not_silently_skip_unreadable_namespaces(tmp_path, monkeypatch):
    directory = tmp_path / "reports"
    directory.mkdir()
    (directory / "existing").write_bytes(b"12345")
    value = ArtifactStore(tmp_path)
    scandir = os.scandir

    def unavailable(path):
        if Path(path) == directory:
            raise PermissionError("cannot scan reports")
        return scandir(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", unavailable)
        with pytest.raises(PermissionError, match="cannot scan reports"):
            value.initialize()
    value.initialize()
    assert value.capacity()["used_bytes"] == 5


@pytest.mark.parametrize("maximum_write", [0, 3])
def test_short_writes_are_completed_or_rolled_back_before_publication(store, monkeypatch, maximum_write):
    original = Path.open

    class ShortStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, data):
            return self.stream.write(data[:maximum_write])

    def open_stream(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        return ShortStream(stream) if args and args[0] == "xb" else stream

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", open_stream)
        if maximum_write:
            ref = store._write("report", uuid4(), [b"complete evidence"])
        else:
            with pytest.raises(OSError, match="progress"):
                store._write("report", uuid4(), [b"complete evidence"])
    if maximum_write:
        assert store.read_bytes(ref) == b"complete evidence"
        assert store.capacity()["used_bytes"] == len(b"complete evidence")
    else:
        assert store.capacity()["used_bytes"] == 0
    assert not stages(store)


def windows_rename_error(code):
    error = PermissionError("native directory rename denied")
    error.winerror = code
    return error


@pytest.mark.parametrize("code", [5, 32, 33])
@pytest.mark.parametrize("capture", [False, True])
def test_transient_windows_rename_denial_retries_atomic_publication(store, monkeypatch, code, capture):
    import backend.storage as module
    original = Path.rename
    attempts, delays = [], []
    def rename(path, target):
        attempts.append((path, target))
        if len(attempts) == 1:
            raise windows_rename_error(code)
        return original(path, target)
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(module, "sleep", delays.append)
    if capture:
        writer = store.capture(uuid4())
        writer.write(camera())
        ref = writer.commit()
        assert store.read_capture_manifest(ref)["sample_count"] == 1
    else:
        ref = store.write_report(uuid4(), {"complete": True})
        assert json.loads(store.read_bytes(ref)) == {"complete": True}
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert delays == [module._PUBLISH_RETRY_DELAYS[0]]
    assert not stages(store)


@pytest.mark.parametrize("capture", [False, True])
def test_persistent_windows_rename_denial_has_bounded_retries_and_owned_cleanup(store, monkeypatch, capture):
    import backend.storage as module
    run_id = uuid4()
    sibling = store.write_report(run_id, {"preserve": True})
    attempts, delays = [], []
    error = windows_rename_error(5)
    def rename(path, target):
        attempts.append((path, target))
        raise error
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(module, "sleep", delays.append)
    with pytest.raises(PermissionError) as raised:
        if capture:
            writer = store.capture(run_id)
            writer.write(camera())
            writer.commit()
        else:
            store.write_report(run_id, {"complete": True})
    assert raised.value is error
    assert len(attempts) == len(module._PUBLISH_RETRY_DELAYS) + 1
    assert delays == list(module._PUBLISH_RETRY_DELAYS)
    assert sum(delays) < 0.5
    assert not stages(store)
    assert json.loads(store.read_bytes(sibling)) == {"preserve": True}
    assert len(list(store.root.glob("*/*/*/*.json"))) == 1


@pytest.mark.parametrize("error", [
    PermissionError(errno.EACCES, "non-Windows permission denied"),
    OSError(errno.ENOSPC, "disk full"),
    windows_rename_error(87),
])
def test_unrelated_rename_errors_fail_without_retry(store, monkeypatch, error):
    import backend.storage as module
    attempts, delays = [], []
    def rename(path, target):
        attempts.append((path, target))
        raise error
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(module, "sleep", delays.append)
    with pytest.raises(OSError) as raised:
        store.write_report(uuid4(), {})
    assert raised.value is error
    assert len(attempts) == 1 and not delays
    assert not stages(store)
    assert not list(store.root.glob("*/*/*/*.json"))


def test_publication_retry_rechecks_destination_and_preserves_competing_owner(store, monkeypatch):
    import backend.storage as module
    attempts = []
    def rename(path, target):
        attempts.append((path, target))
        raise windows_rename_error(32)
    def competitor(delay):
        final = attempts[-1][1]
        final.mkdir()
        (final / "report.json").write_text('{"owner":"competitor"}', encoding="utf-8")
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(module, "sleep", competitor)
    with pytest.raises(FileExistsError, match="cannot be overwritten"):
        store.write_report(uuid4(), {"owner": "current"})
    assert len(attempts) == 1
    assert json.loads((attempts[0][1] / "report.json").read_text()) == {"owner": "competitor"}
    assert not stages(store)


@pytest.mark.parametrize("capture", [False, True])
def test_interruption_after_atomic_rename_rolls_back_the_owned_publication(store, monkeypatch, capture):
    original = store._publish
    def interrupted(stage, final):
        original(stage, final)
        raise KeyboardInterrupt("signal immediately after rename")
    monkeypatch.setattr(store, "_publish", interrupted)
    with pytest.raises(KeyboardInterrupt):
        if capture:
            writer = store.capture(uuid4())
            writer.write(camera())
            writer.commit()
        else:
            store.write_report(uuid4(), {"saved": True})
    assert not stages(store)
    assert not list(store.root.glob("*/*/*/*.json"))


def test_existing_published_artifact_never_overwritten_on_uuid_collision(store, monkeypatch):
    import backend.storage as module
    run_id, artifact_id = uuid4(), uuid4()
    monkeypatch.setattr(module, "uuid4", lambda: artifact_id)
    ref = store.write_report(run_id, {"saved": True})
    with pytest.raises(FileExistsError):
        store.write_report(run_id, {"saved": False})
    assert json.loads(store.read_bytes(ref)) == {"saved": True}
    assert not stages(store)


def test_concurrent_attempts_publish_distinct_artifacts(store):
    run_id = uuid4()
    with ThreadPoolExecutor(max_workers=8) as workers:
        refs = list(workers.map(lambda number: store.write_report(run_id, {"attempt": number}), range(24)))
    assert len({ref.path for ref in refs}) == 24
    assert sorted(json.loads(store.read_bytes(ref))["attempt"] for ref in refs) == list(range(24))
    assert not stages(store)


def test_missing_path_containment_never_uses_unstable_nonstrict_resolution(store, monkeypatch, tmp_path):
    original = Path.resolve
    def unstable(path, strict=False):
        if not strict and not path.exists():
            # Model a native non-strict lookup returning an incomparable path
            # when its error changes while another writer creates an ancestor.
            return tmp_path / "unrelated-native-resolution"
        return original(path, strict=strict)
    monkeypatch.setattr(Path, "resolve", unstable)
    ref = store.write_report(uuid4(), {"complete": True})
    assert json.loads(store.read_bytes(ref)) == {"complete": True}
    with pytest.raises(ArtifactError, match="parent traversal"):
        store._contained(store.root / ".." / "outside")


def test_concurrent_independent_stores_share_one_root_safely(store):
    stores = [ArtifactStore(store.root) for _ in range(8)]
    for value in stores:
        value.initialize()
    run_id = uuid4()
    def write(number):
        return stores[number % len(stores)].write_report(run_id, {"attempt": number})
    with ThreadPoolExecutor(max_workers=8) as workers:
        refs = list(workers.map(write, range(24)))
    assert len({ref.path for ref in refs}) == 24
    assert sorted(json.loads(store.read_bytes(ref))["attempt"] for ref in refs) == list(range(24))
    assert not stages(store)


def symlink_or_skip(path, target, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("Creating symlinks requires platform privileges")


def test_symlinked_root_and_namespace_cannot_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    symlink_or_skip(alias, outside, directory=True)
    with pytest.raises(ArtifactError, match="symlink"):
        ArtifactStore(alias).initialize()
    store = ArtifactStore(tmp_path / "storage")
    store.initialize()
    symlink_or_skip(store.root / "reports", outside, directory=True)
    with pytest.raises(ArtifactError, match="symlink"):
        store.write_report(uuid4(), {})
    assert list(outside.iterdir()) == []


def test_linked_file_cannot_be_read_or_removed_through_store(store, tmp_path):
    ref = store.write_report(uuid4(), {})
    path = store.validate_reference(ref)
    outside = tmp_path / "outside.json"
    outside.write_text("preserve", encoding="utf-8")
    path.unlink()
    symlink_or_skip(path, outside)
    with pytest.raises(ArtifactError, match="symlink"):
        store.read_bytes(ref)
    with pytest.raises(ArtifactError, match="symlink"):
        store.remove(ref)
    assert outside.read_text() == "preserve"


def test_raw_capture_roundtrip_records_exact_bytes_provenance_and_no_encoded_video(store):
    run_id = uuid4()
    writer = store.capture(run_id)
    assert writer.simulation_id == run_id
    assert writer.belongs_to(store)
    assert not writer.belongs_to(ArtifactStore(store.root))
    writer.write(camera())
    assert not list(store.root.glob("recordings/*/*/manifest.json"))
    writer.write(lidar())
    writer.write(camera(frame=11, timestamp=1.55))
    ref = writer.commit()
    assert ref.kind == "recording" and ref.media_type == "application/json"
    manifest = store.read_capture_manifest(ref)
    assert manifest["format"] == "raw-sensor-v1"
    assert manifest["simulation_id"] == str(run_id)
    assert manifest["sample_count"] == 3 and manifest["total_bytes"] == 32
    path = store.validate_reference(ref)
    for item, sample in zip(manifest["samples"], (camera(), lidar(), camera(11, 1.55)), strict=True):
        assert (path.parent / item["file"]).read_bytes() == sample.data
        assert item["sha256"] == sha256(sample.data).hexdigest()
        assert item["metadata"] == sample.metadata
    assert not stages(store)
    assert not (path.parent / ".samples.jsonl").exists()
    writer.abort()
    assert path.exists()
    with pytest.raises(RuntimeError):
        writer.write(camera(12, 1.6))
    with pytest.raises(RuntimeError):
        writer.commit()


@pytest.mark.parametrize("sample", [
    camera(frame=True), camera(frame=-1), camera(timestamp=float("nan")), camera(timestamp=-1),
    camera(data=b"short"), camera(data=bytearray(8)), camera(kind="video"),
    camera(metadata={"encoding": "rgb8", "width": 2, "height": 1}),
    camera(metadata={"encoding": "bgra8", "width": True, "height": 2}),
    camera(metadata={"encoding": "bgra8", "width": 2, "height": 1, "big": "x" * 17000}),
    camera(metadata={"encoding": "bgra8", "width": 2, "height": 1, "bad": float("inf")}),
    lidar(data=b"123"), lidar(metadata={"encoding": "float32_xyzi", "channels": 0}),
    lidar(metadata={"encoding": "float32_xyzi", "channels": True}),
])
def test_invalid_sample_aborts_writer_and_cannot_be_committed(store, sample):
    writer = store.capture(uuid4())
    writer.write(camera())
    with pytest.raises(ArtifactError):
        writer.write(sample)
    assert not stages(store)
    assert not list(store.root.glob("recordings/*/*/manifest.json"))
    writer.abort()
    with pytest.raises(RuntimeError):
        writer.commit()


@pytest.mark.parametrize("second", [camera(), camera(9, 1.6), camera(11, 1.4), camera(11, 1.5)])
def test_duplicate_or_reordered_frames_per_stream_abort_capture(store, second):
    writer = store.capture(uuid4())
    writer.write(camera())
    with pytest.raises(ArtifactError, match="increase"):
        writer.write(second)
    assert not stages(store)


def test_capture_abort_is_idempotent_and_preserves_sibling_artifacts(store):
    run_id = uuid4()
    report = store.write_report(run_id, {})
    writer = store.capture(run_id)
    writer.write(camera())
    writer.abort()
    writer.abort()
    assert not stages(store)
    assert store.validate_reference(report).is_file()


def test_capture_commit_failure_cleans_stage_and_empty_capture_has_no_files(store, monkeypatch):
    assert store.capture(uuid4()).commit() is None
    writer = store.capture(uuid4())
    writer.write(lidar())
    def fail(*args):
        raise OSError("disk rename failed")
    monkeypatch.setattr(store, "_publish", fail)
    with pytest.raises(OSError, match="disk rename"):
        writer.commit()
    assert not stages(store)
    with pytest.raises(RuntimeError):
        writer.commit()


def test_manifest_reader_detects_corruption_and_sample_path_traversal(store):
    writer = store.capture(uuid4())
    writer.write(camera())
    ref = writer.commit()
    path = store.validate_reference(ref)
    manifest = json.loads(path.read_text())
    blob = path.parent / manifest["samples"][0]["file"]
    blob.write_bytes(b"\x00" * 8)
    with pytest.raises(ArtifactError, match="SHA256"):
        store.read_capture_manifest(ref)
    manifest["samples"][0]["file"] = "../../../outside.bin"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ArtifactError, match="filename"):
        store.read_capture_manifest(ref)


def test_empty_lidar_sweep_is_valid_and_stream_timing_is_independent(store):
    writer = store.capture(uuid4())
    writer.write(camera(frame=20, timestamp=4))
    writer.write(lidar(frame=19, timestamp=3.95, data=b""))
    manifest = store.read_capture_manifest(writer.commit())
    assert manifest["samples"][1]["byte_length"] == 0


def named_sample(factory, sensor_id, *, frame=10, timestamp=1.5):
    sample = factory(frame=frame, timestamp=timestamp)
    return SensorSample(sample.kind, sample.frame, sample.timestamp, sample.data,
        sample.metadata | {"agent_sensor_id": sensor_id, "mount": {"position": {"z": 2.0}}})


def test_same_frame_multiple_sensor_ids_and_legacy_streams_roundtrip_independently(store):
    writer = store.capture(uuid4())
    samples = []
    for frame, timestamp in ((10, 1.5), (11, 1.55)):
        for factory in (camera, lidar):
            samples.append(factory(frame=frame, timestamp=timestamp))
            samples.extend(named_sample(factory, sensor_id, frame=frame, timestamp=timestamp)
                           for sensor_id in ("front", "rear", "legacy"))
    for sample in samples:
        writer.write(sample)
    ref = writer.commit()
    manifest = store.read_capture_manifest(ref)
    assert manifest["sample_count"] == 16
    path = store.validate_reference(ref)
    for index, (item, sample) in enumerate(zip(manifest["samples"], samples, strict=True)):
        assert item["file"] == f"{index:08d}-{sample.kind}.bin"
        assert item["metadata"] == sample.metadata
        assert store.capture_sample_path(ref, index).read_bytes() == sample.data
        assert (path.parent / item["file"]).read_bytes() == sample.data


@pytest.mark.parametrize("factory", [camera, lidar])
@pytest.mark.parametrize("second_clock", [(10, 1.5), (9, 1.6), (11, 1.4), (11, 1.5)])
def test_named_sensor_duplicate_or_reordered_clock_aborts_its_capture(store, factory, second_clock):
    writer = store.capture(uuid4())
    writer.write(named_sample(factory, "front"))
    writer.write(named_sample(factory, "rear"))
    with pytest.raises(ArtifactError, match="increase"):
        writer.write(named_sample(factory, "front", frame=second_clock[0], timestamp=second_clock[1]))
    assert not stages(store)
    assert not list(store.root.glob("recordings/*/*/manifest.json"))


@pytest.mark.parametrize("invalid_id", [None, True, False, 0, 1, [], {}, "", "  \t"])
def test_invalid_agent_sensor_id_cannot_create_a_separate_capture_clock(store, invalid_id):
    writer = store.capture(uuid4())
    writer.write(camera())
    with pytest.raises(ArtifactError, match="nonempty strings"):
        writer.write(named_sample(camera, invalid_id))
    assert not stages(store)


@pytest.mark.parametrize("mutation", ["same_sensor_id", "remove_sensor_id", "duplicate_frame", "duplicate_time"])
def test_manifest_reader_rechecks_named_sensor_clock_after_metadata_or_time_corruption(store, mutation):
    writer = store.capture(uuid4())
    writer.write(camera())
    writer.write(named_sample(camera, "front"))
    writer.write(named_sample(camera, "rear"))
    writer.write(named_sample(camera, "rear", frame=11, timestamp=1.55))
    ref = writer.commit()
    path = store.validate_reference(ref)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "same_sensor_id":
        manifest["samples"][2]["metadata"]["agent_sensor_id"] = "front"
    elif mutation == "remove_sensor_id":
        del manifest["samples"][1]["metadata"]["agent_sensor_id"]
    elif mutation == "duplicate_frame":
        manifest["samples"][3]["frame"] = 10
    else:
        manifest["samples"][3]["timestamp"] = 1.5
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ArtifactError, match="increase"):
        store.read_capture_manifest(ref)


@pytest.mark.parametrize("invalid_id", [None, True, 0, [], {}, "", "  "])
def test_manifest_reader_rejects_invalid_named_sensor_ids(store, invalid_id):
    writer = store.capture(uuid4())
    writer.write(named_sample(camera, "front"))
    ref = writer.commit()
    path = store.validate_reference(ref)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["samples"][0]["metadata"]["agent_sensor_id"] = invalid_id
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ArtifactError, match="nonempty strings"):
        store.read_capture_manifest(ref)
