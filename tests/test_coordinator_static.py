"""Production website assets stay bounded, immutable and outside the API router."""

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from coordinator.app import create_app
from coordinator.settings import CoordinatorSettings

ORIGIN = "https://coordinator.test"


def build(root):
    root.mkdir()
    (root / "assets").mkdir()
    (root / "hosted.html").write_text('<!doctype html><script type="module" src="/app/assets/app.js"></script>')
    (root / "assets/app.js").write_text('console.log("public build")')
    return root


def test_hosting_is_explicit_and_never_replaces_api_404(tmp_path):
    with TestClient(create_app(CoordinatorSettings(tmp_path / "db.sqlite", ORIGIN)), base_url=ORIGIN) as client:
        assert client.get("/app/").status_code == 404
        assert client.get("/healthz").status_code == 200


def test_hosted_html_and_assets_have_strict_headers_and_are_immutable(tmp_path):
    root = build(tmp_path / "hosted")
    app = create_app(CoordinatorSettings(tmp_path / "db.sqlite", ORIGIN, hosted_assets_dir=root))
    (root / "assets/app.js").write_text('private_mutation_sentinel')
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/app", follow_redirects=False).headers["location"] == "/app/"
        page = client.get("/app/")
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert "connect-src 'self'" in page.headers["content-security-policy"]
        assert "unsafe-inline" not in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "DENY" and page.headers["cache-control"] == "no-store"
        asset = client.get("/app/assets/app.js")
        assert asset.text == 'console.log("public build")'
        assert asset.headers["x-content-type-options"] == "nosniff"
        head = client.head("/app/assets/app.js")
        assert head.content == b"" and head.headers["content-length"] == asset.headers["content-length"]
        for path in ("/v1/unknown", "/app/unknown", "/app/assets/app.js.map", "/app/assets/%2e%2e/.env",
                     "/app/assets/%2fprivate", "/app/assets/..%5csecret"):
            result = client.get(path)
            assert result.status_code == 404 and "private_mutation_sentinel" not in result.text


def test_hosted_build_rejects_relative_missing_and_oversized_inputs(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        CoordinatorSettings(tmp_path / "db.sqlite", ORIGIN, hosted_assets_dir=Path("relative"))
    root = build(tmp_path / "hosted")
    (root / "assets/app.js").write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="limit"):
        create_app(CoordinatorSettings(tmp_path / "db.sqlite", ORIGIN, hosted_assets_dir=root))


def test_hosted_asset_links_are_refused(tmp_path):
    root = build(tmp_path / "hosted")
    outside = tmp_path / "secret.js"
    outside.write_text("private_link_sentinel")
    try:
        (root / "assets/linked.js").symlink_to(outside)
    except OSError:
        pytest.skip("This machine does not permit creating symlinks")
    with pytest.raises(ValueError, match="links"):
        create_app(CoordinatorSettings(tmp_path / "db.sqlite", ORIGIN, hosted_assets_dir=root))
