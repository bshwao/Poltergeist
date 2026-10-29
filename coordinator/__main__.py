"""Run the separate coordinator with explicit storage, origin and TLS files."""

import argparse
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--certfile", type=Path, required=True)
    parser.add_argument("--keyfile", type=Path, required=True)
    parser.add_argument("--hosted-assets", type=Path, help="Absolute directory of the production hosted frontend build")
    parser.add_argument("--max-connections", type=int, default=100)
    parser.add_argument("--max-transfers", type=int, default=8)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Use a valid local listening port")
    if not args.certfile.is_file() or not args.keyfile.is_file():
        parser.error("Provide existing TLS certificate and key files")
    from .app import create_app
    from .settings import CoordinatorSettings
    import uvicorn
    settings = CoordinatorSettings(args.database, args.origin, hosted_assets_dir=args.hosted_assets,
        max_connections=args.max_connections, max_transfers=args.max_transfers,
        monitor_token=os.environ.get("POLTERGEIST_MONITOR_TOKEN"))
    uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1,
        ssl_certfile=str(args.certfile), ssl_keyfile=str(args.keyfile),
        proxy_headers=False, access_log=False, log_level="warning", ws_max_size=262144,
        ws_max_queue=1, ws_per_message_deflate=False, limit_concurrency=128, timeout_keep_alive=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
