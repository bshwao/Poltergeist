"""First-run CARLA choices and read-only setup feedback in the native window."""

import tkinter as tk
from tkinter import ttk

from . import onboarding
from .profile import CompanionConfig


class SetupPanel:
    def __init__(self, app, parent):
        self.app = app
        self._generation = 0
        self.map_name = tk.StringVar(value="Town10HD_Opt")
        self.summary = tk.StringVar(value="Choose CARLA for live experiments, or continue with synthetic tests while you install it.")
        self.connection = tk.StringVar(value="CARLA connection has not been checked.")
        frame = ttk.Frame(parent, padding=12)
        self.frame = frame
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text="Set up this computer", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="CARLA 0.9.16 is a separate installation. Detection checks its folder; a connection check verifies the running server. No developer tools or .env edits are needed.", wraplength=750).grid(row=1, column=0, sticky="w", pady=8)
        pages = ttk.Notebook(frame)
        pages.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        installation_page = ttk.Frame(pages, padding=8)
        connection_page = ttk.Frame(pages, padding=8)
        pages.add(installation_page, text="1. Installation and hardware")
        pages.add(connection_page, text="2. Connection and maps")
        row = ttk.Frame(installation_page)
        row.pack(fill="x", pady=(0, 8))
        app._button(row, "Detect CARLA and hardware", self.scan)
        app._button(row, "Browse CARLA…", app._browse_carla)
        self.installation = ttk.Combobox(installation_page, textvariable=app.executable, state="normal")
        self.installation.pack(fill="x", pady=(0, 8))
        choices = ttk.Frame(installation_page)
        choices.pack(fill="x")
        ttk.Label(choices, text="Local RPC port").pack(side="left", padx=(0, 6))
        ttk.Entry(choices, textvariable=app.port, width=8).pack(side="left", padx=(0, 20))
        ttk.Label(choices, text="Startup graphics").pack(side="left", padx=(0, 6))
        ttk.Combobox(choices, textvariable=app.quality, values=["Low", "Epic"], state="readonly", width=9).pack(side="left")
        ttk.Checkbutton(installation_page, text="Start CARLA automatically when a live run needs it", variable=app.auto_start).pack(anchor="w", pady=10)
        map_row = ttk.Frame(connection_page)
        map_row.pack(fill="x")
        ttk.Label(map_row, text="Map for explicit startup").pack(side="left", padx=(0, 8))
        self.maps = ttk.Combobox(map_row, textvariable=self.map_name, values=["Town10HD_Opt", "Town03"], width=28)
        self.maps.pack(side="left")
        ttk.Label(connection_page, text="Town10HD_Opt is required by the collision presets. Starting a new server uses this map. An existing server keeps its map; imported recipes keep their own map requirements.", wraplength=750).pack(anchor="w", pady=8)
        native = ttk.Frame(connection_page)
        native.pack(fill="x", pady=(0, 8))
        app._button(native, "Check CARLA connection", self.check_connection)
        app._button(native, "Start / reuse CARLA", self.start_carla)
        ttk.Label(connection_page, textvariable=self.connection, wraplength=750).pack(anchor="w")
        ttk.Label(connection_page, text="Stop the local runtime before explicit CARLA startup. Checks do not tick or reload the world. Use one idle server per companion; a synchronous world may have another tick owner. Saving setup choices does not prove an experiment or model works.", wraplength=750).pack(anchor="w", pady=12)
        self.details = tk.Text(installation_page, wrap="word", height=6, state="disabled")
        self.details.pack(fill="both", expand=True, pady=10)
        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, sticky="ew")
        app._button(actions, "Save setup choices", self.save)
        app._button(actions, "Use synthetic for now", self.synthetic)
        ttk.Label(frame, textvariable=self.summary, wraplength=750).grid(row=4, column=0, sticky="w", pady=(8, 0))
        self._traces = [(variable, variable.trace_add("write", self._changed))
                        for variable in (app.executable, app.port, app.quality, app.auto_start)]

    def close(self):
        for variable, callback in self._traces:
            variable.trace_remove("write", callback)
        self._traces.clear()

    def _changed(self, *args):
        self._generation += 1
        self.connection.set("Settings changed. Check the selected server again before live work.")

    def _config(self):
        return CompanionConfig(carla_executable=self.app.executable.get().strip() or None,
            carla_port=int(self.app.port.get()), carla_quality=self.app.quality.get(),
            carla_auto_start=self.app.auto_start.get(), setup_completed=True)

    def scan(self):
        def inspect():
            return onboarding.detect_carla(), onboarding.inspect_system(self.app.controller.profile)
        def show(result):
            detection, system = result
            paths = [item["executable"] for item in detection["installations"]]
            self.installation.configure(values=paths)
            if len(paths) == 1 and not self.app.executable.get().strip():
                self.app.executable.set(paths[0])
            gb = 1024 ** 3
            memory = "unknown" if system["physical_memory_bytes"] is None else f"{system['physical_memory_bytes'] / gb:.1f} GiB"
            lines = [f"Windows / architecture: {system['platform']} / {system['machine']}",
                     f"Memory: {memory} · Logical CPUs: {system['logical_cpus']}",
                     f"Free space on data drive: {system['disk_free_bytes'] / gb:.1f} GiB",
                     f"Display adapters: {', '.join(system['display_adapters']) or 'not available'}", "",
                     "Hardware is observed, not qualified for CARLA or model performance.",
                     f"Complete CARLA folders found: {len(paths)}. Version remains unverified."]
            for rejected in detection["rejected"]:
                lines.extend(["", f"Rejected: {rejected['path']}", rejected["reason"]])
            if detection["search_limited"] or detection["inaccessible"]:
                lines.append("Some locations were not searched. Use Browse if your installation is missing.")
            if not paths:
                lines.append("Install CARLA 0.9.16 separately, then Browse to its root CarlaUE4.exe. Synthetic tests work without CARLA.")
            self.app._set_text(self.details, "\n".join(lines))
            self.summary.set("Detection finished. Choose your installation or use synthetic tests for now.")
        self.app._run(inspect, show)

    def _native(self, *, start):
        try:
            config = self._config()
        except ValueError as error:
            self.summary.set(str(error))
            return
        generation, map_name = self._generation, self.map_name.get().strip()
        def action():
            self.app._check_files()
            if self.app._close_requested.is_set():
                return None
            if start:
                if self.app.controller.snapshot()["queue"]["state"] != "stopped":
                    raise ValueError("Stop the local runtime before explicit CARLA startup")
                return onboarding.start_carla(self.app.controller.profile, config, map_name,
                    cancellation=self.app._close_requested.is_set)
            return onboarding.check_connection(config)
        def show(result):
            if result is None:
                return
            if generation != self._generation:
                self.connection.set("The check used earlier settings. Check the selected server again.")
                return
            names = [name.rsplit("/", 1)[-1] for name in result["available_maps"]]
            self.maps.configure(values=names)
            synchronous = " World is synchronous; stop its other tick owner before running experiments." if result["synchronous_mode"] else ""
            self.connection.set(f"CARLA {result['server_version']} verified on port {config.carla_port}. Current map: {result['map']}.{synchronous}")
            self.summary.set("Connection verified. No simulation or model was executed by this check.")
        self.app._run(action, show)

    def check_connection(self):
        self._native(start=False)

    def start_carla(self):
        self._native(start=True)

    def save(self):
        try:
            config = self._config()
        except ValueError as error:
            self.summary.set(str(error))
            return
        self.app._run(lambda: self.app.controller.configure(config), self._saved)

    def synthetic(self):
        config = self.app.controller.config.model_copy(update={"setup_completed": True})
        def saved(value):
            self.summary.set("Synthetic setup saved. CARLA choices were not changed; use Save setup choices to apply them later.")
            self.app.message.set("Synthetic setup saved. Start the local runtime and run the synthetic installation check.")
        self.app._run(lambda: self.app.controller.configure(config), saved)

    def _saved(self, value):
        self.summary.set("Setup choices saved. Start the local runtime, then explicitly choose Synthetic test or CARLA to run an experiment.")
        self.app.message.set("Setup choices saved; execution remains untested until a run completes.")
