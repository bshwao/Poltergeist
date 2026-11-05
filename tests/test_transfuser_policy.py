"""Released fusion coordinates, bounded calibration and prediction provenance."""
import base64
from collections import deque
from contextlib import nullcontext
import hashlib
from types import SimpleNamespace

import pytest

from shared.models import ActorState, AgentInputInfo, AgentManifest, AgentObservation, AgentReset, AgentSensorFrame, DrivingTask, Transform, Vector3
from simulator.agent_sensors import resolve_sensor_specs
from simulator.sensor_fixtures import build_sensor_fixture
from simulator.agents.transfuser import TransFuserAgent
from simulator.agents.transfuser import checkpoint
from simulator.agents.transfuser.preprocessing import lidar_histogram
from simulator.agents.transfuser.rig import manifest
from simulator.agents.sdk import AgentCompatibilityError, AgentLifecycleError


def context():
    task = DrivingTask(destination=Transform(position=Vector3(x=20.)), target_speed_mps=4.)
    rig = resolve_sensor_specs(AgentManifest.model_validate(manifest("C:/owned/model.pth")).sensors)
    return AgentReset(protocol_version="1.3", seed=7, map_name="fixture", ego_actor_id="ego", fixed_delta_seconds=.05,
        destination=task.destination, driving_task=task, route=[Transform(), task.destination], sensors=rig)


def observation(index=0):
    c = context()
    return AgentObservation(protocol_version="1.3", observation_mode="sensors", frame=500+index, time_s=index*.05, dt_s=.05,
        ego=ActorState(actor_id="ego", transform=Transform(), velocity_mps=Vector3(x=2.)), driving_task=c.driving_task,
        ego_state_source="simulated_ego_kinematics", sensor_source="deterministic_fixture",
        sensors=build_sensor_fixture(c.sensors, 500+index, index*.05))


def resettable():
    agent = TransFuserAgent()
    controller = lambda: SimpleNamespace(_window=deque([1.]*40, maxlen=40), _max=1., _min=-1.)
    agent._model = SimpleNamespace(turn_controller=controller(), speed_controller=controller())
    agent._budget = 2.
    agent._parameters = 66293634
    agent.reset(context())
    return agent


def test_trained_fusion_rig_and_saved_provenance_retain_release_calibration():
    c = context()
    lidar = c.sensors[1]
    assert lidar.attributes['points_per_second'] == 600000
    assert lidar.attributes['rotation_frequency'] == 10.
    assert lidar.mount.rotation.yaw_deg == -90.
    value = AgentInputInfo(observation_mode="sensors", source="carla_sensors", ego_state_source="simulated_ego_kinematics",
        ground_truth_actor_inputs=False, sensors=c.sensors, assistance=["ideal_ego_kinematics", "framework_route"])
    assert AgentInputInfo.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValueError, match="2 MiB"):
        resolve_sensor_specs([*c.sensors, lidar.model_copy(update={"id": "extra-lidar"})])


@pytest.mark.parametrize("index,attribute,value", [(0,"lens_circle_multiplier",-1.), (0,"chromatic_aberration_offset",True),
    (1,"dropoff_general_rate",1.01), (1,"points_per_second",600001), (1,"unknown_setting",0.)])
def test_optional_sensor_settings_remain_bounded_in_live_and_saved_calibration(index, attribute, value):
    c = context()
    c.sensors[index].attributes[attribute] = value
    with pytest.raises(ValueError): resolve_sensor_specs(c.sensors)
    with pytest.raises(ValueError):
        AgentInputInfo(observation_mode="sensors", source="carla_sensors", ego_state_source="simulated_ego_kinematics",
            ground_truth_actor_inputs=False, sensors=c.sensors, assistance=["ideal_ego_kinematics", "framework_route"])


def test_lidar_histogram_preserves_height_boundary_y_flip_bin_edges_and_saturation():
    np = pytest.importorskip("numpy")
    points = np.array([[0.,1.,-2.,.5]]*7 + [[0.,1.,-1.99,.5], [40.,1.,0.,.5], [0.,-1.,0.,.5]], dtype='<f4')
    raw = points.tobytes()
    sample = AgentSensorFrame(sensor_id="lidar", type="sensor.lidar.ray_cast", frame=1, timestamp_s=0.,
        encoding="float32_xyzi", channels=64, data_base64=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest())
    hist = lidar_histogram(sample)
    assert hist.shape == (2,256,256) and hist.dtype == np.float32
    assert hist[0].max() == 1. and hist[0].sum() == 1.
    assert hist[1].sum() == pytest.approx(.2)
    x = np.searchsorted(np.linspace(-32,33,257),0.,side='right')-1
    y = np.searchsorted(np.linspace(-32,0,257),-1.,side='right')-1
    assert hist[0,x,y] == 1. and hist[1,x,y] == pytest.approx(.2)


