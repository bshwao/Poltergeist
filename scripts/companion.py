"""Launch the private companion with its real Python runtime."""

import argparse
import logging
import os
from pathlib import Path
import sys
import tempfile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--development", action="store_true", help="Use the source environment instead of a verified bundle")
    args = parser.parse_args(argv)
    logger = handler = controller = root = pending_update = None
    try:
        from companion.profile import configure_gui_environment, prepare_logs, profile_path
        bundle = None
        if not args.development:
            from scripts.portable import bundle_root, verify_bundle
            bundle = bundle_root()
            configure_gui_environment(bundle)
            from companion.install_guard import mark_installation_in_use
            mark_installation_in_use()
        profile = profile_path(args.data_dir, bundle=bundle)
        from companion.controller import CompanionController
        controller = CompanionController(profile, bundle=bundle)
        controller.open()
        logger, handler = prepare_logs(profile)
        temporary = profile / "tmp"
        temporary.mkdir(exist_ok=True)
        os.environ["TEMP"] = os.environ["TMP"] = str(temporary)
        tempfile.tempdir = str(temporary)
        # CARLA can write a native map cache relative to the working directory.
        os.chdir(profile)
        import tkinter as tk
        from companion.ui import CompanionWindow
        root = tk.Tk()
        root.report_callback_exception = lambda kind, error, trace: logger.error("Desktop callback failed", exc_info=(kind, error, trace))
        window = CompanionWindow(root, controller, logger,
            autostart=controller.config.setup_completed,
            startup_check=(lambda: verify_bundle(bundle)) if bundle is not None else None)
        logger.info("Companion opened with real interpreter %s", sys.executable)
        root.mainloop()
        pending_update = window._pending_update
        return 0
    except Exception as error:
        if logger:
            logger.exception("Companion startup failed")
        try:
            import tkinter as tk
            from tkinter import messagebox
            if root is None:
                root = tk.Tk()
                root.withdraw()
            messagebox.showerror("Poltergeist Companion", f"{type(error).__name__}: {error}", parent=root)
            root.destroy()
        except Exception:
            print(f"Companion startup failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    finally:
        closed = True
        if controller is not None:
            closed = controller.close()
        if logger and handler:
            logging.getLogger().removeHandler(handler)
            handler.close()
        if pending_update is not None and closed:
            from companion.update_lifecycle import launch_update_helper
            try:
                launch_update_helper(pending_update)
            except Exception as error:
                from scripts.update_companion import notify_failure
                notify_failure(error, controller.profile)


if __name__ == "__main__":
    raise SystemExit(main())
