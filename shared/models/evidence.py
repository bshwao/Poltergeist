"""Pull-only evidence contract; no filesystem paths or unsolicited uploads."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .base import ContractModel
from .telemetry import ControlInput

CHUNK_BYTES = 65536
PAGE_BYTES = 60000
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class EvidenceRequest(ContractModel):
    kind: Literal["evidence"] = "evidence"
    request_id: UUID
    simulation_id: UUID
    resource: Literal["telemetry", "recordings", "samples", "sample"] = "telemetry"
    artifact_index: Annotated[int, Field(ge=0, le=64, strict=True)] = 0
    sample_index: Annotated[int, Field(ge=0, le=1000000, strict=True)] = 0
    offset: Annotated[int, Field(ge=0, le=2**40, strict=True)] = 0
    limit: Annotated[int, Field(ge=1, le=100, strict=True)] = 25
    version: Digest | None = None
    sample_cursor: Annotated[int, Field(ge=0, le=2**40, strict=True)] | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def require_version_for_resume(self):
        if self.offset and self.version is None:
            raise ValueError("Resuming evidence requires its original version")
        return self


class EvidenceFrame(ContractModel):
    frame: Annotated[int, Field(ge=0, strict=True)]
    time_s: Annotated[float, Field(ge=0)]
    speed_mps: Annotated[float, Field(ge=0)] | None
    control: ControlInput
    collisions: Annotated[int, Field(ge=0, strict=True)] | None
    traffic_violations: Annotated[int, Field(ge=0, strict=True)] | None


class CameraFormat(ContractModel):
    encoding: Literal["bgra8"] = "bgra8"
    width: Annotated[int, Field(ge=1, le=4096, strict=True)]
    height: Annotated[int, Field(ge=1, le=4096, strict=True)]


class LidarFormat(ContractModel):
    encoding: Literal["float32_xyzi"] = "float32_xyzi"
    channels: Annotated[int, Field(ge=1, le=4096, strict=True)]


class EvidenceSample(ContractModel):
    sample_index: Annotated[int, Field(ge=0, le=1000000, strict=True)]
    index_offset: Annotated[int, Field(ge=0, le=2**40, strict=True)] | None = None
    kind: Literal["camera", "lidar"]
    frame: Annotated[int, Field(ge=0, strict=True)]
    timestamp: Annotated[float, Field(ge=0)]
    byte_length: Annotated[int, Field(ge=0, le=64 * 1024**2, strict=True)]
    sha256: Digest
    metadata: Annotated[CameraFormat | LidarFormat, Field(discriminator="encoding")]

    @model_validator(mode="after")
    def matching_format(self):
        if self.kind == "camera":
            if not isinstance(self.metadata, CameraFormat) or self.byte_length != self.metadata.width * self.metadata.height * 4:
                raise ValueError("Camera sample dimensions do not match its bytes")
        elif not isinstance(self.metadata, LidarFormat) or self.byte_length % 16:
            raise ValueError("LiDAR sample format is invalid")
        return self


class EvidenceRecording(ContractModel):
    artifact_index: Annotated[int, Field(ge=0, le=64, strict=True)]
    state: Literal["available", "missing", "deleted", "limit_exceeded"]
    sample_count: Annotated[int, Field(ge=0, le=1000001, strict=True)] | None = None
    total_bytes: Annotated[int, Field(ge=0, le=2**40, strict=True)] | None = None


class EvidenceReply(ContractModel):
    kind: Literal["evidence"] = "evidence"
    request_id: UUID
    simulation_id: UUID
    state: Literal["available", "missing", "deleted", "changed", "limit_exceeded"]
    resource: Literal["telemetry", "recordings", "samples", "sample"]
    offset: Annotated[int, Field(ge=0, strict=True)]
    next_offset: Annotated[int, Field(ge=0, strict=True)] | None = None
    version: Digest | None = None
    frames: list[EvidenceFrame] = Field(default_factory=list, max_length=100)
    data_base64: Annotated[str, Field(max_length=87384)] | None = None
    sha256: Digest | None = None
    sample_sha256: Digest | None = None
    total_bytes: Annotated[int, Field(ge=0, le=2**40, strict=True)] | None = None
    frame: Annotated[int, Field(ge=0, strict=True)] | None = None
    timestamp: Annotated[float, Field(ge=0)] | None = None
    artifact_index: Annotated[int, Field(ge=0, le=64, strict=True)] = Field(default=0, exclude_if=lambda value: value == 0)
    recordings: list[EvidenceRecording] = Field(default_factory=list, max_length=64, exclude_if=lambda value: not value)
    samples: list[EvidenceSample] = Field(default_factory=list, max_length=100, exclude_if=lambda value: not value)
    sample: EvidenceSample | None = Field(default=None, exclude_if=lambda value: value is None)
