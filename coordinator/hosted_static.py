"""Explicit immutable hosted build; no SPA fallback into API or machine files."""

import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse, Response


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; "
        "object-src 'none'; frame-src 'none'; frame-ancestors 'none'; form-action 'self'",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}
ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.(js|css|svg|png|webp|jpg|jpeg|ico|woff2?)\Z")
MEDIA = {"js": "text/javascript", "css": "text/css", "svg": "image/svg+xml", "png": "image/png",
         "webp": "image/webp", "jpg": "image/jpeg", "jpeg": "image/jpeg", "ico": "image/x-icon",
         "woff": "font/woff", "woff2": "font/woff2"}


def _read(path, limit):
    for component in (path, *path.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise ValueError("Hosted build paths must not contain links")
    if not path.is_file():
        raise ValueError("Hosted build is incomplete")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("Hosted build file exceeds its limit")
    return data


def configure_hosted_static(app, settings):
    if settings.hosted_assets_dir is None:
        return
    root = Path(settings.hosted_assets_dir)
    document = _read(root / "hosted.html", 256 * 1024)
    assets, total = {}, len(document)
    directory = root / "assets"
    if not directory.is_dir():
        raise ValueError("Hosted build is incomplete")
    for index, path in enumerate(directory.iterdir()):
        if index >= 128:
            raise ValueError("Hosted build contains too many assets")
        match = ASSET_NAME.fullmatch(path.name)
        if match is None or ".." in path.name:
            continue  # Source maps and other build files have no public route.
        data = _read(path, 4 * 1024 * 1024)
        total += len(data)
        if total > 16 * 1024 * 1024:
            raise ValueError("Hosted build exceeds its total size limit")
        assets[path.name] = data, MEDIA[match[1]]
    if not any(name.endswith(".js") for name in assets):
        raise ValueError("Hosted build has no application script")

    def response(request, data, media_type):
        return Response(data if request.method == "GET" else b"", media_type=media_type,
            headers={**SECURITY_HEADERS, "Content-Length": str(len(data))})

    @app.api_route("/app", methods=["GET", "HEAD"], include_in_schema=False)
    def hosted_redirect():
        return RedirectResponse("/app/", status_code=307, headers=SECURITY_HEADERS)

    @app.api_route("/app/", methods=["GET", "HEAD"], include_in_schema=False)
    def hosted_document(request: Request):
        return response(request, document, "text/html")

    @app.api_route("/app/assets/{name:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def hosted_asset(request: Request, name: str):
        if name not in assets:
            return Response(status_code=404, headers=SECURITY_HEADERS)
        data, media_type = assets[name]
        return response(request, data, media_type)
