"""Atomic, storage-relative artifacts and streamed raw sensor recordings.

This module owns generated UUID directories only. Constructing a store does not
touch the filesystem; application startup explicitly calls ``initialize``.
"""

from collections.abc import Iterable, Mapping
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
from threading import Lock
from time import sleep
from typing import Any
from uuid import UUID, uuid4

from evaluation.validation import validate_frames
from shared.models import ArtifactReference, TelemetryFrame
from simulator.protocols import SensorSample


_LAYOUT = {
    "telemetry": ("telemetry", "frames.ndjson", "application/x-ndjson"),
    "recording": ("recordings", "manifest.json", "application/json"),
    "report": ("reports", "report.json", "application/json"),
    "decisions": ("decisions", "timeline.ndjson", "application/x-ndjson"),
}
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.IGNORECASE)
_SAMPLE_FILE = re.compile(r"^\d{8,}-(?:camera|lidar)\.bin$")
_MAX_METADATA_BYTES = 16 * 1024
_MAX_SAMPLE_BYTES = 64 * 1024 * 1024
_PUBLISH_RETRY_DELAYS = (0.01, 0.025, 0.05, 0.1)


class ArtifactError(ValueError):
    """An artifact does not satisfy the store's path or data contract."""


def _uuid(value: UUID | str) -> str:
    if not isinstance(value, (str, UUID)):
        raise ArtifactError("Simulation and artifact IDs must be UUIDs")
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError) as error:
        raise ArtifactError("Simulation and artifact IDs must be UUIDs") from error


def _json(value: Any) -> str:
    def check(item: Any) -> None:
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ArtifactError("JSON object keys must be strings")
            for nested in item.values():
                check(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                check(nested)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ArtifactError("Artifact payload must contain JSON values")
    try:
        check(value)
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError) as error:
        raise ArtifactError(f"Invalid JSON artifact: {error}") from error


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _parts(path: str) -> tuple[str, ...]:
    if not isinstance(path, str) or not path or "\\" in path or ":" in path:
        raise ArtifactError("Artifact paths must use relative forward-slash components")
    parts = tuple(path.split("/"))
    for part in parts:
        if (
            not part or part in (".", "..") or part.endswith((".", " "))
            or any(ord(character) < 32 for character in part)
            or any(character in '<>"|?*' for character in part)
            or _RESERVED.match(part)
        ):
            raise ArtifactError("Unsafe artifact path component")
    return parts


def _sample_stream(kind: str, metadata: Mapping[str, Any]) -> tuple[str, str | None]:
    """Separate declared sensor IDs from each other and from legacy streams."""
    sensor_id = metadata.get("agent_sensor_id")
    if "agent_sensor_id" in metadata and (type(sensor_id) is not str or not sensor_id.strip()):
        raise ArtifactError("Agent sensor IDs must be nonempty strings when present")
    return kind, sensor_id


def _sample_header(kind: Any, frame: Any, timestamp: Any, length: int, metadata: Any) -> dict:
    if kind not in ("camera", "lidar"):
        raise ArtifactError("Only raw camera and LiDAR samples are supported")
    if type(frame) is not int or frame < 0:
        raise ArtifactError("Sensor frames must be nonnegative integers")
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
        raise ArtifactError("Sensor timestamps must be finite nonnegative numbers")
    if not isinstance(metadata, Mapping):
        raise ArtifactError("Sensor metadata must be a JSON object")
    metadata = dict(metadata)
    _sample_stream(kind, metadata)
    if len(_json(metadata).encode("utf-8")) > _MAX_METADATA_BYTES:
        raise ArtifactError("Sensor metadata exceeds 16 KiB")
    if length > _MAX_SAMPLE_BYTES:
        raise ArtifactError("A raw sensor sample exceeds 64 MiB")
    if kind == "camera":
        width, height = metadata.get("width"), metadata.get("height")
        if (
            metadata.get("encoding") != "bgra8"
            or type(width) is not int or type(height) is not int
            or not 1 <= width <= 4096 or not 1 <= height <= 4096
            or length != width * height * 4
        ):
            raise ArtifactError("Camera samples require matching positive BGRA8 dimensions")
    else:
        channels = metadata.get("channels")
        if (
            metadata.get("encoding") != "float32_xyzi"
            or type(channels) is not int or not 1 <= channels <= 4096
            or length % 16
        ):
            raise ArtifactError("LiDAR samples require float32 XYZI points and positive channels")
    return metadata


