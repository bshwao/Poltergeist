"""Review/publish/reconcile commit-bound checks from a saved local workspace."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from backend.config import Settings
from backend.dependencies import ApiRuntime
from backend.persistence_errors import PersistenceError
from backend.services.github_provider import GitHubError
from backend.services.github_service import GitHubService
from shared.models.github import GitHubApproval, GitHubPreviewRequest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser("preview", help="Verify immutable GitHub objects and print the exact proposed check; no write")
    preview.add_argument("request", type=Path, help="GitHubPreviewRequest JSON; no credentials")
    publish = commands.add_parser("publish", help="Publish an already reviewed disclosure once")
    publish.add_argument("id", type=UUID)
    publish.add_argument("--approve-digest", required=True)
    publish.add_argument("--approve", action="store_true", required=True)
    for name in ("get", "reconcile"):
        commands.add_parser(name).add_argument("id", type=UUID)
    commands.add_parser("history")
    commands.add_parser("status")
    args = parser.parse_args(argv)
    runtime = ApiRuntime(Settings())
    workspace = GitHubService(runtime.settings, runtime.persistence)
    try:
        if args.command == "preview":
            if args.request.stat().st_size > 65536:
                raise ValueError("Preview input must fit 64 KiB")
            result = workspace.preview(GitHubPreviewRequest.model_validate_json(args.request.read_bytes()))
        elif args.command == "publish":
            result = workspace.publish(args.id, GitHubApproval(digest=args.approve_digest, approve=args.approve))
        elif args.command == "reconcile":
            result = workspace.reconcile(args.id)
        elif args.command == "get":
            result = workspace.store.get(args.id)
        elif args.command == "history":
            print(json.dumps([item.model_dump(mode="json") for item in workspace.store.history()], indent=2))
            return 0
        else:
            result = workspace.status(runtime.settings)
        print(result.model_dump_json(indent=2))
        return 0 if getattr(result, "state", None) not in {"failed", "uncertain", "publishing"} else 1
    except (PersistenceError, GitHubError, ValueError, OSError):
        # Validation exceptions can include submitted JSON. Keep them out of CI
        # logs; the local dashboard presents the same typed validation workflow.
        print(json.dumps({"error": "GitHub action was not completed. Inspect local configuration, request schema and saved publication history; no automatic retry was made."}))
        return 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
