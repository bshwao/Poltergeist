"""Optional numeric CARLA sensor settings, without changing legacy defaults."""

CAMERA_OPTIONAL_BOUNDS = {
    "lens_circle_multiplier": (0., 10.),
    "lens_circle_falloff": (0., 10.),
    "chromatic_aberration_intensity": (0., 1.),
    "chromatic_aberration_offset": (0., 1.),
}
LIDAR_OPTIONAL_BOUNDS = {
    "atmosphere_attenuation_rate": (0., 1.),
    "dropoff_general_rate": (0., 1.),
    "dropoff_intensity_limit": (0., 1.),
    "dropoff_zero_intensity": (0., 1.),
}
