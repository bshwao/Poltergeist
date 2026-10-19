"""Exercise the external-agent lifecycle and observation/control trust boundary."""

from copy import deepcopy

import pytest

from shared.models import (
    ActorState,
    AgentDiagnostics,
    AgentManifest,
    AgentObservation,
    AgentReset,
    AgentStepResult,
    ControlInput,
    Transform,
    Vector3,
)
from simulator.agents.sdk import (
    AgentCompatibilityError,
    AgentLifecycleError,
    AgentProtocolError,
    AgentSession,
    AutonomousAgent,
)


def manifest(**changes):
    return AgentManifest(
        agent_id="test-driver", name="Test driver", version="1.0",
        entry_point="agent:TestAgent", **changes,
    )


def reset_context(**changes):
    values = {
        "seed": 7, "map_name": "Town03", "ego_actor_id": "ego",
        "fixed_delta_seconds": 0.05,
    }
    return AgentReset(**(values | changes))


def observation(**changes):
    values = {
        "frame": 410, "time_s": 0.0, "dt_s": 0.05,
        "ego": ActorState(
            actor_id="ego", transform=Transform(position=Vector3(x=1.0)),
        ),
        "actors": [ActorState(actor_id="pedestrian")],
        "target_speed_mps": 12.0,
    }
    return AgentObservation(**(values | changes))


class RecordingAgent(AutonomousAgent):
    def __init__(self, *, action=None, fail_on=None, fail_close=False):
        self.calls = []
        self.action = action
        self.fail_on = fail_on
        self.fail_close = fail_close

    def load(self, configuration):
        self.calls.append(("load", configuration))
        if self.fail_on == "load":
            raise RuntimeError("weights could not be loaded")

    def reset(self, context):
        self.calls.append(("reset", context))
        if self.fail_on == "reset":
            raise RuntimeError("route initialization failed")

    def step(self, value):
        self.calls.append(("step", value))
        if self.fail_on == "step":
            raise RuntimeError("model inference failed")
        return self.action(value) if self.action is not None else AgentStepResult(
            frame=value.frame, control=ControlInput(throttle=0.4, steer=-0.2),
        )

    def close(self):
        self.calls.append(("close",))
        if self.fail_close:
            raise RuntimeError("model cleanup failed")


def loaded_session(agent=None, **manifest_values):
    agent = agent or RecordingAgent()
    session = AgentSession(agent, manifest(**manifest_values))
    session.load()
    session.reset(reset_context())
    return session, agent


def call_names(agent):
    return [entry[0] for entry in agent.calls]


def test_sdk_interface_requires_all_lifecycle_operations():
    with pytest.raises(TypeError):
        AutonomousAgent()

    class IncompleteAgent(AutonomousAgent):
        def step(self, value):
            return AgentStepResult(frame=value.frame, control=ControlInput())

    with pytest.raises(TypeError):
        IncompleteAgent()


def test_valid_controls_remain_associated_with_their_observation_frame():
    session, agent = loaded_session()
    try:
        first = session.step(observation())
        second = session.step(observation(frame=411, time_s=0.05))
        assert first == AgentStepResult(frame=410, control=ControlInput(throttle=0.4, steer=-0.2))
        assert second.frame == 411
        assert call_names(agent) == ["load", "reset", "step", "step"]
    finally:
        session.close()
    assert call_names(agent)[-1] == "close"


def test_native_float32_timestep_rounding_does_not_reject_valid_carla_frames():
    import struct

    native_step = struct.unpack("f", struct.pack("f", 0.1))[0]
    agent = RecordingAgent()
    with AgentSession(agent, manifest()) as session:
        session.load()
        session.reset(reset_context(fixed_delta_seconds=0.1))
        for index in range(4):
            assert session.step(observation(
                frame=410 + index, time_s=index * native_step, dt_s=0.1,
            )).frame == 410 + index