class ArtifactStore:
    def __init__(self, root: Path | str):
        self.root = Path(root).absolute()
        self._initialized = False
        self._canonical_root: Path | None = None
        self._publish_lock = Lock()

    def initialize(self) -> None:
        if _linked(self.root):
            raise ArtifactError("The artifact root must not be a symlink or junction")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir() or _linked(self.root):
            raise ArtifactError("Artifact storage must be a directory")
        self._canonical_root = self.root.resolve(strict=True)
        self._initialized = True

    def _contained(self, path: Path, *, exists: bool = False) -> Path:
        if not self._initialized or self._canonical_root is None:
            raise FileNotFoundError("Artifact storage has not been initialized")
        if _linked(self.root) or self.root.resolve(strict=True) != self._canonical_root:
            raise ArtifactError("Artifact storage root changed or became a link")
        try:
            relative = path.relative_to(self.root)
        except ValueError as error:
            raise ArtifactError("Artifact path is outside storage") from error
        if ".." in relative.parts:
            raise ArtifactError("Artifact paths must not contain parent traversal")
        cursor = self.root
        for part in relative.parts:
            cursor /= part
            if _linked(cursor):
                raise ArtifactError("Artifact paths must not cross symlinks or junctions")
        # Windows non-strict realpath can retain its extended device prefix if
        # a missing ancestor appears between native lookup retries. That makes
        # the same physical root compare as a different path under concurrency.
        # Strictly resolve the nearest existing ancestor, then append only the
        # already checked missing components; never use a non-strict result.
        ancestor = path
        missing: list[str] = []
        while True:
            try:
                resolved = ancestor.resolve(strict=True)
                break
            except FileNotFoundError:
                if exists or ancestor == self.root:
                    raise
                missing.append(ancestor.name)
                ancestor = ancestor.parent
        resolved = resolved.joinpath(*reversed(missing))
        if not resolved.is_relative_to(self._canonical_root):
            raise ArtifactError("Artifact path resolves outside storage")
        return path

    def _reference(self, kind: str, run_id: str, artifact_id: str) -> ArtifactReference:
        namespace, filename, media_type = _LAYOUT[kind]
        return ArtifactReference(kind=kind, path=f"{namespace}/{run_id}/{artifact_id}/{filename}", media_type=media_type)

    def validate_reference(self, ref: ArtifactReference, simulation_id: UUID | str | None = None) -> Path:
        # Revalidate mutable Pydantic instances too, rather than trusting construction.
        _parts(ref.path)
        ref = ArtifactReference.model_validate({name: getattr(ref, name) for name in ArtifactReference.model_fields})
        parts = _parts(ref.path)
        namespace, filename, media_type = _LAYOUT[ref.kind]
        if len(parts) != 4 or parts[0] != namespace or parts[3] != filename or ref.media_type != media_type:
            raise ArtifactError("Artifact reference does not match its generated kind layout")
        if parts[1] != _uuid(parts[1]) or parts[2] != _uuid(parts[2]):
            raise ArtifactError("Artifact path IDs must be canonical UUIDs")
        if simulation_id is not None and parts[1] != _uuid(simulation_id):
            raise ArtifactError("Artifact belongs to another simulation")
        path = self._contained(self.root.joinpath(*parts), exists=True)
        if not path.is_file():
            raise ArtifactError("Artifact reference must name a regular file")
        return path

    def _begin(self, kind: str, simulation_id: UUID | str) -> tuple[Path, Path, ArtifactReference]:
        run_id, artifact_id = _uuid(simulation_id), str(uuid4())
        ref = self._reference(kind, run_id, artifact_id)
        parent = self.root / _LAYOUT[kind][0] / run_id
        with self._publish_lock:
            self._contained(parent)
            # Shared ancestors are created under the same lock as publication.
            # Strict ancestor resolution also supports independent store objects
            # sharing the root, whose locks cannot coordinate with this one.
            for directory in (parent.parent, parent):
                self._contained(directory)
                directory.mkdir(exist_ok=True)
                self._contained(directory, exists=True)
            final = parent / artifact_id
            stage = parent / f".{artifact_id}.staging"
            self._contained(final)
            self._contained(stage)
            if final.exists():
                raise FileExistsError("A published artifact already owns this UUID")
            stage.mkdir()
            return stage, final, ref

    def _discard(self, directory: Path) -> None:
        self._contained(directory)
        if not directory.exists():
            return
        # No links may be traversed, even during rollback/removal.
        for parent, directories, files in os.walk(directory, followlinks=False):
            for name in (*directories, *files):
                self._contained(Path(parent) / name, exists=True)
        shutil.rmtree(directory)

    def _publish(self, stage: Path, final: Path) -> None:
        with self._publish_lock:
            for attempt in range(len(_PUBLISH_RETRY_DELAYS) + 1):
                self._contained(stage, exists=True)
                self._contained(final)
                if final.exists():
                    raise FileExistsError("Published artifacts cannot be overwritten")
                try:
                    # Both directories share a filesystem, so readers see one
                    # complete directory. Windows sync/indexing handles can
                    # briefly deny this rename; recheck paths before each retry.
                    stage.rename(final)
                    return
                except OSError as error:
                    if (
                        getattr(error, "winerror", None) not in (5, 32, 33)
                        or attempt == len(_PUBLISH_RETRY_DELAYS)
                    ):
                        raise
                    sleep(_PUBLISH_RETRY_DELAYS[attempt])

    def _rollback(self, stage: Path, final: Path, owner: os.stat_result) -> None:
        self._discard(stage)
        self._contained(final)
        # A signal may arrive after rename completed but before _publish returned.
        # The original directory identity distinguishes our publication from a
        # competing UUID collision, which rollback must never delete.
        if final.exists() and os.path.samestat(owner, final.stat()):
            self._discard(final)

    def _write(self, kind: str, simulation_id: UUID | str, content: Iterable[bytes]) -> ArtifactReference:
        stage, final, ref = self._begin(kind, simulation_id)
        owner = stage.stat()
        try:
            with (stage / _LAYOUT[kind][1]).open("xb") as output:
                for chunk in content:
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            self._publish(stage, final)
            return ref
        except BaseException:
            self._rollback(stage, final, owner)
            raise

    def write_telemetry(self, simulation_id: UUID | str, frames: Iterable[TelemetryFrame]) -> ArtifactReference:
        validated = validate_frames(frames)
        return self._write("telemetry", simulation_id, (
            (frame.model_dump_json() + "\n").encode("utf-8") for frame in validated
        ))

    def read_telemetry(self, ref: ArtifactReference) -> tuple[TelemetryFrame, ...]:
        if ref.kind != "telemetry":
            raise ArtifactError("Expected a telemetry artifact")
        with self.validate_reference(ref).open("r", encoding="utf-8") as source:
            return tuple(validate_frames(json.loads(line) for line in source))

    def write_report(self, simulation_id: UUID | str, payload: Mapping[str, Any]) -> ArtifactReference:
        if not isinstance(payload, Mapping):
            raise ArtifactError("Report payload must be a JSON object")
        return self._write("report", simulation_id, [_json(dict(payload)).encode("utf-8")])

    def read_bytes(self, ref: ArtifactReference) -> bytes:
        return self.validate_reference(ref).read_bytes()

    def remove(self, ref: ArtifactReference) -> None:
        # Validation permits only a generated UUID artifact directory, never a run
        # or namespace directory. Removal is for a just-created transaction rollback.
        self._discard(self.validate_reference(ref).parent)

    def capture(self, simulation_id: UUID | str) -> "CaptureWriter":
        return CaptureWriter(self, _uuid(simulation_id))

    def read_capture_manifest(self, ref: ArtifactReference) -> dict[str, Any]:
        if ref.kind != "recording":
            raise ArtifactError("Expected a raw sensor recording")
        path = self.validate_reference(ref)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict) or manifest.get("format") != "raw-sensor-v1"
            or manifest.get("simulation_id") != path.parent.parent.name
            or manifest.get("artifact_id") != path.parent.name
            or not isinstance(manifest.get("samples"), list)
        ):
            raise ArtifactError("Invalid raw sensor manifest")
        last: dict[tuple[str, str | None], tuple[int, float]] = {}
        total = 0
        for index, item in enumerate(manifest["samples"]):
            if not isinstance(item, dict) or not isinstance(item.get("file"), str) or not _SAMPLE_FILE.fullmatch(item["file"]):
                raise ArtifactError("Invalid raw sample filename")
            kind = item.get("kind")
            if item["file"] != f"{index:08d}-{kind}.bin":
                raise ArtifactError("Raw sample filenames must preserve manifest order")
            blob = self._contained(path.parent / item["file"], exists=True)
            length = blob.stat().st_size
            metadata = _sample_header(kind, item.get("frame"), item.get("timestamp"), length, item.get("metadata"))
            stream = _sample_stream(kind, metadata)
            previous = last.get(stream)
            if previous is not None and (item["frame"] <= previous[0] or item["timestamp"] <= previous[1]):
                raise ArtifactError("Raw sensor streams must increase by frame and timestamp")
            last[stream] = item["frame"], item["timestamp"]
            digest = sha256()
            with blob.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if type(item.get("byte_length")) is not int or item["byte_length"] != length or item.get("sha256") != digest.hexdigest():
                raise ArtifactError("Raw sensor sample size or SHA256 does not match")
            total += length
        if (
            type(manifest.get("sample_count")) is not int or manifest["sample_count"] != len(manifest["samples"])
            or type(manifest.get("total_bytes")) is not int or manifest["total_bytes"] != total
        ):
            raise ArtifactError("Raw sensor manifest totals do not match samples")
        return manifest

    def capture_sample_path(self, ref: ArtifactReference, index: int) -> Path:
        """Resolve a verified generated sample for an indexed HTTP download."""
        if type(index) is not int or index < 0:
            raise ArtifactError("Raw sample index must be a nonnegative integer")
        manifest = self.read_capture_manifest(ref)
        if index >= len(manifest["samples"]):
            raise ArtifactError("Raw sample index does not exist")
        path = self.validate_reference(ref).parent / manifest["samples"][index]["file"]
        return self._contained(path, exists=True)


