"""Supported inference packs, independent of where their pinned bytes are hosted."""
from .model_assets import ModelAsset, PLANT_WEIGHTS
from simulator.agents.aim.checkpoint import (CHECKPOINT_BYTES, CHECKPOINT_FILENAME, CHECKPOINT_SHA256,
    CHECKPOINT_URL, MODEL_ID, SOURCE_BYTES, SOURCE_OFFSET, SOURCE_REVISION)

AIM_WEIGHTS = ModelAsset(MODEL_ID, CHECKPOINT_FILENAME, CHECKPOINT_URL, CHECKPOINT_BYTES,
    CHECKPOINT_SHA256, range_start=SOURCE_OFFSET, source_size=SOURCE_BYTES)


def aim_manifest(weights):
    return {"protocol_version": "1.3", "agent_id": "poltergeist-aim-camera", "name": "Managed AIM camera (CPU)",
        "version": "cvpr2021-" + SOURCE_REVISION[:12], "entry_point": "agent:AIMAgent", "observation_mode": "sensors",
        "control_mode": "vehicle_control", "navigation_mode": "provided_route", "assets": [],
        "sensors": [{"id": "front-camera", "type": "sensor.camera.rgb",
            "attributes": {"image_size_x": 400, "image_size_y": 300, "fov": 100.},
            "mount": {"position": {"x": 1.3, "z": 2.3}}}],
        "configuration": {"device": "cpu", "torch_threads": 2, "inference_budget_s": 1., "checkpoint_path": str(weights)}}


def model_info(kind):
    if kind == "plant":
        return {"id": "plant", "name": "PlanT 2.0 (CPU)", "size": PLANT_WEIGHTS.size, "huggingface": True,
            "description": "Learned waypoint and speed planning from privileged simulator scene data and a provided route. This is not a camera perception model.",
            "source": "Hugging Face · SimonGer/PlanT2 · Apache-2.0 weights / MIT integration",
            "notice": "The supported upstream checkpoint uses Python pickle. Only the pinned, fully verified release can be loaded."}
    if kind == "aim":
        return {"id": "aim", "name": "AIM camera (CPU)", "size": AIM_WEIGHTS.size, "huggingface": False,
            "description": "Learned waypoints from front RGB pixels, with declared ideal simulated ego kinematics and a supplied route. Other actors' ground-truth state is excluded.",
            "source": "Official TransFuser CVPR 2021 AIM release · MIT integration; checkpoint terms not separately declared by upstream",
            "notice": "Downloads only the pinned AIM member from the official archive. Restricted weight loading follows full size/hash verification. Initial compatibility work does not establish road safety or real-time performance."}
    raise ValueError("Unknown supported AI model")


def model_options():
    return [model_info(kind) for kind in ("plant", "aim")]