def test_plain_mapping_result_supports_structured_agent_diagnostics():
    agent = RecordingAgent(action=lambda value: {
        "frame": value.frame,
        "control": {"brake": 0.8},
        "diagnostics": {
            "maneuver": "stop", "reason_codes": ["pedestrian_ahead"],
            "hazard_actor_ids": ["pedestrian"], "metadata": {"confidence": 0.9},
        },
    })
    session, _ = loaded_session(agent)
    try:
        result = session.step(observation())
        assert result.control == ControlInput(brake=0.8)
        assert result.diagnostics.maneuver == "stop"
        assert result.diagnostics.reason_codes == ["pedestrian_ahead"]
        assert result.diagnostics.metadata == {"confidence": 0.9}
    finally:
        session.close()


def test_manifest_configuration_is_snapshotted_and_agent_cannot_mutate_it():
    configured = manifest(configuration={"planner": {"target_speed": 8.0}})
    agent = RecordingAgent()
    session = AgentSession(agent, configured)
    configured.configuration["planner"]["target_speed"] = 100.0
    try:
        session.load()
        received = agent.calls[0][1]
        assert received == {"planner": {"target_speed": 8.0}}
        received["planner"]["target_speed"] = -1.0
        assert configured.configuration["planner"]["target_speed"] == 100.0
    finally:
        session.close()


def test_reset_route_is_a_defensive_copy():
    context = reset_context(route=[Transform(position=Vector3(x=20.0))])
    agent = RecordingAgent()
    session = AgentSession(agent, manifest())
    try:
        session.load()
        session.reset(context)
        received = agent.calls[-1][1]
        received.route[0].position.x = 999.0
        assert context.route[0].position.x == 20.0
    finally:
        session.close()


def test_agent_cannot_mutate_caller_observation_or_relabel_its_response():
    def mutate(value):
        original_frame = value.frame
        value.frame = 999
        value.ego.transform.position.x = 999.0
        value.actors.clear()
        return AgentStepResult(frame=original_frame, control=ControlInput(brake=1.0))

    value = observation()
    original = deepcopy(value)
    session, _ = loaded_session(RecordingAgent(action=mutate))
    try:
        assert session.step(value).frame == 410
        assert value == original
    finally:
        session.close()


def test_agent_input_mutation_cannot_change_the_sessions_frame_or_time_history():
    def mutate(value):
        original_frame = value.frame
        value.frame = 999
        value.time_s = 999.0
        return AgentStepResult(frame=original_frame, control=ControlInput(brake=1.0))

    session, _ = loaded_session(RecordingAgent(action=mutate))
    try:
        assert session.step(observation()).frame == 410
        assert session.step(observation(frame=411, time_s=0.05)).frame == 411
    finally:
        session.close()


def test_mapping_observations_with_existing_nested_models_remain_isolated():
    def mutate(value):
        value.ego.transform.position.x = 999.0
        value.actors[0].actor_id = "mutated"
        return AgentStepResult(frame=value.frame, control=ControlInput())

    value = observation()
    payload = value.model_dump()
    # Public mapping callers may supply models instead of plain dictionaries.
    payload["ego"] = value.ego
    payload["actors"] = value.actors
    original = deepcopy(value)
    session, _ = loaded_session(RecordingAgent(action=mutate))
    try:
        session.step(payload)
        assert value == original
        assert payload["actors"] is value.actors
    finally:
        session.close()


def test_returned_controls_and_diagnostics_do_not_alias_agent_owned_objects():
    original = AgentStepResult(
        frame=410, control=ControlInput(brake=0.5),
        diagnostics=AgentDiagnostics(metadata={"risks": [0.3]}),
    )
    session, _ = loaded_session(RecordingAgent(action=lambda _: original))
    try:
        returned = session.step(observation())
        returned.control.brake = 0.0
        returned.diagnostics.metadata["risks"].append(0.9)
        assert original.control.brake == 0.5
        assert original.diagnostics.metadata == {"risks": [0.3]}
    finally:
        session.close()


