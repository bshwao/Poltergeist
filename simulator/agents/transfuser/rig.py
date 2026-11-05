"""Exact front-view CVPR 2021 agent and bundled Leaderboard wrapper settings."""
from .checkpoint import SOURCE_REVISION


def manifest(weights):
    return {
        "protocol_version": "1.3", "agent_id": "poltergeist-transfuser-fusion",
        "name": "TransFuser RGB + LiDAR (CPU)", "version": "cvpr2021-" + SOURCE_REVISION[:12],
        "entry_point": "agent:TransFuserAgent", "observation_mode": "sensors",
        "control_mode": "vehicle_control", "navigation_mode": "provided_route", "assets": [],
        "sensors": [
            {"id": "front-camera", "type": "sensor.camera.rgb",
             "attributes": {"image_size_x": 400, "image_size_y": 300, "fov": 100.,
                            "lens_circle_multiplier": 3., "lens_circle_falloff": 3.,
                            "chromatic_aberration_intensity": .5, "chromatic_aberration_offset": 0.},
             "mount": {"position": {"x": 1.3, "z": 2.3}}},
            {"id": "lidar", "type": "sensor.lidar.ray_cast",
             "attributes": {"channels": 64, "range": 85., "points_per_second": 600000,
                            "rotation_frequency": 10., "upper_fov": 10., "lower_fov": -30.,
                            "atmosphere_attenuation_rate": .004, "dropoff_general_rate": .45,
                            "dropoff_intensity_limit": .8, "dropoff_zero_intensity": .4},
             "mount": {"position": {"x": 1.3, "z": 2.5}, "rotation": {"yaw_deg": -90.}}},
        ],
        "configuration": {"checkpoint_path": str(weights), "device": "cpu",
                          "torch_threads": 2, "inference_budget_s": 2.},
    }
