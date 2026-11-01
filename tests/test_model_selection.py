"""Model choice survives reopening without silently starting a different pack."""
import json
from threading import Event
from time import monotonic, sleep

import pytest
from backend.persistence_errors import ConflictError
from companion.controller import CompanionController
from companion.model_manager import ModelManager
from companion.model_assets import DownloadPaused


def settled(manager):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        value = manager.snapshot()
        if not value["busy"]:
            return value
        sleep(.01)
    raise AssertionError("Model action did not finish")


def test_aim_selection_reopens_the_same_pack_without_downloading_or_starting_execution(tmp_path):
    manager = ModelManager(tmp_path)
    try:
        assert manager.snapshot()["selected_model"]["id"] == "plant"
        manager.select_model("aim")
        assert manager.snapshot()["selected_model"]["id"] == "aim"
        assert manager.environment.kind == "aim" and manager._store.asset.range_start == 138
        assert not manager.snapshot()["inference_ready"]
    finally:
        assert manager.close()
    reopened = ModelManager(tmp_path)
    try:
        assert reopened.kind == "aim" and reopened.snapshot()["source"]["state"] == "fixed"
        assert not (tmp_path / "models").exists() and not (tmp_path / "environments").exists()
        assert not (tmp_path / "database").exists()
    finally:
        assert reopened.close()


def test_corrupt_model_choice_requires_explicit_reselection_before_any_action(tmp_path):
    (tmp_path / "model-selection.json").write_text('{"model":"unknown"}', encoding="utf-8")
    manager = ModelManager(tmp_path)
    try:
        assert manager.snapshot()["selection_error"]
        with pytest.raises(ConflictError, match="Select a supported model"):
            manager.start("download")
        manager.select_model("plant")
        assert manager.snapshot()["selection_error"] is None
        assert json.loads((tmp_path / "model-selection.json").read_text(encoding="utf-8"))["model"] == "plant"
    finally:
        assert manager.close()


def test_failed_preference_commit_does_not_switch_the_active_model_or_readiness(tmp_path, monkeypatch):
    manager = ModelManager(tmp_path)
    previous = manager._store, manager.environment
    ready = manager._readiness = object()
    def failed(kind): raise OSError("Owned preference write failure fixture")
    monkeypatch.setattr(manager._selection, "save", failed)
    try:
        with pytest.raises(OSError): manager.select_model("aim")
        assert manager.kind == "plant" and (manager._store, manager.environment) == previous
        assert manager._readiness is ready
    finally:
        assert manager.close()


def test_source_pause_retains_profile_and_preserves_model_readiness(tmp_path):
    controller, other = CompanionController(tmp_path), CompanionController(tmp_path)
    controller.open()
    manager = controller._model_manager()
    ready = manager._readiness = object()
    entered, release = Event(), Event()
    def held_source(*, cancelled):
        entered.set()
        assert release.wait(10)
        if cancelled(): raise DownloadPaused()
        return {"verified": True}
    manager.hub_source.verify_source = held_source
    try:
        controller.prepare_model("check_source")
        assert entered.wait(5)
        with pytest.raises(ConflictError): manager.configure_huggingface("hf_fixture_only_token")
        assert not controller.close()
        with pytest.raises(ConflictError): other.open()
        release.set()
        status = settled(manager)
        assert status["source"]["state"] == "paused" and manager._readiness is ready
        assert controller._runtime is None and not (tmp_path / "database").exists()
        assert controller.close()
    finally:
        release.set()
        settled(manager)
        controller.close()
        other.close()


def test_model_selection_cannot_change_the_controller_of_a_running_runtime(tmp_path):
    controller = CompanionController(tmp_path)
    controller.open()
    try:
        controller.start()
        with pytest.raises(ConflictError, match="Stop the local runtime"):
            controller.select_model("aim")
        assert controller.model_status()["selected_model"]["id"] == "plant"
        assert controller.stop()
        controller.select_model("aim")
        assert controller.model_status()["selected_model"]["id"] == "aim"
    finally:
        assert controller.close()
