"""The API bootstrap is usable without a CARLA server or database."""

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


def test_health_reports_liveness_without_creating_storage(tmp_path):
    storage_dir = tmp_path / "unused-storage"
    settings = Settings(
        _env_file=None,
        storage_dir=storage_dir,
        database_url=f"sqlite:///{(storage_dir / 'results.db').as_posix()}",
        carla_host="unavailable.invalid",
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "poltergeist"}
    assert app.state.settings is settings
    assert not storage_dir.exists()


def test_configured_origin_can_make_preflight_request():
    app = create_app(Settings(_env_file=None, cors_origins=["https://dashboard.example"]))

    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": "https://dashboard.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://dashboard.example"


def test_unconfigured_origin_is_rejected_by_cors():
    app = create_app(Settings(_env_file=None, cors_origins=["https://dashboard.example"]))

    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_module_launcher_uses_configured_address(monkeypatch):
    from backend import __main__ as launcher

    settings = Settings(_env_file=None, api_host="127.0.0.1", api_port=8123)
    calls = []
    monkeypatch.setattr(launcher, "get_settings", lambda: settings)
    monkeypatch.setattr(
        launcher.uvicorn,
        "run",
        lambda app, **options: calls.append((app, options)),
    )

    launcher.main()

    assert calls == [("backend.main:app", {"host": "127.0.0.1", "port": 8123})]
