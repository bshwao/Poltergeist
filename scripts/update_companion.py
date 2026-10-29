"""Apply a verified downloaded release with the Companion closed."""

import argparse
from importlib.metadata import version
from pathlib import Path


def notify_failure(error, profile):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror("Poltergeist update did not complete",
            f"{error}\n\nUpdate records, if created: {profile / 'updates'}\nKeep this profile closed until recovery is reviewed.", parent=root)
    finally:
        root.destroy()


def reopen_updated_companion(receipt, install, profile):
    import json
    import re
    from companion.update_lifecycle import launch_update_helper
    from companion.updates import _plain, file_digest
    value = json.loads(_plain(receipt).read_bytes())
    build = value["target_build"]
    if value["state"] != "installed" or re.fullmatch(r"private-[0-9a-f]{16}", build) is None:
        raise ValueError("Update receipt does not identify an installed release")
    bundle = _plain(install / "versions" / build)
    if file_digest(bundle / "bundle.json") != value["bundle_manifest_sha256"]:
        raise ValueError("Updated release identity changed before reopening")
    launch_update_helper([str(bundle / "runtime/pythonw.exe"), "-I", "-B", "-m", "scripts.companion", "--data-dir", str(profile)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--wait-for-pid", type=int)
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args(argv)
    from companion.updates import apply_update
    try:
        if args.wait_for_pid is not None:
            from companion.update_lifecycle import wait_for_process_exit
            wait_for_process_exit(args.wait_for_pid)
        receipt = apply_update(args.data_dir, args.installer, args.manifest, args.install_root,
                               current_version=version("poltergeist"), rollback=args.rollback)
        print(receipt)
        if args.notify:
            reopen_updated_companion(receipt, args.install_root, args.data_dir)
        return 0
    except Exception as error:
        if not args.notify:
            raise
        notify_failure(error, args.data_dir)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
