"""Fixed collision-stress recipes; danger tiers are scoped to live calibration.

All four families reuse the normal template rules and map placement. Occlusion
is geometric: the unchanged baseline observes ground-truth actors behind it.
"""

from dataclasses import dataclass
from typing import Literal

from shared.models import Scenario, ScenarioTemplate

from .scenario_generator import build_scenario

CATALOG_VERSION = "collision-stress-v1"
CALIBRATION_MAP = "Town10HD_Opt"


@dataclass(frozen=True)
class StressPreset:
    id: str
    family: ScenarioTemplate
    intended_stress: Literal["low", "medium", "high"]
    description: str
    # Immutable pairs prevent a caller changing the shared catalogue.
    parameters: tuple[tuple[str, float], ...]
    duration_s: float = 8.0

    def scenario(self, *, seed: int = 7) -> Scenario:
        return build_scenario(self.family, parameters=dict(self.parameters), seed=seed,
                              name=self.id, duration_s=self.duration_s, map_name=CALIBRATION_MAP)


def _preset(family, tier, description, **parameters):
    return StressPreset(f"{family}-{tier}", ScenarioTemplate(family), tier, description, tuple(parameters.items()))


STRESS_PRESETS = (
    _preset("intersection", "low", "Cross traffic clears well before the ego arrives.",
            ego_speed_mps=8., intersection_distance_m=30., crossing_speed_mps=6., crossing_distance_m=4., crossing_delay_s=0.),
    _preset("intersection", "medium", "Similar nominal arrival times with a moderate stopping margin.",
            ego_speed_mps=12., intersection_distance_m=22., crossing_speed_mps=10., crossing_distance_m=6., crossing_delay_s=0.),
    _preset("intersection", "high", "Short approaches and closely aligned nominal arrival times.",
            ego_speed_mps=25., intersection_distance_m=30., crossing_speed_mps=20., crossing_distance_m=3.5, crossing_delay_s=0.),
    _preset("pedestrian_crossing", "low", "An early crossing and a long ego approach.",
            ego_speed_mps=8., pedestrian_distance_m=40., pedestrian_lateral_offset_m=3., pedestrian_speed_mps=1.5, crossing_delay_s=0.),
    _preset("pedestrian_crossing", "medium", "A crossing timed near a moderately fast ego approach.",
            ego_speed_mps=10., pedestrian_distance_m=22., pedestrian_lateral_offset_m=2., pedestrian_speed_mps=1.5, crossing_delay_s=0.),
    _preset("pedestrian_crossing", "high", "A fast close crossing with little stopping distance.",
            ego_speed_mps=25., pedestrian_distance_m=10., pedestrian_lateral_offset_m=2., pedestrian_speed_mps=5., crossing_delay_s=0.),
    _preset("sudden_braking", "low", "A long following gap and gradual lead braking.",
            ego_speed_mps=8., lead_speed_mps=8., lead_distance_m=35., brake_delay_s=2., lead_deceleration_mps2=3.),
    _preset("sudden_braking", "medium", "A shorter gap and abrupt lead braking.",
            ego_speed_mps=14., lead_speed_mps=12., lead_distance_m=14., brake_delay_s=.5, lead_deceleration_mps2=8.),
    _preset("sudden_braking", "high", "A fast ego closes a short gap as the lead brakes immediately.",
            ego_speed_mps=25., lead_speed_mps=12., lead_distance_m=5.5, brake_delay_s=0., lead_deceleration_mps2=10.),
    _preset("occluded_pedestrian", "low", "A distant early crossing behind a stationary roadside vehicle.",
            ego_speed_mps=8., pedestrian_distance_m=40., pedestrian_lateral_offset_m=4., pedestrian_speed_mps=1.4,
            crossing_delay_s=0., occlusion_gap_m=4.),
    _preset("occluded_pedestrian", "medium", "A nearer crossing behind a stationary roadside vehicle.",
            ego_speed_mps=13., pedestrian_distance_m=24., pedestrian_lateral_offset_m=4., pedestrian_speed_mps=3.,
            crossing_delay_s=0., occlusion_gap_m=3.),
    _preset("occluded_pedestrian", "high", "A fast close crossing behind a stationary roadside vehicle.",
            ego_speed_mps=25., pedestrian_distance_m=14., pedestrian_lateral_offset_m=4.5, pedestrian_speed_mps=7.,
            crossing_delay_s=0., occlusion_gap_m=4.),
)


def get_stress_preset(identifier: str) -> StressPreset:
    for preset in STRESS_PRESETS:
        if preset.id == identifier:
            return preset
    raise ValueError(f"Unknown collision stress preset: {identifier!r}")
