"""Bounded reads of finalized evidence. Callers authorize the accepted run first."""

import base64
from hashlib import sha256
from math import hypot

from backend.storage import ArtifactError
from shared.models import TelemetryFrame
from shared.models.evidence import CHUNK_BYTES, PAGE_BYTES, EvidenceFrame, EvidenceReply
from shared.models.remote import canonical_json
from .recording_evidence import (EvidenceChanged, EvidenceLimit, catalogue,
    sample_location, sample_page)

MAX_LINE = 65536


def _plain(path):
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ArtifactError("Evidence must be an ordinary owned file")
    return path


def read_evidence(store, result, request):
    try:
        return _read_evidence(store, result, request)
    except EvidenceLimit:
        state = "limit_exceeded"
    except EvidenceChanged:
        state = "changed"
    except (ArtifactError, ValueError, KeyError, IndexError, TypeError, OSError):
        state = "missing"
    return EvidenceReply(request_id=request.request_id, simulation_id=request.simulation_id,
        resource=request.resource, artifact_index=request.artifact_index, offset=request.offset, state=state)


def _read_evidence(store, result, request):
    base = dict(request_id=request.request_id, simulation_id=request.simulation_id,
                resource=request.resource, artifact_index=request.artifact_index, offset=request.offset)
    def reply(state, **values):
        return EvidenceReply(**base, state=state, **values)
    if result is None:
        return reply("missing")
    if request.resource == "recordings":
        if request.offset:
            return reply("changed")
        recordings = catalogue(store, result)
        payload = canonical_json([value.model_dump(mode="json") for value in recordings]).encode()
        version = sha256(payload).hexdigest()
        if request.version is not None and request.version != version:
            return reply("changed")
        return reply("available", version=version, recordings=recordings, total_bytes=len(payload),
            data_base64=base64.b64encode(payload).decode("ascii"), sha256=version)
    refs = result.artifacts
    if request.resource == "telemetry":
        refs = [ref for ref in refs if ref.kind == "telemetry"]
        if len(refs) != 1:
            return reply("missing")
        ref = refs[0]
    else:
        if request.artifact_index >= len(refs) or refs[request.artifact_index].kind != "recording":
            return reply("missing")
        ref = refs[request.artifact_index]
    # The generated reference is validated before deriving even a tombstone path.
    try:
        path = store.validate_reference(ref, request.simulation_id)
    except FileNotFoundError:
        from pathlib import PurePosixPath
        # validate_reference already checked kind, layout and run ownership.
        marker = store._contained(store.root.joinpath(*PurePosixPath(ref.path).parts).parent / ".deleted")
        return reply("deleted" if marker.is_file() else "missing")
    if store._contained(path.parent / ".deleted").is_file():
        return reply("deleted")
    metadata = None
    if request.resource == "samples":
        samples, version, total, following = sample_page(store, ref, request)
        payload = canonical_json([value.model_dump(mode="json") for value in samples]).encode()
        return reply("available", samples=samples, version=version, total_bytes=total, next_offset=following,
            data_base64=base64.b64encode(payload).decode("ascii"), sha256=sha256(payload).hexdigest())
    if request.resource == "sample":
        path, metadata = sample_location(store, ref, request)
    path = _plain(path)
    before = path.stat()
    identity = f"{before.st_dev}:{before.st_ino}:{before.st_size}:{before.st_mtime_ns}"
    if metadata is not None:
        identity += canonical_json(metadata)
    version = sha256(identity.encode()).hexdigest()
    if request.version is not None and request.version != version:
        return reply("changed")
    if request.offset > before.st_size:
        return reply("changed")
    with path.open("rb") as source:
        source.seek(request.offset)
        if metadata is not None:
            data = source.read(CHUNK_BYTES)
            end = source.tell()
            response = reply("available", version=version, total_bytes=before.st_size,
                next_offset=end if end < before.st_size else None,
                data_base64=base64.b64encode(data).decode("ascii"), sha256=sha256(data).hexdigest(),
                sample_sha256=metadata.sha256, frame=metadata.frame, timestamp=metadata.timestamp, sample=metadata)
        else:
            if request.offset:
                source.seek(request.offset - 1)
                if source.read(1) != b"\n":
                    return reply("changed")
            frames = []
            size = 0
            for _ in range(request.limit):
                start = source.tell()
                line = source.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    return reply("limit_exceeded")
                frame = TelemetryFrame.model_validate_json(line)
                velocity = frame.ego.velocity_mps
                view = EvidenceFrame(frame=frame.frame, time_s=frame.time_s,
                    speed_mps=None if velocity is None else hypot(velocity.x, velocity.y, velocity.z),
                    control=frame.control, collisions=None if frame.collisions is None else len(frame.collisions),
                    traffic_violations=None if frame.traffic_violations is None else len(frame.traffic_violations))
                encoded = canonical_json(view).encode()
                if size + len(encoded) > PAGE_BYTES:
                    source.seek(start)
                    break
                frames.append(view)
                size += len(encoded)
            end = source.tell()
            payload = canonical_json([f.model_dump(mode="json") for f in frames]).encode()
            response = reply("available", version=version, total_bytes=before.st_size, frames=frames,
                next_offset=end if end < before.st_size else None,
                data_base64=base64.b64encode(payload).decode("ascii"), sha256=sha256(payload).hexdigest())
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        return reply("changed")
    return response
