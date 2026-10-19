"""Structured simulator-state conversion matching the released PlanT inputs.

No camera or fabricated perception is used. Missing route, geometry, motion,
road raster, or speed limit is an input error rather than an empty observation.
"""

from dataclasses import dataclass
import base64
from functools import lru_cache
import math

from shared.models import AgentObservation
from simulator.transforms import rotation_matrix

CLASS_NUMBERS = {"vehicle": 1., "pedestrian": 2., "static": 3.,
                 "stop_sign": 4., "traffic_light": 5., "emergency": 6.}
BEV_COLORS = ((0.485, 0.456, 0.406), (0.25, 0.25, 0.75),
              (0.485, 0.456, 0.406), (0.75, 0.25, 0.25), (0.25, 0.75, 0.25))


def relative_position(position, ego_transform):
    yaw = math.radians(ego_transform.rotation.yaw_deg)
    dx = position.x - ego_transform.position.x
    dy = position.y - ego_transform.position.y
    return (math.cos(yaw) * dx + math.sin(yaw) * dy,
            -math.sin(yaw) * dx + math.cos(yaw) * dy,
            position.z - ego_transform.position.z)


def forward_speed(velocity, transform):
    yaw = math.radians(transform.rotation.yaw_deg)
    pitch = math.radians(transform.rotation.pitch_deg)
    return (velocity.x * math.cos(yaw) * math.cos(pitch)
            + velocity.y * math.sin(yaw) * math.cos(pitch)
            + velocity.z * math.sin(pitch))


def relative_box_position(position, ego_transform):
    """Upstream object boxes use the inverse full 3D ego transform."""
    offset = (position.x - ego_transform.position.x,
              position.y - ego_transform.position.y,
              position.z - ego_transform.position.z)
    matrix = rotation_matrix(ego_transform.rotation)
    return tuple(sum(matrix[row][column] * offset[row] for row in range(3)) for column in range(3))


def speed_category(speed_mps: float) -> int:
    """Nearest training category; original training maps only 50/80/100/120 km/h.

    CARLA 0.9.16 includes other local speed limits. Mapping them to the nearest
    training category is explicit domain adaptation, not model speed control.
    """
    return min(range(4), key=lambda index: abs((50., 80., 100., 120.)[index] - speed_mps * 3.6))


@dataclass(frozen=True)
class PlanTFeatures:
    objects: list[list[float]]
    route: list[list[float]]
    speed_category: int
    ego_speed_mps: float
    bev_classes: list[list[int]] | None
    omitted_objects: int