def test_reset_allows_another_episode_with_a_new_clock_and_frame_origin():
    session, agent = loaded_session()
    try:
        session.step(observation())
        session.step(observation(frame=411, time_s=0.05))
        session.reset(reset_context(seed=8, fixed_delta_seconds=0.1))
        assert session.step(observation(frame=900, dt_s=0.1)).frame == 900
        assert session.step(observation(frame=901, time_s=0.1, dt_s=0.1)).frame == 901
        assert call_names(agent).count("load") == 1
        assert call_names(agent).count("reset") == 2
    finally:
        session.close()


def test_normal_floating_point_simulation_clock_remains_accepted():
    session, agent = loaded_session()
    try:
        for index in range(100):
            assert session.step(observation(frame=410 + index, time_s=index * 0.05)).frame == 410 + index
        assert call_names(agent).count("step") == 100
    finally:
        session.close()


def test_lifecycle_order_is_enforced_without_invoking_user_code():
    agent = RecordingAgent()
    session = AgentSession(agent, manifest())
    with pytest.raises(AgentLifecycleError):
        session.reset(reset_context())
    with pytest.raises(AgentLifecycleError):
        session.step(observation())
    assert agent.calls == []
    session.load()
    with pytest.raises(AgentLifecycleError):
        session.load()
    with pytest.raises(AgentLifecycleError):
        session.step(observation())
    session.reset(reset_context())
    assert session.step(observation()).frame == 410
    session.close()
    for operation in (session.load, lambda: session.reset(reset_context()), lambda: session.step(observation())):
        with pytest.raises(AgentLifecycleError):
            operation()
    session.close()
    assert call_names(agent) == ["load", "reset", "step", "close"]


def test_context_manager_supports_explicit_loading_and_closes_once():
    agent = RecordingAgent()
    session = AgentSession(agent, manifest())
    with session as entered:
        assert entered is session
        assert agent.calls == []
        session.load()
        session.reset(reset_context())
        session.step(observation())
    session.close()
    assert call_names(agent) == ["load", "reset", "step", "close"]


@pytest.mark.parametrize("stage,message", [
    ("load", "weights could not be loaded"),
    ("reset", "route initialization failed"),
    ("step", "model inference failed"),
])
def test_user_failure_is_actionable_terminal_and_always_cleaned_up(stage, message):
    agent = RecordingAgent(fail_on=stage)
    session = AgentSession(agent, manifest())
    with pytest.raises(RuntimeError, match=message):
        with session:
            session.load()
            session.reset(reset_context())
            session.step(observation())
    assert call_names(agent)[-1] == "close"
    assert call_names(agent).count(stage) == 1
    with pytest.raises(AgentLifecycleError):
        session.step(observation())
    session.close()
    assert call_names(agent).count("close") == 1


def test_failed_inference_requires_cleanup_and_cannot_restart_in_place():
    session, agent = loaded_session(RecordingAgent(fail_on="step"))
    with pytest.raises(RuntimeError, match="model inference failed"):
        session.step(observation())
    agent.fail_on = None
    try:
        for operation in (session.load, lambda: session.reset(reset_context()), lambda: session.step(observation())):
            with pytest.raises(AgentLifecycleError):
                operation()
        assert call_names(agent) == ["load", "reset", "step"]
    finally:
        session.close()


def test_cleanup_failure_does_not_replace_the_inference_failure():
    agent = RecordingAgent(fail_on="step", fail_close=True)
    session = AgentSession(agent, manifest())
    with pytest.raises(RuntimeError, match="model inference failed") as caught:
        with session:
            session.load()
            session.reset(reset_context())
            session.step(observation())
    assert any("model cleanup failed" in note for note in caught.value.__notes__)
    session.close()
    assert call_names(agent).count("close") == 1


def test_cleanup_failure_without_prior_error_is_reported_and_attempted_once():
    agent = RecordingAgent(fail_close=True)
    session = AgentSession(agent, manifest())
    with pytest.raises(RuntimeError, match="model cleanup failed"):
        session.close()
    session.close()
    with pytest.raises(AgentLifecycleError):
        session.load()
    assert call_names(agent) == ["close"]


