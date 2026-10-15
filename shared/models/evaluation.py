"""Versioned evaluation settings and observation coverage."""

from typing import Literal, Self

from pydantic import Field, StrictBool, model_validator

from .base import ContractModel, NonNegativeFloat, NonNegativeInt, PositiveFloat


class EvaluationSettings(ContractModel):
    method_version: Literal["1.0"] = "1.0"
    collision_episode_gap_s: NonNegativeFloat = 1.0
    violation_episode_gap_s: NonNegativeFloat = 1.0
    speeding_tolerance_mps: NonNegativeFloat = 0.5
    hard_braking_threshold_mps2: PositiveFloat = 4.0
    stopped_speed_mps: NonNegativeFloat = 0.1
    max_sample_gap_s: PositiveFloat = 0.5
    ttc_horizon_s: PositiveFloat = 10.0
    ttc_warning_s: PositiveFloat = 3.0
    clearance_warning_m: PositiveFloat = 2.0
    braking_full_severity_mps2: PositiveFloat = 8.0
    violations_for_full_severity: NonNegativeInt = Field(default=3, gt=0, strict=True)
    ttc_weight: NonNegativeFloat = 45.0
    clearance_weight: NonNegativeFloat = 25.0
    braking_weight: NonNegativeFloat = 15.0
    traffic_rule_weight: NonNegativeFloat = 15.0

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        if self.ttc_horizon_s < self.ttc_warning_s:
            raise ValueError("ttc_horizon_s must cover ttc_warning_s")
        if self.braking_full_severity_mps2 <= self.hard_braking_threshold_mps2:
            raise ValueError("braking_full_severity_mps2 must exceed the hard braking threshold")
        total = self.ttc_weight + self.clearance_weight + self.braking_weight + self.traffic_rule_weight
        if abs(total - 100.0) > 1e-9:
            raise ValueError("Severity component weights must sum to 100")
        return self


class EvaluationCoverage(ContractModel):
    """Counts of actual observations; empty actor lists are observed empty scenes.

    A pair's TTC is evaluated even when its known trajectory has no predicted
    contact. Missing geometry/velocity is not an evaluated pair.
    """

    total_frames: NonNegativeInt = 0
    collision_frames: NonNegativeInt = 0
    traffic_rule_frames: NonNegativeInt = 0
    braking_frames: NonNegativeInt = 0
    clearance_frames: NonNegativeInt = 0
    ttc_frames: NonNegativeInt = 0
    actor_pairs: NonNegativeInt = 0
    clearance_pairs: NonNegativeInt = 0
    ttc_pairs: NonNegativeInt = 0
    has_gaps: StrictBool = False

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        counts = (
            self.collision_frames, self.traffic_rule_frames, self.braking_frames,
            self.clearance_frames, self.ttc_frames,
        )
        if any(count > self.total_frames for count in counts):
            raise ValueError("Observed frame counts cannot exceed total_frames")
        if self.clearance_pairs > self.actor_pairs or self.ttc_pairs > self.clearance_pairs:
            raise ValueError("Pair coverage must satisfy ttc_pairs <= clearance_pairs <= actor_pairs")
        return self

    @property
    def complete(self) -> bool:
        return self.total_frames > 0 and not self.has_gaps and self.ttc_pairs == self.clearance_pairs == self.actor_pairs and all(
            count == self.total_frames for count in (
                self.collision_frames, self.traffic_rule_frames, self.braking_frames,
                self.clearance_frames, self.ttc_frames,
            )
        )
