"""Generated artifact ownership, atomic publication, and raw sensor provenance."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
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