@pytest.mark.parametrize("changes", [
    {"time_s": 0.05},
    {"dt_s": 0.1},
    {"ego": ActorState(actor_id="another-car")},
])
def test_initial_observation_must_match_reset_context(changes):
    session, agent = loaded_session()
    try:
        with pytest.raises(AgentProtocolError):
            session.step(observation(**changes))
        assert call_names(agent) == ["load", "reset"]
        with pytest.raises(AgentLifecycleError):
            session.step(observation())
    finally:
        session.close()


@pytest.mark.parametrize("changes", [
    {"frame": 410, "time_s": 0.05},
    {"frame": 409, "time_s": 0.05},
    {"frame": 412, "time_s": 0.05},
    {"frame": 411, "time_s": 0.0},
    {"frame": 411, "time_s": 0.1},
    {"frame": 411, "time_s": 0.05, "dt_s": 0.1},
])
def test_stale_skipped_or_mistimed_observations_never_reach_agent(changes):
    session, agent = loaded_session()
    try:
        session.step(observation())
        with pytest.raises(AgentProtocolError):
            session.step(observation(**changes))
        assert call_names(agent).count("step") == 1
    finally:
        session.close()


@pytest.mark.parametrize("result", [
    None,
    ControlInput(brake=1.0),
    {"frame": 410, "control": {"throttle": 1.1}},
    {"frame": 410, "control": {"steer": float("nan")}},
    {"frame": 410, "control": {"brake": float("inf")}},
    {"frame": 410, "control": {"brake": True}},
    {"frame": 410, "control": {"throttle": "0.5"}},
    {"frame": 410, "control": {"unknown": 1.0}},
    {"frame": 411, "control": {"brake": 1.0}},
    {"frame": 410, "control": {}, "diagnostics": {"metadata": {"risk": float("nan")}}},
])
def test_invalid_or_misassociated_result_never_becomes_a_driving_action(result):
    session, agent = loaded_session(RecordingAgent(action=lambda _: result))
    try:
        with pytest.raises(AgentProtocolError):
            session.step(observation())
        with pytest.raises(AgentLifecycleError):
            session.step(observation(frame=411, time_s=0.05))
        assert call_names(agent).count("step") == 1
    finally:
        session.close()


@pytest.mark.parametrize("constructed", [False, True])
def test_model_instances_cannot_bypass_control_validation(constructed):
    control = ControlInput.model_construct(throttle=-1.0) if constructed else ControlInput()
    if not constructed:
        control.throttle = -1.0
    result = AgentStepResult.model_construct(frame=410, control=control)
    session, _ = loaded_session(RecordingAgent(action=lambda _: result))
    try:
        with pytest.raises(AgentProtocolError):
            session.step(observation())
    finally:
        session.close()


@pytest.mark.parametrize("field,bad", [
    ("frame", True), ("time_s", float("nan")), ("target_speed_mps", -1.0),
])
def test_constructed_observations_are_revalidated_before_user_code(field, bad):
    value = observation().model_copy(update={field: bad})
    session, agent = loaded_session()
    try:
        with pytest.raises(AgentProtocolError):
            session.step(value)
        assert "step" not in call_names(agent)
    finally:
        session.close()


def test_mutated_reset_context_is_revalidated_before_user_code():
    agent = RecordingAgent()
    session = AgentSession(agent, manifest())
    context = reset_context()
    context.fixed_delta_seconds = float("inf")
    try:
        session.load()
        with pytest.raises(AgentProtocolError):
            session.reset(context)
        assert call_names(agent) == ["load"]
        with pytest.raises(AgentLifecycleError):
            session.reset(reset_context())
    finally:
        session.close()


def test_phase_one_rejects_sensor_mode_without_calling_agent():
    configured = manifest(
        observation_mode="sensors", sensors=[{"id": "front", "type": "sensor.camera.rgb"}],
    )
    agent = RecordingAgent()
    with pytest.raises(AgentCompatibilityError):
        AgentSession(agent, configured)
    assert agent.calls == []
