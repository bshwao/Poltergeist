"""Bounded decision spooling, linked to raw sensor bytes stored only once."""

from hashlib import sha256
import json
from math import isclose
import os

from shared.models import AgentObservation, AgentReset
from shared.models.decision import DecisionPage, DecisionRecord, DecisionSensor
from backend.persistence_errors import PersistenceError
from backend.storage import ArtifactError, _json


class DecisionWriter:
    def __init__(self, store, simulation_id, capture=None):
        self.store, self.simulation_id, self.capture = store, simulation_id, capture
        self.stage, self.final, self.reference = store._begin("decisions", simulation_id)
        self.owner = self.stage.stat()
        self.closed = False
        self.pending = self.context = None
        self.total = self.applied = 0
        self.digest = sha256()

    def reset(self, context):
        if self.closed or self.context is not None or self.total or self.pending is not None:
            raise ArtifactError("Decision reset context must precede all inputs")
        self.context = AgentReset.model_validate(context.model_dump(mode="json"))

    def observation(self, observation, control_mode):
        if self.closed or self.pending is not None:
            raise ArtifactError("Previous decision input has not been resolved")
        if not isinstance(observation, AgentObservation):
            raise ArtifactError("Decision input must be a validated agent observation")
        payload = observation.model_dump(mode="json")
        sensors = []
        for sensor in observation.sensors:
            if self.capture is None:
                raise ArtifactError("Sensor decision evidence requires raw capture")
            sensors.append(DecisionSensor(
                header=sensor.model_dump(mode="json", exclude={"data_base64"}),
                byte_length=len(sensor.data_base64) // 4 * 3 - (len(sensor.data_base64) - len(sensor.data_base64.rstrip("="))),
                sample_index=self.capture.agent_sample_index(sensor.sensor_id, sensor.frame, sensor.sha256),
            ))
        payload["sensors"] = []
        self.pending = DecisionRecord(frame=observation.frame, time_s=observation.time_s,
            status="unresolved", observation=payload, sensors=sensors, control_mode=control_mode)

    def decision(self, record):
        if self.closed or self.pending is None:
            raise ArtifactError("Decision has no preceding saved input")
        payload = dict(record["observation"])
        sensor_headers = [{key: value for key, value in sensor.items() if key != "data_base64"}
                          for sensor in payload["sensors"]]
        payload["sensors"] = []
        if payload != self.pending.observation or sensor_headers != [sensor.header for sensor in self.pending.sensors]:
            raise ArtifactError("Decision does not match its preceding input")
        saved = DecisionRecord.model_validate(record | {"status": "applied", "observation": payload,
            "sensors": [sensor.model_dump(mode="json") for sensor in self.pending.sensors]})
        self._append(saved)
        self.pending = None

    def _append(self, record):
        line = (_json(record.model_dump(mode="json")) + "\n").encode("utf-8")
        with self.store._contained(self.stage / ".records").open("ab", buffering=0) as stream:
            self.store._write_bytes(stream, line)
        self.digest.update(line)
        self.total += 1
        self.applied += record.status == "applied"

    def commit(self, recording_artifact_index=None):
        if self.closed:
            raise ArtifactError("Decision writer is already closed")
        try:
            if self.pending is not None:
                self._append(self.pending)
                self.pending = None
            header = {"format": "decision-timeline-v1", "simulation_id": str(self.simulation_id),
                "total": self.total, "applied": self.applied, "sha256": self.digest.hexdigest(),
                "reset": None if self.context is None else self.context.model_dump(mode="json"),
                "recording_artifact_index": recording_artifact_index}
            with self.store._contained(self.stage / "timeline.ndjson").open("xb", buffering=0) as target:
                self.store._write_bytes(target, (_json(header) + "\n").encode("utf-8"))
                spool = self.store._contained(self.stage / ".records")
                if spool.exists():
                    with spool.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            self.store._write_bytes(target, chunk)
                    size = spool.stat().st_size
                    spool.unlink()
                    with self.store._budget_lock:
                        self.store._used_bytes = max(0, self.store._used_bytes - size)
                target.flush()
                os.fsync(target.fileno())
            self.store._publish(self.stage, self.final)
            self.closed = True
            return self.reference
        except BaseException:
            self.abort()
            raise

    def abort(self):
        if not self.closed:
            self.store._rollback(self.stage, self.final, self.owner)
            self.closed = True