class CaptureWriter:
    """One-shot capture transaction; raw bytes are streamed directly to disk."""

    def __init__(self, store: ArtifactStore, simulation_id: str):
        self._store, self._simulation_id = store, simulation_id
        self._stage: Path | None = None
        self._final: Path | None = None
        self._ref: ArtifactReference | None = None
        self._owner: os.stat_result | None = None
        self._closed = False
        self._count = self._bytes = 0
        self._last: dict[tuple[str, str | None], tuple[int, float]] = {}
        self._agent_samples: dict[str, tuple[int, str, int]] = {}

    @property
    def simulation_id(self) -> UUID:
        return UUID(self._simulation_id)

    def belongs_to(self, store: ArtifactStore) -> bool:
        return self._store is store

    def write(self, sample: SensorSample) -> None:
        if self._closed:
            raise RuntimeError("Capture writer is already committed or aborted")
        try:
            if not isinstance(sample, SensorSample) or not isinstance(sample.data, bytes):
                raise ArtifactError("Raw capture requires a SensorSample with immutable bytes")
            metadata = _sample_header(sample.kind, sample.frame, sample.timestamp, len(sample.data), sample.metadata)
            stream = _sample_stream(sample.kind, metadata)
            previous = self._last.get(stream)
            if previous is not None and (sample.frame <= previous[0] or sample.timestamp <= previous[1]):
                raise ArtifactError("Raw sensor streams must strictly increase by frame and timestamp")
            if self._stage is None:
                self._stage, self._final, self._ref = self._store._begin("recording", self._simulation_id)
                self._owner = self._stage.stat()
            self._store._contained(self._stage, exists=True)
            filename = f"{self._count:08d}-{sample.kind}.bin"
            with self._store._contained(self._stage / filename).open("xb") as output:
                output.write(sample.data)
                output.flush()
                os.fsync(output.fileno())
            entry = {
                "kind": sample.kind, "frame": sample.frame, "timestamp": sample.timestamp,
                "file": filename, "byte_length": len(sample.data),
                "sha256": sha256(sample.data).hexdigest(), "metadata": metadata,
            }
            # Spool metadata as well as bytes, keeping capture memory bounded by
            # one callback/sample rather than the length of the run.
            with self._store._contained(self._stage / ".samples.jsonl").open("ab") as manifest:
                manifest.write((_json(entry) + "\n").encode("utf-8"))
            self._last[stream] = sample.frame, sample.timestamp
            if stream[1] is not None:
                self._agent_samples[stream[1]] = (sample.frame, entry["sha256"], self._count)
            self._count += 1
            self._bytes += len(sample.data)
        except BaseException:
            self.abort()
            raise

    def agent_sample_index(self, sensor_id: str, frame: int, digest: str) -> int:
        """Bind an input to bytes already captured; memory is bounded by rig size."""
        sample = self._agent_samples.get(sensor_id)
        if sample is None or sample[:2] != (frame, digest):
            raise ArtifactError("Decision sensor input has no matching captured sample")
        return sample[2]

    def commit(self) -> ArtifactReference | None:
        if self._closed:
            raise RuntimeError("Capture writer is already committed or aborted")
        try:
            if self._stage is None:
                self._closed = True
                return None
            assert self._ref is not None and self._final is not None
            header = {
                "format": "raw-sensor-v1", "simulation_id": self._simulation_id,
                "artifact_id": self._final.name, "sample_count": self._count,
                "total_bytes": self._bytes,
            }
            self._store._contained(self._stage, exists=True)
            with self._store._contained(self._stage / "manifest.json").open("xb") as target:
                target.write((_json(header)[:-1] + ',"samples":[').encode("utf-8"))
                with self._store._contained(self._stage / ".samples.jsonl", exists=True).open("rb") as source:
                    for index, line in enumerate(source):
                        if index:
                            target.write(b",")
                        target.write(line.rstrip(b"\n"))
                target.write(b"]}")
                target.flush()
                os.fsync(target.fileno())
            (self._stage / ".samples.jsonl").unlink()
            self._store._publish(self._stage, self._final)
            self._closed = True
            return self._ref
        except BaseException:
            self.abort()
            raise

    def abort(self) -> None:
        if self._closed:
            return
        if self._stage is not None:
            assert self._final is not None and self._owner is not None
            self._store._rollback(self._stage, self._final, self._owner)
        self._closed = True
