"""Model lifecycle stays off Tk and retains profile ownership until I/O exits."""

from threading import Event
from time import monotonic

import pytest

from backend.persistence_errors import ConflictError
from companion.controller import CompanionController
from companion.model_assets import AssetStore
from companion.model_manager import ModelManager
from test_model_assets import asset_server, DATA
from test_relay_client import tls


def settled(controller):
    deadline = monotonic() + 15
    while monotonic() < deadline:
        result = controller.model_status()
        if not result["busy"]:
            return result
        Event().wait(.01)
    raise AssertionError("Owned model operation did not stop")


def test_app_checks_downloads_repairs_removes_and_reopens_without_registering_an_agent(tmp_path, tls, monkeypatch):
    with asset_server(tls) as (asset, open_request, _):
        monkeypatch.setattr("companion.model_manager.AssetStore", lambda profile: AssetStore(profile, asset, _open=open_request))
        controller = CompanionController(tmp_path)
        assert not (tmp_path / "models").exists()
        controller.open()
        try:
            assert controller.model_status()["state"] == "unchecked"
            controller.prepare_model("inspect")
            assert settled(controller)["state"] == "absent"
            controller.prepare_model("download")
            assert settled(controller)["state"] == "weights_ready"
            assert controller.snapshot()["agents"] == []
            assert not (tmp_path / "database").exists()
            store = controller._models._store
            store.path.write_bytes(b"damaged")
            controller.prepare_model("inspect")
            assert settled(controller)["state"] == "error"
            controller.start()
            for action, repair in (("download", True), ("remove", False)):
                with pytest.raises(ConflictError, match="Stop the local runtime"):
                    controller.prepare_model(action, repair=repair)
            assert store.path.read_bytes() == b"damaged"
            assert controller.stop()
            controller.prepare_model("download", repair=True)
            assert settled(controller)["state"] == "weights_ready"
            assert store.path.read_bytes() == DATA
            controller.prepare_model("remove")
            assert settled(controller)["state"] == "absent" and not store.path.exists()
            assert controller.close()
            controller.open()
            controller.prepare_model("inspect")
            assert settled(controller)["state"] == "absent"
        finally:
            assert controller.close()


def test_close_requests_pause_and_retains_profile_and_transfer_leases_until_io_returns(tmp_path, tls, monkeypatch):
    entered, release = Event(), Event()
    with asset_server(tls) as (asset, open_request, observed):
        def blocked(request, **kwargs):
            entered.set()
            assert release.wait(15)
            return open_request(request, **kwargs)
        monkeypatch.setattr("companion.model_manager.AssetStore", lambda profile: AssetStore(profile, asset, _open=blocked))
        controller, other = CompanionController(tmp_path), CompanionController(tmp_path)
        controller.open()
        try:
            controller.prepare_model("download")
            assert entered.wait(5)
            with pytest.raises(ConflictError, match="current model"):
                controller.prepare_model("inspect")
            assert not controller.close()
            with pytest.raises(ConflictError, match="already open"):
                other.open()
            with pytest.raises(ConflictError):
                AssetStore(tmp_path, asset).remove()
            release.set()
            assert settled(controller)["state"] == "paused"
            assert not controller._models._store.path.exists()
            assert controller.close()
            other.open()
            assert len(observed) == 1
        finally:
            release.set()
            if controller._owned:
                settled(controller)
            assert controller.close()
            assert other.close()


def test_model_operations_require_open_profile_and_known_local_action(tmp_path):
    controller = CompanionController(tmp_path)
    with pytest.raises(ConflictError, match="Open"):
        controller.prepare_model("download")
    controller.open()
    try:
        with pytest.raises(ValueError, match="Unknown"):
            controller.prepare_model("arbitrary-url")
        with pytest.raises(ValueError, match="Unknown"):
            controller.prepare_model("inspect", repair=True)
        assert not (tmp_path / "models").exists()
    finally:
        assert controller.close()


@pytest.mark.parametrize("kind", ["plant", "aim"])
def test_removing_environment_preserves_weights_and_resets_readiness_progress(tmp_path, kind):
    manager = ModelManager(tmp_path)
    try:
        manager.select_model(kind)
        store = manager._store
        with manager.environment.owned():
            manager.environment.runtime.mkdir()
            manager.environment.python.write_bytes(b"fixture interpreter; never executed")
        store._own()
        try:
            store.path.write_bytes(b"retained fixture checkpoint; not verified")
        finally:
            store._lease.release()
        manager._readiness = {"fixture": True}
        manager._status.update(state="ready", bytes=1, total=1)
        manager.start("remove_environment")
        deadline = monotonic() + 15
        while manager.snapshot()["busy"] and monotonic() < deadline:
            Event().wait(.01)
        value = manager.snapshot()
        assert not value["busy"]
        assert value["state"] == "environment_removed"
        assert "Saved weights are kept" in value["message"]
        assert not value["inference_ready"]
        assert value["bytes"] == 0 and value["total"] == store.asset.size
        assert store.path.read_bytes() == b"retained fixture checkpoint; not verified"
        assert not manager.environment.python.exists()
        # Only an explicit weight inspection can certify the retained bytes.
        manager.start("inspect")
        deadline = monotonic() + 15
        while manager.snapshot()["busy"] and monotonic() < deadline:
            Event().wait(.01)
        assert manager.snapshot()["state"] == "error"
        assert "size does not match" in manager.snapshot()["message"]
    finally:
        assert manager.close()
