"""Measured model readiness in the selected SDK worker, separate from static checks."""

from uuid import uuid4

from shared.models import (ActorState, AgentObservation, AgentReset, AgentRoadRaster,
    AgentSceneContext, AgentSceneObject, BoundingBoxExtent, Transform, Vector3)
from shared.models.registry import AgentExecutionOptions, AgentRegistration
from simulator.agents.fingerprints import build_agent_snapshot
from simulator.agents.process import AgentProcessOptions, ProcessAgent
from simulator.agents.reference_ai.checkpoint import CHECKPOINT_SHA256, verify_checkpoint
from simulator.errors import SimulationCancelled

from .model_assets import AssetError, DownloadPaused


class ModelReadinessError(AssetError):
    pass


def managed_registration(installed, checkpoint):
    return AgentRegistration(package_path=str(installed.agent_package), python_executable=str(installed.interpreter),
        configuration={"checkpoint_path": str(checkpoint), "device": "cpu", "torch_threads": 2, "cuda_graphs": False},
        model_files=[str(installed.root / "capsule.json")],
        isolated_runtime={"prefix": str(installed.interpreter.parent), "manifest_sha256": installed.manifest_sha256}, trusted_local_code=True,
        process_options=AgentExecutionOptions(load_timeout_s=300., reset_timeout_s=20., step_timeout_s=10.,
                                              close_timeout_s=3., terminate_timeout_s=3.))


def _observation(blocked=False):
    classes = [0] * (128 * 128)
    for row in range(56, 72):
        classes[row * 128:(row + 1) * 128] = [3 if row in (56, 71) else 1] * 128
    objects = [] if not blocked else [AgentSceneObject(actor_id="lead", kind="vehicle",
        transform=Transform(position=Vector3(x=8.)), extent=BoundingBoxExtent(x=2., y=1., z=1.),
        velocity_mps=Vector3())]
    return AgentObservation(protocol_version="1.2", frame=100, time_s=0., dt_s=.05,
        ego=ActorState(actor_id="ego", transform=Transform(), velocity_mps=Vector3(x=8.)),
        actors=[], target_speed_mps=12., scene=AgentSceneContext(source="synthetic",
            route=[Transform(position=Vector3(x=float(index))) for index in range(41)], objects=objects,
            speed_limit_mps=50. / 3.6, road_raster=AgentRoadRaster(width=128, height=128,
                pixels_per_meter=2., classes=classes, ego_row=64., ego_column=64.)))


class ModelReadiness:
    def __init__(self):
        self._agent = None
        self.last_failure = None

    @property
    def retained(self):
        return self._agent is not None

    def close(self):
        if self._agent is None:
            return True
        try:
            self._agent.close()
        except Exception:
            return False
        self._agent = None
        return True

    def run(self, installed, checkpoint, *, cancelled=lambda: False, progress=lambda *args: None):
        if self.retained:
            raise ModelReadinessError("The previous model process is still stopping")
        phase = "checkpoint"
        self.last_failure = None
        try:
            if cancelled():
                raise DownloadPaused()
            progress("checking_checkpoint", 0, 1)
            verify_checkpoint(checkpoint)
            if cancelled():
                raise DownloadPaused()
            phase = "worker"
            request = managed_registration(installed, checkpoint)
            snapshot = build_agent_snapshot(request, uuid4())
            self._agent = ProcessAgent(installed.agent_package, python_executable=installed.interpreter,
                options=AgentProcessOptions(**request.process_options.model_dump()), snapshot=snapshot)
            self._agent.set_cancellation(cancelled)
            progress("loading_model", 0, 1)
            self._agent.load(snapshot.configuration)
            phase = "inference"
            progress("checking_inference", 0, 2)
            context = AgentReset(protocol_version="1.2", seed=0, map_name="synthetic", ego_actor_id="ego", fixed_delta_seconds=.05)
            self._agent.reset(context)
            clear = self._agent.step(_observation())
            progress("checking_inference", 1, 2)
            self._agent.reset(context)
            blocked = self._agent.step(_observation(True))
            for result in (clear, blocked):
                metadata = result.diagnostics.metadata
                if (metadata.get("checkpoint_sha256") != CHECKPOINT_SHA256 or metadata.get("device") != "cpu"
                        or type(metadata.get("parameter_count")) is not int or metadata["parameter_count"] <= 30_000_000
                        or len(result.diagnostics.planned_trajectory) != 20
                        or type(metadata.get("inference_wall_time_s")) not in (float, int)
                        or metadata["inference_wall_time_s"] <= 0):
                    raise ModelReadinessError("The model did not produce the expected measured CPU inference")
            if clear.diagnostics.planned_trajectory == blocked.diagnostics.planned_trajectory:
                raise ModelReadinessError("The model did not respond to the changed scene fixture")
            progress("checking_inference", 2, 2)
            return {"schema": "poltergeist-managed-model-readiness-v1", "passed": True,
                "scope": "CPU inference with two synthetic structured-scene fixtures; no live CARLA or safety claim.",
                "generation": installed.generation, "checkpoint_sha256": CHECKPOINT_SHA256,
                "device": "cpu", "torch_version": clear.diagnostics.metadata["torch_version"],
                "parameter_count": clear.diagnostics.metadata["parameter_count"],
                "inference_wall_times_s": [row.diagnostics.metadata["inference_wall_time_s"] for row in (clear, blocked)],
                "scene_response_changed": True, "isolated_worker": True}
        except (SimulationCancelled, DownloadPaused):
            raise DownloadPaused() from None
        except ModelReadinessError:
            raise
        except Exception as error:
            # Retained only on this local owner for diagnosis. UI status uses
            # the fixed actionable message, never raw worker/configuration text.
            self.last_failure = {"stage": phase, "type": type(error).__name__, "detail": str(error)[:4096],
                "stderr_tail": "" if self._agent is None else self._agent.stderr_tail[-8192:]}
            raise ModelReadinessError({"checkpoint": "PlanT weights need verification or repair before model checks",
                "worker": "The isolated PlanT worker could not load. Check or repair its environment.",
                "inference": "The PlanT inference check failed. Check or repair its environment."}[phase]) from None
        finally:
            if not self.close():
                raise ModelReadinessError("The model process is still stopping. Keep the app open and retry Pause or Close.")
