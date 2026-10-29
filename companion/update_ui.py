"""Native explicit update/rollback review; all transfer work stays off Tk."""

from threading import Event
import tkinter as tk
from tkinter import filedialog, ttk


class UpdatePanel:
    def __init__(self, app, parent):
        self.app = app
        self.frame = ttk.Frame(parent, padding=12)
        self.cancel = Event()
        self._busy = None
        self._view = {"candidate": None, "downloaded": False, "rollback": False}
        self._status = app.controller.update_status()
        ttk.Label(self.frame, text="Signed updates", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        current = self._status.get("current_version")
        self.status = tk.StringVar(value=(f"Installed version {current}. " if current else "") + self._status["message"])
        ttk.Label(self.frame, textvariable=self.status, wraplength=740).pack(anchor="w", pady=(6, 12))
        self.candidate = tk.StringVar(value="No release selected.")
        ttk.Label(self.frame, textvariable=self.candidate, wraplength=740).pack(anchor="w", pady=(0, 12))
        transfer_actions = ttk.Frame(self.frame)
        transfer_actions.pack(anchor="w", pady=(0, 8))
        self.check_button = app._button(transfer_actions, "Check for updates", lambda: self._start("check", lambda: app.controller.check_updates(self.cancel)))
        self.download_button = app._button(transfer_actions, "Download and verify candidate", lambda: self._start("download", lambda: app.controller.download_checked_update(self.cancel)))
        install_actions = ttk.Frame(self.frame)
        install_actions.pack(anchor="w", pady=(0, 8))
        self.install_button = app._button(install_actions, "Close and install candidate", self.install)
        self.clear_button = app._button(install_actions, "Clear candidate", lambda: self._start("clear", app.controller.clear_update))
        local_actions = ttk.Frame(self.frame)
        local_actions.pack(anchor="w", pady=(0, 8))
        self.local_button = app._button(local_actions, "Select downloaded update…", lambda: self.select_local(False))
        self.rollback_button = app._button(local_actions, "Select signed rollback…", lambda: self.select_local(True))
        self.cache_button = app._button(local_actions, "Remove cached download…", self.remove_cached)
        self.cancel_button = ttk.Button(self.frame, text="Cancel update transfer", command=self.cancel.set)
        self.cancel_button.pack(anchor="w", pady=(8, 4))
        self.progress = tk.StringVar(value="")
        ttk.Label(self.frame, textvariable=self.progress, wraplength=740).pack(anchor="w")
        ttk.Label(self.frame, text="Review the version and download size before transferring. Installation closes the app, backs up your profile, and reopens the verified release. Finish or cancel queued work first.", wraplength=740).pack(anchor="w", pady=(12, 6))
        ttk.Label(self.frame, text="A rollback needs a saved installer and current signed metadata for a lower version compatible with your profile. Clearing a feed candidate removes its download; selected local rollback files stay where you chose them.", wraplength=740).pack(anchor="w")
        self.sync_controls()

    def _start(self, kind, operation):
        from .update_delivery import DownloadCancelled
        self.cancel.clear()
        self._busy = kind
        def run():
            try:
                return operation()
            except DownloadCancelled:
                return {"cancelled": True}
            except Exception:
                if kind in {"check", "download"} and self.cancel.is_set():
                    return {"cancelled": True}
                raise
        def finished(value):
            self._busy = None
            if value.get("cancelled"):
                self.app.message.set("Update transfer cancelled.")
                return
            self._view = value
            release = value["candidate"]
            if release is None:
                self.candidate.set("No eligible update selected for this computer.")
            else:
                self.candidate.set(f"{'Rollback' if value['rollback'] else 'Update'} to {release['version']} · {release['installer_bytes'] / 1024**2:.1f} MiB · {release['channel']}\nMetadata expires {release['expires_at']}. {'Installer verified.' if value['downloaded'] else 'Metadata verified; installer has not been downloaded.'}")
        self.app._run(run, finished)
        self.sync_controls()

    def select_local(self, rollback):
        installer = filedialog.askopenfilename(parent=self.app.root, title="Select the saved signed installer",
            initialdir=self.app.controller.profile / "updates/downloads", filetypes=[("Windows installer", "*.exe")])
        if not installer:
            return
        manifest = filedialog.askopenfilename(parent=self.app.root, title="Select its signed release metadata", filetypes=[("Signed release metadata", "*.ps1")])
        if manifest:
            operation = self.app.controller.select_signed_rollback if rollback else self.app.controller.select_signed_update
            self._start("local", lambda: operation(installer, manifest))

    def remove_cached(self):
        manifest = filedialog.askopenfilename(parent=self.app.root, title="Select cached metadata to remove its download",
            initialdir=self.app.controller.profile / "updates/downloads", filetypes=[("Cached release metadata", "*.ps1")])
        if manifest:
            self._start("clear", lambda: self.app.controller.remove_cached_update(manifest))

    def install(self):
        def prepared(arguments):
            self.app._pending_update = arguments
            self.app.request_close()
        self.app._run(self.app.controller.prepare_update_install, prepared)

    def sync_controls(self):
        busy = self.app._future is not None or self.app._closing
        if not busy:
            self._busy = None
        ready = self._status["enabled"] and not busy
        selected = self._view["candidate"] is not None
        for button, enabled in ((self.check_button, ready and self._status["feed_enabled"] and not selected),
                (self.download_button, ready and selected and self._view.get("from_feed") and not self._view["downloaded"]),
                (self.install_button, ready and selected and self._view["downloaded"]),
                (self.local_button, ready and not selected), (self.rollback_button, ready and not selected),
                (self.clear_button, ready and selected),
                (self.cache_button, not busy and (self.app.controller.profile / "updates/downloads").is_dir()),
                (self.cancel_button, self._busy in {"check", "download"} and busy)):
            button.state(["!disabled"] if enabled else ["disabled"])
        self.install_button.configure(text="Close and roll back to candidate" if self._view["rollback"] else "Close and install candidate")
        received, total = self.app.controller.update_progress
        self.progress.set(f"{received / 1024**2:.1f} / {total / 1024**2:.1f} MiB received" if total else "")

    def close(self):
        self.cancel.set()