def decision_page(store, result, frames, *, offset=0, limit=25, validate_sensors=False, on_record=None):
    """Validate the entire lightweight trace, retaining only the requested page.

    Raw bytes are verified at publication and on indexed sample downloads; page
    navigation never decodes all camera frames again.
    """
    refs = [ref for ref in result.artifacts if ref.kind == "decisions"]
    transitions = max(0, len(frames) - 1)
    base = dict(simulation_id=result.simulation.id, transitions=transitions, offset=offset, limit=limit)
    if not refs:
        return DecisionPage(**base, available=False, coverage="unavailable", total=0, applied=0,
                            reset=None, recording_artifact_index=None, records=[], telemetry=[])
    if len(refs) != 1:
        raise PersistenceError("Result must have at most one decision timeline")
    records = []
    count = applied = 0
    digest = sha256()
    audit_by_frame = {}
    if result.control_audit is not None:
        for intervention in result.control_audit.interventions:
            audit_by_frame.setdefault(intervention.frame, []).append(intervention)
    with store.validate_reference(refs[0], result.simulation.id).open("rb") as stream:
        header = json.loads(stream.readline())
        if (not isinstance(header, dict) or header.get("format") != "decision-timeline-v1"
                or header.get("simulation_id") != str(result.simulation.id)):
            raise PersistenceError("Decision timeline header does not match its run")
        reset = None if header.get("reset") is None else AgentReset.model_validate(header["reset"])
        if reset is not None:
            scenario = result.simulation.scenario
            if (reset.seed != scenario.seed or reset.map_name != scenario.map
                    or not isclose(reset.fixed_delta_seconds, scenario.fixed_delta_seconds, abs_tol=1e-9)
                    or (frames and reset.ego_actor_id != frames[0].ego.actor_id)):
                raise PersistenceError("Decision reset does not match its run")
            snapshot = result.simulation.agent_snapshot
            if snapshot is not None and (reset.protocol_version != snapshot.manifest.protocol_version
                    or reset.sensors != snapshot.manifest.sensors):
                raise PersistenceError("Decision reset differs from the registered agent contract")
            outcome = result.driving_outcome
            if outcome is not None and (reset.route != outcome.resolved_route or reset.destination != outcome.destination):
                raise PersistenceError("Decision reset differs from resolved navigation")
        recording_index = header.get("recording_artifact_index")
        manifest = None
        if recording_index is not None:
            if (type(recording_index) is not int or not 0 <= recording_index < len(result.artifacts)
                    or result.artifacts[recording_index].kind != "recording"):
                raise PersistenceError("Decision sensor recording reference is invalid")
            if validate_sensors:
                manifest = store.read_capture_manifest(result.artifacts[recording_index])
        for line in stream:
            digest.update(line)
            record = DecisionRecord.model_validate_json(line)
            if count >= len(frames) or (count and applied != count):
                raise PersistenceError("Decision inputs must form a contiguous prefix of telemetry")
            before = frames[count]
            observation = record.observation
            expected_ego = before.ego.model_dump(mode="json")
            if observation.get("observation_mode") == "sensors":
                for key in ("bounding_box_extent", "bounding_box_transform", "speed_limit_mps"):
                    expected_ego[key] = None
            if (record.frame != before.frame or not isclose(record.time_s, before.time_s, abs_tol=1e-6)
                    or observation.get("ego") != expected_ego
                    or not isclose(observation.get("dt_s", 0), result.simulation.scenario.fixed_delta_seconds, abs_tol=1e-9)):
                raise PersistenceError("Decision input does not match its measured preceding frame")
            sensor_mode = observation.get("observation_mode") == "sensors"
            if result.agent_inputs is not None and observation.get("observation_mode") != result.agent_inputs.observation_mode:
                raise PersistenceError("Decision input mode differs from the run provenance")
            if result.simulation.agent_snapshot is not None and reset is None:
                raise PersistenceError("Registered agent decisions require their delivered reset context")
            if not sensor_mode:
                AgentObservation.model_validate(observation)
                if record.sensors or observation.get("actors") != (None if before.actors is None else
                        [actor.model_dump(mode="json") for actor in before.actors]):
                    raise PersistenceError("State decision inputs differ from measured actors")
            else:
                if not record.sensors or recording_index is None:
                    raise PersistenceError("Sensor decisions require referenced raw input samples")
                if manifest is not None:
                    _validate_sensor_links(record, manifest, result)
            if result.control_audit is not None and record.control_mode != result.control_audit.mode:
                raise PersistenceError("Decision control authority differs from the run audit")
            if record.status == "applied":
                if result.simulation.agent_snapshot is not None and record.agent_result is None:
                    raise PersistenceError("Registered agent action is missing its SDK response")
                if count >= transitions:
                    raise PersistenceError("Decision has no measured resulting frame")
                after = frames[count + 1]
                if record.resulting_frame != after.frame or record.resulting_ego != after.ego or record.applied_control != after.control:
                    raise PersistenceError("Decision output differs from measured transition")
                if result.control_audit is not None:
                    interventions = audit_by_frame.get(record.frame, [])
                    if ([item.kind for item in interventions] != record.interventions or any(
                            item.proposed_control != record.proposed_control or item.applied_control != record.applied_control
                            for item in interventions)):
                        raise PersistenceError("Decision interventions differ from the run audit")
                applied += 1
            if offset <= count < offset + limit:
                records.append(record)
            if on_record is not None:
                # Consumers must publish nothing until this function returns:
                # the whole-file count/checksum is verified after the stream.
                on_record(record)
            count += 1
        if (type(header.get("total")) is not int or header["total"] != count
                or type(header.get("applied")) is not int or header["applied"] != applied
                or header.get("sha256") != digest.hexdigest()):
            raise PersistenceError("Decision timeline count or checksum mismatch")
    if result.simulation.status == "completed" and (applied != transitions or count != applied):
        raise PersistenceError("Completed run is missing applied decision evidence")
    coverage = "empty" if count == transitions == 0 else "complete" if count == applied == transitions else "partial"
    return DecisionPage(**base, available=True, coverage=coverage, total=count, applied=applied,
        reset=reset, recording_artifact_index=recording_index, records=records,
        telemetry=list(frames[offset:offset + len(records) + 1]) if records else [])


