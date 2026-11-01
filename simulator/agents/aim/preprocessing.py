"""Pinned AIM camera cropping and route coordinates, without actor ground truth."""
import math

def camera_rgb(sample):
    import numpy as np

    if (sample.type != "sensor.camera.rgb" or sample.encoding != "bgra8"
            or sample.width != 400 or sample.height != 300 or sample.fov_deg is None
            or not math.isclose(sample.fov_deg, 100., abs_tol=1e-4)):
        raise ValueError("AIM requires its 400×300 BGRA camera with 100° FOV")
    raw = sample.raw_data()
    if len(raw) != 400 * 300 * 4:
        raise ValueError("AIM camera payload length does not match its calibration")
    image = np.frombuffer(raw, dtype=np.uint8).reshape(300, 400, 4)
    # Upstream scale=1 center crop: rows 22:278, columns 72:328.
    # Preserve upstream 0..255 float inputs; its trained normalization is inside
    # ImageCNN. Introducing a /255 here would change the released policy.
    return np.ascontiguousarray(image[22:278, 72:328, 2::-1].transpose(2, 0, 1), dtype=np.float32)


class RouteTarget:
    """Upstream 4/50 m route cursor, using the already supplied framework route."""
    def __init__(self, route):
        if not route:
            raise ValueError("AIM requires an explicit resolved framework route")
        self.points = [(item.position.x, item.position.y) for item in route]
        self.cursor = 0

    def next(self, pose):
        x, y = pose.position.x, pose.position.y
        to_pop, farthest, distance = 0, -math.inf, 0.
        for index in range(self.cursor + 1, len(self.points)):
            if distance > 50.:
                break
            a, b = self.points[index - 1], self.points[index]
            distance += math.dist(a, b)
            delta = math.hypot(b[0] - x, b[1] - y)
            if delta <= 4. and delta > farthest:
                to_pop, farthest = index - self.cursor, delta
        self.cursor = min(self.cursor + to_pop, max(0, len(self.points) - 2))
        target = self.points[min(self.cursor + 1, len(self.points) - 1)]
        dx, dy = target[0] - x, target[1] - y
        yaw = math.radians(pose.rotation.yaw_deg)
        forward = math.cos(yaw) * dx + math.sin(yaw) * dy
        right = -math.sin(yaw) * dx + math.cos(yaw) * dy
        return right, -forward


def world_waypoints(values, pose):
    from shared.models import Transform, Vector3
    yaw = math.radians(pose.rotation.yaw_deg)
    result = []
    for right, negative_forward in values:
        forward = -negative_forward
        result.append(Transform(position=Vector3(
            x=pose.position.x + math.cos(yaw) * forward - math.sin(yaw) * right,
            y=pose.position.y + math.sin(yaw) * forward + math.cos(yaw) * right,
            z=pose.position.z)))
    return result
