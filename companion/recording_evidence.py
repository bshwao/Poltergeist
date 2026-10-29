"""Bounded recording discovery and indexed sample lookup; never disclose paths."""

from hashlib import sha256
import json
import re

from backend.storage import ArtifactError
from shared.models.evidence import (CameraFormat, EvidenceRecording, EvidenceSample,
                                    LidarFormat, PAGE_BYTES)
from shared.models.remote import canonical_json

MAX_LINE = 65536
MAX_LEGACY_MANIFEST = 2 * 1024 * 1024
MAX_INDEX_SCAN = 64 * 1024 * 1024
_FILE = re.compile(r"^([0-9]{8,})-(camera|lidar)\.bin$")


class EvidenceLimit(ArtifactError):
    pass


class EvidenceChanged(ArtifactError):
    pass


def plain(path):
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ArtifactError("Evidence must be an ordinary owned file")
    return path


def fingerprint(*paths, sample=None):
    stats = []
    for path in paths:
        value = plain(path).stat()
        stats.append((value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns))
    return sha256(canonical_json({"files": stats, "sample": sample}).encode()).hexdigest()


def _legacy(path):
    with plain(path).open("rb") as source:
        raw = source.read(MAX_LEGACY_MANIFEST + 1)
    if len(raw) > MAX_LEGACY_MANIFEST:
        raise EvidenceLimit("Legacy recording exceeds bounded manifest limits")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("samples"), list):
        raise ArtifactError("Invalid sample catalogue")
    return manifest


def header(path):
    with plain(path).open("rb") as source:
        prefix = source.read(4096)
    marker = prefix.find(b',"samples":[')
    value = json.loads(prefix[:marker] + b"}") if marker >= 0 else _legacy(path)
    if (not isinstance(value, dict) or value.get("format") != "raw-sensor-v1" or value.get("simulation_id") != path.parent.parent.name
            or value.get("artifact_id") != path.parent.name
            or type(value.get("sample_count")) is not int or not 0 <= value["sample_count"] <= 1000001
            or type(value.get("total_bytes")) is not int or not 0 <= value["total_bytes"] <= 2**40):
        raise ArtifactError("Recording identity or totals are invalid")
    return value


def descriptor(item, *, cursor=None):
    if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
        raise ArtifactError("Invalid sample metadata")
    match = _FILE.fullmatch(item.get("file", ""))
    if match is None:
        raise ArtifactError("Invalid sample filename")
    index, kind = int(match[1]), match[2]
    if item["file"] != f"{index:08d}-{kind}.bin" or item.get("kind") != kind:
        raise ArtifactError("Invalid sample identity")
    metadata = item.get("metadata", {})
    shape = (CameraFormat(width=metadata["width"], height=metadata["height"])
             if kind == "camera" else LidarFormat(channels=metadata["channels"]))
    if metadata.get("encoding") != shape.encoding:
        raise ArtifactError("Unsupported recording encoding")
    return EvidenceSample(sample_index=index, index_offset=cursor, kind=kind, frame=item["frame"],
        timestamp=item["timestamp"], byte_length=item["byte_length"], sha256=item["sha256"], metadata=shape)


def catalogue(store, result):
    if len(result.artifacts) > 65:
        raise EvidenceLimit("Too many artifact references")
    recordings = []
    for index, reference in enumerate(result.artifacts):
        if reference.kind != "recording":
            continue
        try:
            path = store.validate_reference(reference, result.simulation.id)
            if store._contained(path.parent / ".deleted").is_file():
                raise FileNotFoundError
            before = fingerprint(path)
            value = header(path)
            if before != fingerprint(path):
                raise EvidenceChanged("Recording changed during discovery")
            entry = EvidenceRecording(artifact_index=index, state="available",
                sample_count=value["sample_count"], total_bytes=value["total_bytes"])
        except FileNotFoundError:
            # validate_reference checked generated layout/ownership first.
            marker = store._contained(store.root.joinpath(*reference.path.split("/")).parent / ".deleted")
            entry = EvidenceRecording(artifact_index=index, state="deleted" if marker.is_file() else "missing")
        except EvidenceLimit:
            entry = EvidenceRecording(artifact_index=index, state="limit_exceeded")
        except (ArtifactError, ValueError, KeyError, TypeError, OSError):
            entry = EvidenceRecording(artifact_index=index, state="missing")
        recordings.append(entry)
    if len(recordings) > 64:
        raise EvidenceLimit("Too many recordings")
    return recordings


