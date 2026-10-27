"""Catalogue preparation controls; Tk only consumes thread-safe progress values."""

import tkinter as tk
from tkinter import messagebox, ttk

from .model_assets import PLANT_WEIGHTS


class ModelPanel:
    def __init__(self, app, parent):
        self.app = app
        self.frame = ttk.Frame(parent, padding=12)
        ttk.Label(self.frame, text="PlanT 2.0 reference AI", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(self.frame, text="Prepare the official weights for the supported reference agent. The baseline controller already works without a model download.", wraplength=750).pack(anchor="w", pady=8)
        ttk.Label(self.frame, text=f"Download: {PLANT_WEIGHTS.size / 1024**2:.1f} MiB · Model: Apache-2.0 · Adapted code: MIT\nSource: huggingface.co/SimonGer/PlanT2 · revision {PLANT_WEIGHTS.id.removeprefix('plant2-')}", wraplength=750).pack(anchor="w")
        ttk.Label(self.frame, text="PlanT uses privileged simulator scene data and a provided route. It is not a camera perception model. The official checkpoint uses Python pickle; only the pinned, verified release may be loaded by the reference agent.", wraplength=750).pack(anchor="w", pady=8)
        self.status = tk.StringVar(value="Saved weights have not been checked in this session.")
        self.progress = ttk.Progressbar(self.frame, maximum=PLANT_WEIGHTS.size)
        self.progress.pack(fill="x", pady=8)
        self.detail = tk.StringVar(value="")
        ttk.Label(self.frame, textvariable=self.detail).pack(anchor="w")
        ttk.Label(self.frame, textvariable=self.status, wraplength=750).pack(anchor="w", pady=8)
        actions = ttk.Frame(self.frame)
        actions.pack(fill="x", pady=8)
        self.buttons = []
        for label, action in (("Download / resume", lambda: self._start("download")),
                              ("Check saved weights", lambda: self._start("inspect")),
                              ("Repair weights…", self._repair), ("Remove weights…", self._remove)):
            button = ttk.Button(actions, text=label, command=action)
            button.pack(side="left", padx=(0, 8))
            self.buttons.append(button)
        self.pause_button = ttk.Button(self.frame, text="Pause download / check", command=app.controller.pause_model_download)
        self.pause_button.pack(anchor="w")
        ttk.Label(self.frame, text="Environment installation is still being built. Downloaded weights do not enable PlanT in the Agent selector. No Python packages are installed into the Companion runtime by these controls. Stop local execution before repair or removal.", wraplength=750).pack(anchor="w", pady=16)
        self.sync_controls()

    def _start(self, action, *, repair=False):
        def start():
            self.app._check_files()
            if not self.app._close_requested.is_set():
                self.app.controller.prepare_model(action, repair=repair)
        self.app._run(start)

    def _repair(self):
        if messagebox.askyesno("Repair PlanT weights", "Repair verifies completed weights first. If damaged, it removes only this app's managed weights and starts a fresh download. Continue?", parent=self.app.root):
            self._start("download", repair=True)

    def _remove(self):
        if messagebox.askyesno("Remove PlanT weights", "Remove this app's managed PlanT weights and partial download? Imported models and simulation evidence are kept.", parent=self.app.root):
            self._start("remove")

    def sync_controls(self):
        if self.app._closing:
            # The background close can release profile ownership just before
            # its future is collected. Do not re-enter the closed controller.
            for button in [*self.buttons, self.pause_button]:
                button.state(["disabled"])
            return
        value = self.app.controller.model_status()
        busy = value["busy"]
        blocked = self.app._closing or self.app._future is not None
        for button in self.buttons:
            button.state(["disabled"] if busy or blocked else ["!disabled"])
        self.pause_button.state(["!disabled"] if busy and not self.app._closing else ["disabled"])
        self.progress["value"] = value["bytes"]
        stage = {"unchecked": "Not checked", "weights_ready": "Weights verified", "absent": "No weights",
                 "starting": "Starting", "paused": "Paused", "downloading": "Downloading",
                 "verifying": "Verifying", "error": "Needs attention"}[value["state"]]
        self.detail.set(f"{stage} · {value['bytes'] / 1024**2:.1f} / {value['total'] / 1024**2:.1f} MiB")
        self.status.set(value["message"] or ("Verifying every saved byte…" if value["state"] == "verifying" else "The transfer can be paused and resumed."))
