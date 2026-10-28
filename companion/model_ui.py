"""Catalogue preparation controls; Tk only consumes thread-safe progress values."""

import tkinter as tk
from tkinter import messagebox, ttk

from .model_assets import PLANT_WEIGHTS


class ModelPanel:
    def __init__(self, app, parent):
        self.app = app
        self.frame = ttk.Frame(parent, padding=12)
        self._canvas = tk.Canvas(self.frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(self._canvas)
        content = self._canvas.create_window(0, 0, anchor="nw", window=body)
        body.bind("<Configure>", lambda _: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda event: self._canvas.itemconfigure(content, width=event.width))
        self._wheel_binding = app.root.bind("<MouseWheel>", self._scroll, add="+")
        ttk.Label(body, text="PlanT 2.0 reference AI", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(body, text="Prepare the official weights for the supported reference agent. The baseline controller already works without a model download.", wraplength=750).pack(anchor="w", pady=8)
        ttk.Label(body, text=f"Download: {PLANT_WEIGHTS.size / 1024**2:.1f} MiB · Model: Apache-2.0 · Adapted code: MIT\nSource: huggingface.co/SimonGer/PlanT2 · revision {PLANT_WEIGHTS.id.removeprefix('plant2-')}", wraplength=750).pack(anchor="w")
        ttk.Label(body, text="PlanT uses privileged simulator scene data and a provided route. It is not a camera perception model. The official checkpoint uses Python pickle; only the pinned, verified release may be loaded by the reference agent.", wraplength=750).pack(anchor="w", pady=8)
        self.status = tk.StringVar(value="Saved weights have not been checked in this session.")
        self.progress = ttk.Progressbar(body, maximum=PLANT_WEIGHTS.size)
        self.progress.pack(fill="x", pady=8)
        self.detail = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.detail).pack(anchor="w")
        ttk.Label(body, textvariable=self.status, wraplength=750).pack(anchor="w", pady=8)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=8)
        self.buttons = []
        for label, action in (("Download / resume", lambda: self._start("download")),
                              ("Check saved weights", lambda: self._start("inspect")),
                              ("Repair weights…", self._repair), ("Remove weights…", self._remove)):
            button = ttk.Button(actions, text=label, command=action)
            button.pack(side="left", padx=(0, 8))
            self.buttons.append(button)
        self.pause_button = ttk.Button(body, text="Pause download / check", command=app.controller.pause_model_download)
        self.pause_button.pack(anchor="w")
        ttk.Label(body, text="PlanT CPU environment", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(16, 6))
        ttk.Label(body, text="Install/check runs the app's pinned model code locally in its own Python environment. It checks two synthetic scenes. Recipe execution needs CARLA. Stop the local runtime before environment preparation.", wraplength=750).pack(anchor="w")
        self.environment_status = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.environment_status, wraplength=750).pack(anchor="w", pady=6)
        self.environment_progress = ttk.Progressbar(body, maximum=1)
        self.environment_progress.pack(fill="x", pady=6)
        self.environment_detail = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.environment_detail, wraplength=750).pack(anchor="w")
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=8)
        self.environment_buttons = []
        for index, (label, action) in enumerate((("Install and check CPU", lambda: self._start("install_environment")),
                              ("Check environment", lambda: self._start("inspect_environment")),
                              ("Repair environment…", self._repair_environment),
                              ("Remove environment…", self._remove_environment))):
            button = ttk.Button(actions, text=label, command=action)
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 8), pady=(0, 6))
            self.environment_buttons.append(button)
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        self.use_button = ttk.Button(body, text="Use PlanT CPU", command=self._use)
        self.use_button.pack(anchor="w", pady=6)
        ttk.Label(body, text="After model checks pass, start the local runtime and choose Use PlanT CPU to add it to the Agent selector. Environment removal keeps weights and results; removing either requires saved model jobs to finish or be cancelled.", wraplength=750).pack(anchor="w", pady=6)
        self.sync_controls()

    def _scroll(self, event):
        if str(event.widget).startswith(str(self.frame) + "."):
            self._canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def close(self):
        self.app.root.unbind("<MouseWheel>", self._wheel_binding)

    def _start(self, action, *, repair=False):
        def start():
            self.app._check_files()
            if not self.app._close_requested.is_set():
                self.app.controller.prepare_model(action, repair=repair,
                    trusted_local_code=action in {"install_environment", "inspect_environment"})
        self.app._run(start)

    def _repair(self):
        if messagebox.askyesno("Repair PlanT weights", "Repair verifies completed weights first. If damaged, it removes only this app's managed weights and starts a fresh download. Continue?", parent=self.app.root):
            self._start("download", repair=True)

    def _remove(self):
        if messagebox.askyesno("Remove PlanT weights", "Remove this app's managed PlanT weights and partial download? Imported models and simulation evidence are kept.", parent=self.app.root):
            self._start("remove")

    def _repair_environment(self):
        if messagebox.askyesno("Repair PlanT environment", "Build a fresh verified CPU environment and check the model? Existing results and model weights are kept.", parent=self.app.root):
            self._start("install_environment", repair=True)

    def _remove_environment(self):
        if messagebox.askyesno("Remove PlanT environment", "Remove this app's managed CPU environment? Existing results and model weights are kept.", parent=self.app.root):
            self._start("remove_environment")

    def _use(self):
        def selected(result):
            self.app._apply_snapshot(result["snapshot"])
            label = next(name for name, identifier in self.app._agent_ids.items() if identifier == result["agent_id"])
            self.app.agent.set(label)
            self.app.mode.set("CARLA")
            self.app.message.set("PlanT CPU selected. Choose a recipe and run with CARLA.")
        self.app._run(lambda: self.app.controller.use_managed_model(trusted_local_code=True), selected)

    def sync_controls(self):
        if self.app._closing:
            # The background close can release profile ownership just before
            # its future is collected. Do not re-enter the closed controller.
            for button in [*self.buttons, self.pause_button, *self.environment_buttons, self.use_button]:
                button.state(["disabled"])
            return
        value = self.app.controller.model_status()
        busy = value["busy"]
        blocked = self.app._closing or self.app._future is not None
        if not blocked:
            self.app.start_button.state(["disabled"] if value["execution_blocked"] else ["!disabled"])
        for button in self.buttons:
            button.state(["disabled"] if busy or blocked else ["!disabled"])
        self.pause_button.state(["!disabled"] if busy and not self.app._closing else ["disabled"])
        self.progress["value"] = value["bytes"]
        stage = {"unchecked": "Not checked", "weights_ready": "Weights verified", "absent": "No weights",
                 "starting": "Starting", "paused": "Paused", "downloading": "Downloading",
                 "verifying": "Verifying", "error": "Needs attention"}[value["state"]]
        self.detail.set(f"{stage} · {value['bytes'] / 1024**2:.1f} / {value['total'] / 1024**2:.1f} MiB")
        self.status.set(value["message"] or ("Verifying every saved byte…" if value["state"] == "verifying" else "The transfer can be paused and resumed."))
        environment = value["environment"]
        self.environment_status.set(environment["message"] or {
            "loading_model": "Loading the CPU model in its isolated worker…",
            "checking_inference": "Checking model inference with two synthetic scenes…",
            "checking_checkpoint": "Verifying the official model weights…",
        }.get(environment["state"], "Verifying the environment and its files…"))
        detail = {"ready": "CPU check passed · CARLA execution remains separate",
            "unavailable": "No model environment included in this build", "unchecked": "Environment not checked",
            "absent": "Environment not installed", "paused": "Preparation paused", "error": "Needs attention",
            "stopping": "Waiting for the model process to stop"}.get(environment["state"], "Preparing CPU environment")
        if environment["state"] in {"verifying_environment", "installing_environment", "verifying_capsule_contents", "verifying_capsule"}:
            detail += f" · {environment['progress'] / 1024**2:.1f} / {environment['total'] / 1024**2:.1f} MiB"
        self.environment_detail.set(detail)
        self.environment_progress.configure(maximum=max(1, environment["total"]), value=environment["progress"])
        queue = (self.app._last_snapshot or {}).get("queue", {})
        stopped = queue.get("state") == "stopped"
        available = environment["state"] != "unavailable"
        for index, button in enumerate(self.environment_buttons):
            enabled = available and not busy and not blocked and stopped and (index == 3 or value["state"] == "weights_ready")
            button.state(["!disabled"] if enabled else ["disabled"])
        use = not busy and not blocked and environment["state"] == "ready" and queue.get("state") == "running"
        self.use_button.state(["!disabled"] if use else ["disabled"])
