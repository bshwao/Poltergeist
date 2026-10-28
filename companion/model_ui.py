"""Catalogue preparation controls; Tk only consumes thread-safe progress values."""

import tkinter as tk
from tkinter import messagebox, ttk

from .model_assets import PLANT_WEIGHTS


class ModelPanel:
    def __init__(self, app, parent):
        self.app = app
        self.frame = ttk.Frame(parent)
        self.canvas = tk.Canvas(self.frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        panel = ttk.Frame(self.canvas, padding=12)
        item = self.canvas.create_window((0, 0), window=panel, anchor="nw")
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(item, width=event.width))
        panel.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        ttk.Label(panel, text="PlanT 2.0 reference AI", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(panel, text="Prepare the official weights for the supported reference agent. The baseline controller already works without a model download.", wraplength=750).pack(anchor="w", pady=8)
        ttk.Label(panel, text=f"Download: {PLANT_WEIGHTS.size / 1024**2:.1f} MiB · Model: Apache-2.0 · Adapted code: MIT\nSource: huggingface.co/SimonGer/PlanT2 · revision {PLANT_WEIGHTS.id.removeprefix('plant2-')}", wraplength=750).pack(anchor="w")
        ttk.Label(panel, text="PlanT uses privileged simulator scene data and a provided route. It is not a camera perception model. The official checkpoint uses Python pickle; only the pinned, verified release may be loaded by the reference agent.", wraplength=750).pack(anchor="w", pady=8)
        self.status = tk.StringVar(value="Saved weights have not been checked in this session.")
        self.progress = ttk.Progressbar(panel, maximum=PLANT_WEIGHTS.size)
        self.progress.pack(fill="x", pady=8)
        self.detail = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.detail).pack(anchor="w")
        ttk.Label(panel, textvariable=self.status, wraplength=750).pack(anchor="w", pady=8)
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=8)
        self.buttons = []
        for label, action in (("Download / resume", lambda: self._start("download")),
                              ("Check saved weights", lambda: self._start("inspect")),
                              ("Repair weights…", self._repair), ("Remove weights…", self._remove)):
            button = ttk.Button(actions, text=label, command=action)
            button.pack(side="left", padx=(0, 8))
            self.buttons.append(button)
        self.pause_button = ttk.Button(panel, text="Pause download / check", command=app.controller.pause_model_download)
        self.pause_button.pack(anchor="w")
        ttk.Label(panel, text="Windows x64 CPU pack · separate Python 3.12 runtime · about 296 MiB of environment downloads plus weights · allow 3 GiB free space. CPU inference can run slower than simulation time. Stop the local runtime before preparing or checking AI.", wraplength=750).pack(anchor="w", pady=12)
        environment_actions = ttk.Frame(panel)
        environment_actions.pack(fill="x", pady=8)
        for label, action in (("Install / resume AI", lambda: self._start("prepare")),
                              ("Check AI readiness", lambda: self._start("check_environment")),
                              ("Repair AI…", self._repair_environment),
                              ("Remove AI…", self._remove_environment)):
            button = ttk.Button(environment_actions, text=label, command=action)
            button.pack(side="left", padx=(0, 8))
            self.buttons.append(button)
        self.enable_button = ttk.Button(panel, text="Enable PlanT for local runs…", command=self._enable)
        self.enable_button.pack(anchor="w", pady=8)
        self.starter_button = ttk.Button(panel, text="Enable teaching starter (no download)…", command=self._enable_starter)
        self.starter_button.pack(anchor="w", pady=4)
        ttk.Label(panel, text="Enable only after import and real pretrained inference checks pass. Enabling registers trusted local model code and starts the local runtime; then select Managed PlanT in the Agent selector. Model and dependency notices are retained in the managed environment's notices and site-packages folders. Hosted execution keeps its existing policy.", wraplength=750).pack(anchor="w", pady=8)
        def bind_scroll(widget):
            widget.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"))
            for child in widget.winfo_children():
                bind_scroll(child)
        bind_scroll(panel)
        self.sync_controls()

    def _repair_environment(self):
        if messagebox.askyesno("Repair PlanT environment", "Rebuild this app's managed CPU environment from verified downloads? Stop local execution first. Results and imported agents are preserved.", parent=self.app.root):
            self._start("prepare", repair=True)

    def _remove_environment(self):
        if messagebox.askyesno("Remove PlanT environment", "Remove this app's managed CPU interpreter, dependencies and cached environment downloads? Weights and recorded experiments remain. Stop local execution first.", parent=self.app.root):
            self._start("remove_environment")

    def _enable(self):
        if messagebox.askokcancel("Trust the supported PlanT model", "Enable the verified PlanT CPU pack for local experiments? This executes the supported model and its pinned Python dependencies with your user permissions. PlanT uses simulator scene data and a supplied route. It does not provide camera perception or prove driving safety.", parent=self.app.root):
            def enable():
                self.app.controller.enable_managed_model(trusted_local_code=True)
                return self.app.controller.snapshot()
            def success(snapshot):
                self.app.message.set("Managed PlanT enabled. Choose it in the Agent selector for a local run.")
                self.app._apply_snapshot(snapshot)
            self.app._run(enable, success)

    def _enable_starter(self):
        if messagebox.askokcancel("Trust the teaching starter", "Enable the included rule-based starter for local runs? It executes trusted first-party Python code using the Companion runtime and needs no model download.", parent=self.app.root):
            def enable():
                self.app.controller.enable_starter(trusted_local_code=True)
                return self.app.controller.snapshot()
            self.app._run(enable, self.app._apply_snapshot)

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
            for button in [*self.buttons, self.pause_button, self.enable_button, self.starter_button]:
                button.state(["disabled"])
            return
        value = self.app.controller.model_status()
        busy = value["busy"]
        blocked = self.app._closing or self.app._future is not None
        for button in self.buttons:
            button.state(["disabled"] if busy or blocked else ["!disabled"])
        self.pause_button.state(["!disabled"] if busy and not self.app._closing else ["disabled"])
        self.enable_button.state(["!disabled"] if value.get("inference_ready") and not busy and not blocked else ["disabled"])
        self.starter_button.state(["disabled"] if blocked else ["!disabled"])
        self.progress["maximum"] = value["total"]
        self.progress["value"] = value["bytes"]
        stage = {"unchecked": "Not checked", "weights_ready": "Weights verified", "absent": "No weights",
                 "starting": "Starting", "paused": "Paused", "downloading": "Downloading",
                 "verifying": "Verifying", "error": "Needs attention", "environment_download": "Environment download",
                 "installing": "Installing packages", "checking_environment": "Checking imports and inference", "ready": "AI ready"}[value["state"]]
        unit = (f"{value['bytes']} / {value['total']}" if value["state"] in {"installing", "checking_environment", "ready"}
                else f"{value['bytes'] / 1024**2:.1f} / {value['total'] / 1024**2:.1f} MiB")
        self.detail.set(f"{stage} · {unit}")
        self.status.set(value["message"] or ("Verifying every saved byte…" if value["state"] == "verifying" else "The transfer can be paused and resumed."))
