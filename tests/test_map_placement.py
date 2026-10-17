"""Template placement on mapped lanes, independent of any Town coordinates."""

import math
from types import SimpleNamespace as NS

import pytest

from scenarios import build_scenario
from simulator.map_placement import TemplatePlacementPlanner


class JunctionMap:
    def __init__(self, rotation=0.0, second_height=3.0):
        self.rotation, self.second_height = rotation, second_height
        self.junction = NS(get_waypoints=lambda lane_type: [
            (self.point(1, 0), self.point(1, 20)),
            (self.point(2, 0), self.point(2, 20)),
        ])

    def point(self, road, s):
        angle = self.rotation + (90 if road == 2 else 0)
        theta = math.radians(angle)
        point = NS(
            road_id=road, section_id=0, lane_id=-1, s=s, junction_id=10,
            is_junction=0 <= s <= 20,
            transform=NS(location=NS(x=100 + (s - 10) * math.cos(theta),
                                     y=200 + (s - 10) * math.sin(theta),
                                     z=self.second_height if road == 2 else 3),
                         rotation=NS(pitch=0, yaw=angle, roll=0)),
        )
        point.previous = lambda distance: [self.point(road, s - distance)]
        point.next = lambda distance: [self.point(road, s + distance)]
        point.get_junction = lambda: self.junction
        return point

    def generate_waypoints(self, distance):
        return [self.point(1, 10), self.point(2, 10)]

    def get_waypoint_xodr(self, road, lane, s):
        return self.point(road, s)


@pytest.mark.parametrize('rotation', [0, 37, -135])
def test_intersection_uses_a_shared_junction_and_preserves_approach_distances(rotation):
    scenario = build_scenario('intersection')
    before = scenario.model_dump()
    road_map = JunctionMap(rotation)
    planner = TemplatePlacementPlanner(road_map, NS(LaneType=NS(Driving=1)), scenario)
    plan = next(planner.candidates())
    ego, crossing = plan.spawns['ego'], plan.spawns['crossing']
    assert math.hypot(ego.position.x - 100, ego.position.y - 200) == pytest.approx(30)
    assert math.hypot(crossing.position.x - 100, crossing.position.y - 200) == pytest.approx(25)
    assert (crossing.rotation.yaw_deg - ego.rotation.yaw_deg) % 360 == pytest.approx(90)
    assert len(plan.routes['ego']) > 30
    assert len(plan.routes['crossing']) > 30
    assert next(TemplatePlacementPlanner(road_map, planner.carla, scenario).candidates()) == plan
    assert scenario.model_dump() == before


def test_intersection_rejects_crossing_roads_at_different_heights():
    planner = TemplatePlacementPlanner(JunctionMap(second_height=12), NS(LaneType=NS(Driving=1)),
                                       build_scenario('intersection'))
    assert list(planner.candidates()) == []


def test_intersection_preserves_custom_distance_parameters():
    scenario = build_scenario('intersection', parameters={'intersection_distance_m': 47.5, 'crossing_distance_m': 17.3})
    plan = next(TemplatePlacementPlanner(JunctionMap(), NS(LaneType=NS(Driving=1)), scenario).candidates())
    assert plan.spawns['ego'].position.x == pytest.approx(52.5)
    assert plan.spawns['crossing'].position.y == pytest.approx(182.7)