class PlanTFeatureBuilder:
    def __init__(self, *, input_bev: bool, range_m: float = 50., range_factor_front: float = 2.):
        self.input_bev = input_bev
        self.range_m = range_m
        self.range_factor_front = range_factor_front
        self.moving_walkers: set[str] = set()
        self.cleared_stop_signs: set[str] = set()

    def reset(self):
        self.moving_walkers.clear()
        self.cleared_stop_signs.clear()

    def build(self, observation: AgentObservation) -> PlanTFeatures:
        scene = observation.scene
        if scene is None:
            raise ValueError("Reference AI requires the structured_scene observation feature")
        if observation.ego.transform is None or observation.ego.velocity_mps is None:
            raise ValueError("Reference AI requires observed ego pose and velocity")
        if scene.route is None or len(scene.route) < 2:
            raise ValueError("Reference AI requires a map-derived route extending at least 21.5 metres ahead")
        if scene.objects is None:
            raise ValueError("Reference AI requires observed scene objects; null means unavailable")
        if scene.speed_limit_mps is None:
            raise ValueError("Reference AI requires an observed map speed limit")
        ego_transform = observation.ego.transform
        ego_speed = forward_speed(observation.ego.velocity_mps, ego_transform)
        candidates = []
        for order, obj in enumerate(scene.objects):
            if obj.actor_id == observation.ego.actor_id:
                continue
            x, y, z = relative_box_position(obj.transform.position, ego_transform)
            divisor = self.range_factor_front**2 if x > 0. else 1.
            if x*x/divisor + y*y > self.range_m**2 or abs(z) > 30.:
                continue
            kind = obj.kind
            if kind in ("vehicle", "pedestrian", "emergency"):
                if obj.velocity_mps is None:
                    raise ValueError(f"Reference AI requires observed velocity for {obj.actor_id}")
                speed = forward_speed(obj.velocity_mps, obj.transform)
            else:
                speed = 0.
            if kind == "pedestrian":
                if speed < 0.1 and obj.actor_id not in self.moving_walkers:
                    continue
                self.moving_walkers.add(obj.actor_id)
            if kind == "traffic_light":
                if obj.traffic_light_state in (None, "unknown"):
                    raise ValueError(f"Reference AI requires observed traffic light state for {obj.actor_id}")
                if obj.traffic_light_state not in ("red", "yellow"):
                    continue
            if kind == "stop_sign":
                distance = math.hypot(x, y)
                if distance > 10.:
                    self.cleared_stop_signs.discard(obj.actor_id)
                elif distance < 3. and ego_speed < 0.1:
                    self.cleared_stop_signs.add(obj.actor_id)
                if obj.actor_id in self.cleared_stop_signs:
                    continue
            if kind in ("stop_sign", "traffic_light") and math.hypot(x, y) >= 30.:
                continue
            yaw = (obj.transform.rotation.yaw_deg - ego_transform.rotation.yaw_deg + 180.) % 360. - 180.
            values = [CLASS_NUMBERS[kind], x, y, yaw, speed * 3.6,
                      obj.extent.y * 2., obj.extent.x * 2.]
            # Upstream groups moving objects before static/sign/light tokens.
            group = 0 if kind in ("vehicle", "pedestrian", "emergency") else 1
            candidates.append((group, order, x*x + y*y, values))
        omitted = max(0, len(candidates) - 480)
        if omitted:
            # Leave space for BERT's path/waypoint/map tokens (512 total).
            candidates = sorted(candidates, key=lambda item: item[2])[:480]
        candidates.sort(key=lambda item: item[:2])
        route = self._route(scene.route, ego_transform)
        bev = self._raster(scene.road_raster) if self.input_bev else None
        return PlanTFeatures([item[3] for item in candidates], route,
                             speed_category(scene.speed_limit_mps), ego_speed, bev, omitted)

    @staticmethod
    def _route(world_route, ego_transform):
        """Project onto the supplied map polyline; sample 2.5m..21.5m ahead.

        This is route preprocessing, not a learned/global route planner. No
        straight-line extrapolation is used when map route coverage ends.
        """
        points = [relative_position(item.position, ego_transform)[:2] for item in world_route]
        segments, distance, closest = [], 0., None
        for first, second in zip(points, points[1:]):
            dx, dy = second[0] - first[0], second[1] - first[1]
            length = math.hypot(dx, dy)
            if length < 1e-8:
                continue
            projection = max(0., min(1., -(first[0] * dx + first[1] * dy) / length**2))
            px, py = first[0] + projection * dx, first[1] + projection * dy
            candidate = (px*px + py*py, distance + projection * length)
            if closest is None or candidate[0] < closest[0]:
                closest = candidate
            segments.append((distance, distance + length, first, second))
            distance += length
        if closest is None or distance < closest[1] + 21.5 - 1e-8:
            raise ValueError("Reference AI requires a map-derived route extending at least 21.5 metres ahead")
        result, segment_index = [], 0
        for index in range(20):
            desired = closest[1] + 2.5 + index
            while segment_index < len(segments) - 1 and segments[segment_index][1] < desired:
                segment_index += 1
            start, end, first, second = segments[segment_index]
            fraction = (desired - start) / (end - start)
            result.append([first[0] + fraction * (second[0] - first[0]),
                           first[1] + fraction * (second[1] - first[1])])
        return result

    @staticmethod
    def _raster(raster):
        if raster is None:
            raise ValueError("This PlanT checkpoint requires an observed road raster")
        if (raster.width != 128 or raster.height != 128
                or not math.isclose(raster.pixels_per_meter, 2.)
                or raster.axes != "rows_right_columns_forward"):
            raise ValueError("PlanT requires a 128x128 road raster at 2 pixels/metre in the declared ego axes")
        # Upstream warps a 256-square image to corners [0,255], then applies
        # np.rot90 and a 64-pixel crop. Thus model axes both run backwards:
        # row=63.5-(255/128)*right, column=63.5-(255/128)*forward.
        # Resample from the explicitly declared SDK origin using nearest
        # semantic labels. No mask is replaced with an all-empty placeholder.
        indices = _raster_indices(raster.ego_row, raster.ego_column)
        classes = raster.classes
        if classes is None:
            classes = base64.b64decode(raster.data_base64, validate=True)
        return [[classes[index] for index in row] for row in indices]


@lru_cache(maxsize=8)
def _raster_indices(ego_row, ego_column):
    scale = 255. / 128.
    output = []
    for row in range(128):
        right = (63.5 - row) / scale
        source_row = min(127, max(0, int(math.floor(ego_row + right * 2. + 0.5))))
        output_row = []
        for column in range(128):
            forward = (63.5 - column) / scale
            source_column = min(127, max(0, int(math.floor(ego_column + forward * 2. + 0.5))))
            output_row.append(source_row * 128 + source_column)
        output.append(tuple(output_row))
    return tuple(output)


def tensor_batch(features: PlanTFeatures, torch, device):
    objects = [[0., 0., 0., 0., 0., 0., 0.], *features.objects]
    batch = {
        "x_objs": torch.tensor(objects, dtype=torch.float32, device=device),
        "idxs": torch.arange(1, len(objects), dtype=torch.long, device=device)[None],
        "route_original": torch.tensor([features.route], dtype=torch.float32, device=device),
        "speed_limit": torch.tensor([features.speed_category], dtype=torch.long, device=device),
        "input_ego_speed": torch.tensor([features.ego_speed_mps], dtype=torch.float32, device=device),
    }
    if features.bev_classes is not None:
        palette = torch.tensor(BEV_COLORS, dtype=torch.float32, device=device)
        classes = torch.tensor(features.bev_classes, dtype=torch.long, device=device)
        batch["BEV"] = palette[classes].permute(2, 0, 1)[None]
    return batch
