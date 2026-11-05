"""Released two-height LiDAR histogram, preserving its exact bins and axes."""
from simulator.agents.aim.preprocessing import camera_rgb, RouteTarget, world_waypoints


def lidar_histogram(sample):
    import numpy as np

    if (sample.type != "sensor.lidar.ray_cast" or sample.encoding != "float32_xyzi"
            or sample.channels != 64):
        raise ValueError("TransFuser requires its 64-channel XYZI LiDAR")
    raw = sample.raw_data()
    if len(raw) % 16:
        raise ValueError("TransFuser LiDAR payload must contain complete XYZI points")
    points = np.frombuffer(raw, dtype="<f4").reshape(-1, 4)
    if not np.isfinite(points).all():
        raise ValueError("TransFuser LiDAR contains nonfinite coordinates or intensity")
    points = points[:, :3].copy()
    # Released sensor yaw is -90 degrees. With seq_len=1, the upstream
    # past-to-current pose transform is identity; only local Y is inverted.
    points[:, 1] *= -1
    # Preserve the upstream [-32, 33] bins (despite its 8 px/m comment).
    # Correcting that apparent asymmetry would change the trained input.
    xbins = np.linspace(-32, 33, 257)
    ybins = np.linspace(-32, 0, 257)
    layers = []
    for mask in (points[:, 2] <= -2., points[:, 2] > -2.):
        counts = np.histogramdd(points[mask, :2], bins=(xbins, ybins))[0]
        layers.append(np.minimum(counts, 5) / 5)
    return np.stack(layers).astype(np.float32)