def _validate_sensor_links(record, manifest, result):
    inputs = result.agent_inputs
    if inputs is None or inputs.observation_mode != "sensors":
        raise PersistenceError("Sensor decision has no run input provenance")
    observation = record.observation
    if (observation.get("protocol_version") != "1.3" or observation.get("actors") is not None
            or observation.get("scene") is not None or observation.get("traffic_light_red") is not None
            or observation.get("ego_state_source") != inputs.ego_state_source
            or observation.get("sensor_source") != inputs.source):
        raise PersistenceError("Sensor decision contains invalid input provenance or privileged state")
    expected = {spec.id: spec for spec in inputs.sensors}
    seen = set()
    for sensor in record.sensors:
        header = sensor.header
        sensor_id = header.get("sensor_id")
        if sensor_id not in expected or sensor_id in seen or sensor.sample_index >= len(manifest["samples"]):
            raise PersistenceError("Decision sensor ID or raw sample index is invalid")
        seen.add(sensor_id)
        sample = manifest["samples"][sensor.sample_index]
        metadata = sample["metadata"]
        if (header.get("frame") != record.frame or sample["frame"] != record.frame
                or not isclose(header.get("timestamp_s", -1), record.time_s, abs_tol=1e-6)
                or metadata.get("agent_sensor_id") != sensor_id or sample["sha256"] != header.get("sha256")
                or sensor.byte_length != sample["byte_length"] or metadata.get("sensor_type") != header.get("type")
                or any(header.get(key) != metadata.get(key) for key in ("encoding", "width", "height", "fov_deg", "channels"))):
            raise PersistenceError("Decision sensor header differs from its captured input")
    if seen != set(expected):
        raise PersistenceError("Decision input is missing a declared sensor")
