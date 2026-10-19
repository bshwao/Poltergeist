"""Serializable execution options used by the runner and queued API jobs."""

from pydantic import Field, StrictBool

from .base import ContractModel, NonNegativeInt, PositiveFloat


class SimulationOptions(ContractModel):
    allow_map_reload: StrictBool = False
    # None preserves the server setting; explicit values are scoped/restored.
    no_rendering_mode: StrictBool | None = None
    sensor_timeout_s: PositiveFloat = 2.0
    sensor_settle_s: PositiveFloat = 0.02
    max_ticks: NonNegativeInt = Field(default=100_000, gt=0, strict=True)
    camera: StrictBool = False
    lidar: StrictBool = False
    camera_width: NonNegativeInt = Field(default=640, gt=0, le=4096, strict=True)
    camera_height: NonNegativeInt = Field(default=360, gt=0, le=4096, strict=True)