@pytest.mark.parametrize("change", [lambda c: c.sensors.pop(),
    lambda c: c.sensors[1].attributes.update(rotation_frequency=20.),
    lambda c: setattr(c.sensors[1].mount.rotation,"yaw_deg",0.),
    lambda c: setattr(c,"fixed_delta_seconds",.1)])
def test_incompatible_rig_invalidates_previous_reset(change):
    agent = resettable()
    c = context(); change(c)
    with pytest.raises(AgentCompatibilityError): agent.reset(c)
    with pytest.raises(AgentLifecycleError): agent.step(observation())


@pytest.mark.parametrize("changes", [{"actors": []}, {"scene": {}}, {"frame":501}, {"dt_s":.1}, {"traffic_light_red":False}])
def test_invalid_or_privileged_observations_cannot_produce_controls(changes):
    with pytest.raises(AgentCompatibilityError): resettable().step(observation().model_copy(update=changes))


def test_warmup_and_held_waypoints_keep_original_prediction_frame_and_hashes():
    np = pytest.importorskip("numpy")
    class Tensor:
        def __init__(self, values): self.values = np.asarray(values,dtype=np.float32)
        @property
        def shape(self): return self.values.shape
        def unsqueeze(self, axis): return Tensor(np.expand_dims(self.values,axis))
        def clone(self): return Tensor(self.values.copy())
        def __getitem__(self, key): return self.values[key]
    class Model:
        def __init__(self, base): self.__dict__.update(base.__dict__); self.calls=0
        def __call__(self,*args):
            self.calls+=1
            return Tensor([[[0.,-1.],[0.,-2.],[0.,-3.],[0.,-4.]]])
        def control_pid(self, predicted, speed):
            predicted.values[:,:,1]*=-1  # The released PID mutates its argument.
            return .1,.3,False,{"desired_speed":2.}
    agent=resettable(); agent._model=Model(agent._model)
    agent._torch=SimpleNamespace(from_numpy=Tensor,tensor=lambda v,dtype:Tensor(v),float32=np.float32,
        inference_mode=nullcontext,isfinite=lambda t:np.isfinite(t.values))
    results=[agent.step(observation(i)) for i in range(7)]
    assert results[0].diagnostics.maneuver == "fusion_warmup"
    assert results[0].control.throttle == 0.
    assert agent._model.calls == 5
    held=results[5].diagnostics.metadata
    assert not held['model_inference_performed'] and held['camera_frame'] == 505
    assert held['prediction_inputs']['frame'] == 504
    assert held['prediction_inputs']['lidar_sha256'] == observation(4).sensors[1].sha256
    assert held['model_waypoints'][0][1] == -1.
    assert results[6].diagnostics.metadata['prediction_inputs']['frame'] == 506
    with pytest.raises(AgentCompatibilityError): agent.step(observation(6))


def test_checkpoint_hash_is_verified_before_optional_dependency_import(tmp_path, monkeypatch):
    path=tmp_path/'weights.pth'; path.write_bytes(b'pin')
    monkeypatch.setattr(checkpoint,'CHECKPOINT_BYTES',3)
    monkeypatch.setattr(checkpoint,'CHECKPOINT_SHA256',hashlib.sha256(b'pin').hexdigest())
    assert checkpoint.checkpoint_bytes(path)==b'pin'
    path.write_bytes(b'bad')
    with pytest.raises(ValueError, match="hash"): TransFuserAgent().load({'checkpoint_path':str(path)})


def test_policy_budget_exhaustion_cannot_return_even_a_warmup_fallback(monkeypatch):
    pytest.importorskip("numpy")
    agent = resettable()
    clock = iter([0., 3.])
    monkeypatch.setattr("simulator.agents.transfuser.agent.perf_counter", lambda: next(clock))
    with pytest.raises(AgentCompatibilityError, match="exceeded"):
        agent.step(observation())
    assert agent._last is None and agent._step == 0
