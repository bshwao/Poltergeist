"""Catalogue preparation controls; Tk only consumes thread-safe progress values."""

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .model_assets import PLANT_WEIGHTS
from .model_catalog import model_options
from .scrolling import ScrollPanel


class ModelPanel:
    def __init__(self, app, parent):
        self.app = app
        self._scroll = ScrollPanel(parent)
        self.frame, self.canvas = self._scroll.frame, self._scroll.canvas
        panel = self._scroll.content
        self._model_options = model_options()
        ttk.Label(panel, text="Supported AI model").pack(anchor="w")
        self.model_choice = tk.StringVar(value=self._model_options[0]["name"])
        self.model_selector = ttk.Combobox(panel, textvariable=self.model_choice,
            values=[value["name"] for value in self._model_options], state="readonly", width=38)
        self.model_selector.pack(anchor="w", pady=6)
        self.model_selector.bind("<<ComboboxSelected>>", lambda event: self._select_model())
        self.model_title = tk.StringVar(value=self._model_options[0]["name"])
        self.model_source = tk.StringVar(value="")
        self.model_description = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.model_title, font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(panel, text="Prepare a supported driving model, check its readiness, then enable it for local experiments. The baseline controller is available without a download.", wraplength=750).pack(anchor="w", pady=8)
        ttk.Label(panel, textvariable=self.model_source, wraplength=750).pack(anchor="w")
        ttk.Label(panel, textvariable=self.model_description, wraplength=750).pack(anchor="w", pady=8)
        self.guidance = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.guidance, wraplength=750).pack(anchor="w", pady=8)
        self.status = tk.StringVar(value="Saved weights have not been checked in this session.")
        self.progress = ttk.Progressbar(panel, maximum=PLANT_WEIGHTS.size)
        self.progress.pack(fill="x", pady=8)
        self.detail = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.detail, wraplength=750).pack(anchor="w")
        ttk.Label(panel, textvariable=self.status, wraplength=750).pack(anchor="w", pady=8)
        actions = ttk.LabelFrame(panel, text="Model weights", padding=8)
        actions.pack(fill="x", pady=8)
        self.buttons = []
        self._requires_stopped = []
        for label, action in (("Download / resume", lambda: self._start("download")),
                              ("Check saved weights", lambda: self._start("inspect")),
                              ("Repair weights…", self._repair), ("Remove weights…", self._remove)):
            button = ttk.Button(actions, text=label, command=action)
            index = len(self.buttons)
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=4)
            self.buttons.append(button)
            if index >= 2:
                self._requires_stopped.append(button)
        for column in range(2):
            actions.columnconfigure(column, weight=1, uniform="weights")
        self.pause_button = ttk.Button(panel, text="Pause download / check", command=app.controller.pause_model_download)
        self.pause_button.pack(anchor="w")
        provider = ttk.LabelFrame(panel, text="Model source and optional Hugging Face account", padding=8)
        provider.pack(fill="x", pady=10)
        self.source_status = tk.StringVar(value="Public downloads work without an account.")
        ttk.Label(provider, textvariable=self.source_status, wraplength=730).pack(anchor="w")
        source_actions = ttk.Frame(provider)
        source_actions.pack(fill="x", pady=6)
        for index, (label, action) in enumerate((("Check pinned source", lambda: self._start("check_source")),
                              ("Configure account…", self._configure_account),
                              ("Forget account", lambda: app._run(app.controller.clear_huggingface)))):
            button = ttk.Button(source_actions, text=label, command=action)
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=4)
            self.buttons.append(button)
            if label == "Check pinned source":
                self.source_check_button = button
        for column in range(2):
            source_actions.columnconfigure(column, weight=1, uniform="source")
        ttk.Label(panel, text="Windows x64 CPU pack · separate Python 3.12 runtime · about 296 MiB of environment downloads plus weights · allow 3 GiB free space. CPU inference can run slower than simulation time. Stop the local runtime before preparing or checking AI.", wraplength=750).pack(anchor="w", pady=12)
        environment_actions = ttk.LabelFrame(panel, text="AI environment and inference", padding=8)
        environment_actions.pack(fill="x", pady=8)
        for index, (label, action) in enumerate((("Install / resume AI", lambda: self._start("prepare")),
                              ("Check AI readiness", lambda: self._start("check_environment")),
                              ("Repair AI…", self._repair_environment),
                              ("Remove AI…", self._remove_environment))):
            button = ttk.Button(environment_actions, text=label, command=action)
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=4)
            self.buttons.append(button)
            self._requires_stopped.append(button)
        for column in range(2):
            environment_actions.columnconfigure(column, weight=1, uniform="environment")
        self.enable_button = ttk.Button(panel, text="Enable selected AI for local runs…", command=self._enable)
        self.enable_button.pack(anchor="w", pady=8)
        self.starter_button = ttk.Button(panel, text="Enable teaching starter (no download)…", command=self._enable_starter)
        self.starter_button.pack(anchor="w", pady=4)
        ttk.Label(panel, text="Enable only after import and real pretrained inference checks pass. Enabling registers trusted local model code and starts the local runtime; then choose the enabled model in the Agent selector. Model and dependency notices are retained in the managed environment's notices and site-packages folders. Hosted execution keeps its existing policy.", wraplength=750).pack(anchor="w", pady=8)
        self._scroll.bind_content()
        self.sync_controls()

    def _repair_environment(self):
        if messagebox.askyesno("Repair selected AI environment", "Rebuild this app's selected managed CPU environment from verified downloads? Stop local execution first. Results and imported agents are preserved.", parent=self.app.root):
            self._start("prepare", repair=True)

    def _remove_environment(self):
        if messagebox.askyesno("Remove selected AI environment", "Remove this app's selected managed CPU interpreter, dependencies and cached environment downloads? Weights and recorded experiments remain. Stop local execution first.", parent=self.app.root):
            self._start("remove_environment")

    def _select_model(self):
        selected = next(value for value in self._model_options if value["name"] == self.model_choice.get())
        self.app._run(lambda: self.app.controller.select_model(selected["id"]))

    def _enable(self):
        model = self.app.controller.model_status().get("selected_model", self._model_options[0])
        if messagebox.askokcancel("Trust the supported model", f"Enable {model['name']} for local experiments? This executes the supported model and pinned Python dependencies with your user permissions. {model['description']} This does not establish driving safety.", parent=self.app.root):
            def enable():
                self.app.controller.enable_managed_model(trusted_local_code=True)
                return self.app.controller.snapshot()
            def success(snapshot):
                self.app.message.set(f"{model['name']} enabled. Choose it in the Agent selector for a local run.")
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

    def _configure_account(self):
        token = simpledialog.askstring("Hugging Face account",
            "Optional fine-grained read token. Public models work without one. Saved tokens are protected for your Windows account.",
            show="*", parent=self.app.root)
        if token is not None:
            self.app._run(lambda: self.app.controller.configure_huggingface(token))

    def _repair(self):
        if messagebox.askyesno("Repair selected AI weights", "Repair verifies completed weights first. If damaged, it removes only this app's selected managed weights and starts a fresh download. Continue?", parent=self.app.root):
            self._start("download", repair=True)

    def _remove(self):
        if messagebox.askyesno("Remove selected AI weights", "Remove this app's selected managed weights and partial download? Imported models and simulation evidence are kept.", parent=self.app.root):
            self._start("remove")

    def sync_controls(self):
        if self.app._closing:
            # The background close can release profile ownership just before
            # its future is collected. Do not re-enter the closed controller.
            for button in [*self.buttons, self.pause_button, self.enable_button, self.starter_button]:
                button.state(["disabled"])
            self.model_selector.state(["disabled"])
            return
        value = self.app.controller.model_status()
        model = value.get("selected_model", self._model_options[0])
        self.model_choice.set(model["name"])
        self.model_title.set(model["name"])
        self.model_source.set(f"Download: {model['size'] / 1024**2:.1f} MiB · {model['source']}")
        self.model_description.set(model["description"] + " " + model["notice"])
        account_info = value.get("account", {})
        account = account_info.get("message") or ("Configured account" if account_info.get("configured") else "Anonymous public access")
        source = value.get("selection_error") or value.get("source", {}).get("message", "Pinned source not checked.")
        self.source_status.set(f"{account} · {source}")
        busy = value["busy"]
        blocked = self.app._closing or self.app._future is not None
        runtime_running = value.get("runtime_running", False)
        self.model_selector.state(["disabled"] if busy or blocked or runtime_running else ["!disabled", "readonly"])
        for button in self.buttons:
            button.state(["disabled"] if busy or blocked or value.get("selection_error") else ["!disabled"])
        if runtime_running:
            for button in self._requires_stopped:
                button.state(["disabled"])
        self.source_check_button.state(["disabled"] if busy or blocked or value.get("selection_error") or not model["huggingface"] else ["!disabled"])
        self.pause_button.state(["!disabled"] if busy and not self.app._closing else ["disabled"])
        self.enable_button.state(["!disabled"] if value.get("inference_ready") and not busy and not blocked else ["disabled"])
        self.starter_button.state(["disabled"] if blocked else ["!disabled"])
        self.guidance.set(
            "Stop the local runtime to switch models or install, check, repair or remove AI. Source checks and weight downloads remain available."
            if runtime_running else
            "Ready to enable. Choose Enable selected AI for local runs, then select this model in Local execution."
            if value.get("inference_ready") else
            "Wait for the current action to finish, or pause it before changing models."
            if busy else
            "Next: Install / resume AI prepares weights and checks inference. If already installed, choose Check AI readiness.")
        self.progress["maximum"] = value["total"]
        self.progress["value"] = value["bytes"]
        stage = {"unchecked": "Not checked", "weights_ready": "Weights verified", "absent": "No weights",
                 "starting": "Starting", "paused": "Paused", "downloading": "Downloading",
                 "verifying": "Verifying", "error": "Needs attention", "environment_download": "Environment download",
                 "installing": "Installing packages", "checking_environment": "Checking environment and inference", "ready": "AI ready",
                 "environment_removed": "AI environment removed"}[value["state"]]
        unit = ("" if value["state"] in {"unchecked", "starting", "absent", "environment_removed", "ready"} else
                f"{value['bytes']} / {value['total']} items" if value["state"] in {"installing", "checking_environment"}
                else f"{value['bytes'] / 1024**2:.1f} / {value['total'] / 1024**2:.1f} MiB")
        self.detail.set(f"{stage} · {unit}" if unit else stage)
        self.status.set(value["message"] or ("Verifying every saved byte…" if value["state"] == "verifying" else "The transfer can be paused and resumed."))
