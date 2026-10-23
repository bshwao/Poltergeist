"""Explicit uninstall cleanup, limited to an installer-owned inactive profile."""

import argparse
import configparser
import json
import os
from pathlib import Path
from types import SimpleNamespace

from backend.worker_lock import WorkerLease
from companion.profile import instance_lease


def _linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def remove_owned_profile(profile, owner):
    profile = Path(profile)
    if not profile.is_absolute() or profile.name != "CompanionPrototype":
        raise ValueError("Refusing cleanup outside an explicitly named CompanionPrototype directory")
    canonical = profile.resolve()
    if os.path.normcase(str(canonical)) != os.path.normcase(str(profile.absolute())):
        raise ValueError("Linked or noncanonical profile path; data was preserved")
    for ancestor in (profile, *profile.parents):
        if _linked(ancestor):
            raise ValueError("Profile path contains a link or junction; data was preserved")
    marker = profile / "installation-owner.ini"
    if _linked(marker):
        raise ValueError("Profile ownership marker is linked; data was preserved")
    ownership = configparser.ConfigParser(interpolation=None)
    data = marker.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            if os.name != "nt":
                raise
            text = data.decode("mbcs")
    ownership.read_string(text)
    if (ownership.get("Owner", "AppId", fallback=None) != owner
            or os.path.normcase(ownership.get("Owner", "Profile", fallback="")) != os.path.normcase(str(profile))):
        raise ValueError("This installer does not own the profile; data was preserved")
    leases = [instance_lease(profile), WorkerLease(SimpleNamespace(file_path=profile / "database" / "poltergeist.db"))]
    held = []
    protected = {profile / "companion-instance.worker.lock", profile / "database" / "poltergeist.db.worker.lock"}
    directories = []
    try:
        for lease in leases:
            lease.acquire()
            held.append(lease)
        # Inspect the full tree before the first deletion. Never follow links.
        count = 0
        for directory, folders, files in os.walk(profile, followlinks=False):
            directory = Path(directory)
            if len(directory.relative_to(profile).parts) > 64:
                raise ValueError("Profile tree is too deep for automated cleanup; data was preserved")
            directories.append(directory)
            for name in (*folders, *files):
                candidate = directory / name
                count += 1
                if count > 500_000:
                    raise ValueError("Profile exceeds automated cleanup limits; data was preserved")
                if _linked(candidate) or not candidate.resolve().is_relative_to(canonical):
                    raise ValueError("Profile contains a link or junction; data was preserved")
        for directory, folders, files in os.walk(profile, topdown=False, followlinks=False):
            directory = Path(directory)
            for name in files:
                path = directory / name
                if path not in protected:
                    if _linked(path) or not path.resolve().is_relative_to(canonical):
                        raise ValueError("Profile changed during cleanup; remaining data was preserved")
                    path.unlink()
            for name in folders:
                path = directory / name
                if _linked(path) or not path.resolve().is_relative_to(canonical):
                    raise ValueError("Profile changed during cleanup; remaining data was preserved")
                try:
                    path.rmdir()
                except OSError:
                    pass  # The database lock is held until cleanup finishes.
    finally:
        for lease in reversed(held):
            lease.release()
    # Only delete known lock files and empty directories after releasing locks.
    # Newly created files are preserved if another process starts at this point.
    for path in protected:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    for path in reversed(directories):
        try:
            path.rmdir()
        except OSError:
            pass
    return {"schema_version": "poltergeist-profile-removal-v1", "removed": not profile.exists(),
            "scope": "Explicit removal of an inactive installer-owned plain directory; no linked paths were followed."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = remove_owned_profile(args.profile, args.owner)
    except Exception as error:
        report = {"schema_version": "poltergeist-profile-removal-v1", "removed": False,
                  "error": f"{type(error).__name__}: {error}"}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["removed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