def _index(store, path):
    sidecar = store._contained(path.parent / "samples.ndjson")
    return plain(sidecar) if sidecar.exists() else None


def sample_page(store, reference, request):
    path = store.validate_reference(reference, request.simulation_id)
    metadata = header(path)
    index = _index(store, path)
    version = fingerprint(path, *([index] if index is not None else []))
    if request.version is not None and request.version != version:
        raise EvidenceChanged("Recording index changed")
    rows, size = [], 0
    if index is not None:
        total = index.stat().st_size
        if request.offset > total:
            raise EvidenceChanged("Invalid index cursor")
        with index.open("rb") as source:
            if request.offset:
                source.seek(request.offset - 1)
                if source.read(1) != b"\n":
                    raise EvidenceChanged("Index cursor splits a record")
            for _ in range(request.limit):
                start = source.tell()
                line = source.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    raise EvidenceLimit("Sample index row is too large")
                value = descriptor(json.loads(line), cursor=start)
                expected = rows[-1].sample_index + 1 if rows else request.sample_index
                if value.sample_index >= metadata["sample_count"] or value.sample_index != expected:
                    raise ArtifactError("Sample index order changed")
                encoded = canonical_json(value).encode()
                if size + len(encoded) > PAGE_BYTES:
                    source.seek(start)
                    break
                size += len(encoded)
                rows.append(value)
            end = source.tell()
            if end == total and ((rows and rows[-1].sample_index + 1 != metadata["sample_count"])
                    or (not rows and metadata["sample_count"] != 0)):
                raise ArtifactError("Sample index is truncated")
    else:
        # A legacy manifest is bounded to 2 MiB. Give it a deterministic virtual
        # line index so paging does not write or migrate the user's recording.
        manifest = _legacy(path)
        if len(manifest["samples"]) != metadata["sample_count"]:
            raise ArtifactError("Legacy sample count changed")
        total, end = 0, request.offset
        boundary = request.offset == 0
        page_full = False
        for position, item in enumerate(manifest["samples"]):
            value = descriptor(item)
            if value.sample_index != position:
                raise ArtifactError("Legacy sample order changed")
            length = len(canonical_json(item).encode()) + 1
            if total == request.offset:
                boundary = True
            if total >= request.offset and not page_full:
                encoded = canonical_json(value).encode()
                if len(rows) < request.limit and size + len(encoded) <= PAGE_BYTES:
                    rows.append(value)
                    size += len(encoded)
                    end = total + length
                else:
                    page_full = True
            total += length
        if not boundary or request.offset > total:
            raise EvidenceChanged("Invalid virtual index cursor")
    if version != fingerprint(path, *([index] if index is not None else [])):
        raise EvidenceChanged("Recording index changed during read")
    return rows, version, total, end if end < total else None


def sample_location(store, reference, request):
    path = store.validate_reference(reference, request.simulation_id)
    metadata = header(path)
    if request.sample_index >= metadata["sample_count"]:
        raise FileNotFoundError("The selected sample does not exist")
    index = _index(store, path)
    if index is not None:
        with index.open("rb") as source:
            if request.sample_cursor is not None:
                if request.sample_cursor > index.stat().st_size:
                    raise EvidenceChanged("Invalid sample cursor")
                source.seek(max(0, request.sample_cursor - 1))
                if request.sample_cursor and source.read(1) != b"\n":
                    raise EvidenceChanged("Sample cursor splits a record")
                cursor = source.tell()
                line = source.readline(MAX_LINE + 1)
            else:
                consumed = 0
                for _ in range(request.sample_index + 1):
                    cursor = source.tell()
                    line = source.readline(MAX_LINE + 1)
                    consumed += len(line)
                    if not line or len(line) > MAX_LINE or consumed > MAX_INDEX_SCAN:
                        raise EvidenceLimit("Sample lookup exceeds bounded index scan")
            if not line or len(line) > MAX_LINE or not line.endswith(b"\n"):
                raise EvidenceLimit("Sample index row is unavailable")
            value = descriptor(json.loads(line), cursor=cursor)
    else:
        value = descriptor(_legacy(path)["samples"][request.sample_index])
    if value.sample_index != request.sample_index:
        raise EvidenceChanged("Selected sample cursor does not match its index")
    blob = plain(store._contained(path.parent / f"{value.sample_index:08d}-{value.kind}.bin", exists=True))
    if blob.stat().st_size != value.byte_length:
        raise EvidenceChanged("Sample size changed")
    return blob, value
