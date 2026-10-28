"""Small native desktop surface. Tk stays on the main thread; I/O does not."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from scenarios import validate_scenario
from scenarios.stress_presets import STRESS_PRESETS

from .controller import read_json
from .profile import CompanionConfig


def installation_recipe():
    return validate_scenario({"name": "Companion synthetic installation check",
        "description": "Synthetic physics only; no CARLA or driving-safety conclusion.",
        "duration_s": .5, "seed": 7, "map": "Town03", "fixed_delta_seconds": .05,
        "actors": [{"id": "ego", "type": "ego_vehicle", "speed_mps": 2., "spawn_point_index": 0},
                   {"id": "lead", "type": "vehicle", "speed_mps": 0., "spawn": {"position": {"x": 35.}}}]})


class CompanionWindow:
    def __init__(self, root, controller, logger, *, autostart=True, startup_check=None):
        self.root, self.controller, self.logger = root, controller, logger
        self._wrap_width = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="companion-actions")
        self._future = None
        self._success = None
        self._closing = False
        self._close_requested = Event()
        self._startup_check = startup_check
        self._files_checked = startup_check is None
        self._destroyed = False
        self._last_snapshot = None
        self._poll_count = 0
        self._buttons = []
        self._recipe = installation_recipe()
        self._agent_ids = {"Baseline controller": None}
        self._last_diagnostics = None
        self._imports = {}
        root.title("Poltergeist Companion — private prototype")
        root.geometry("860x740")
        root.minsize(700, 600)
        root.protocol("WM_DELETE_WINDOW", self.request_close)
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Poltergeist Companion", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Private local prototype · Windows x64", foreground="#555555").pack(anchor="w", pady=(2, 10))
        self.runtime_text = tk.StringVar(value="Local runtime: starting…" if autostart else "Local runtime: stopped")
        ttk.Label(outer, textvariable=self.runtime_text, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Private account connection preview. Remote requests need pairing, local consent and an explicit connection.", wraplength=790).pack(anchor="w", pady=(4, 8))
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 10))
        self.start_button = self._button(toolbar, "Start local runtime", lambda: self._run(self._start_runtime, self._apply_snapshot))
        self.stop_button = self._button(toolbar, "Stop local runtime", self._stop)
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        execution = ttk.Frame(tabs, padding=12)
        settings = ttk.Frame(tabs, padding=12)
        diagnostics = ttk.Frame(tabs, padding=12)
        archives = ttk.Frame(tabs, padding=12)
        for frame, label in ((execution, "Local execution"), (settings, "Settings"),
                             (diagnostics, "Diagnostics"), (archives, "Imported reports")):
            tabs.add(frame, text=label)
        self._build_execution(execution)
        self._build_settings(settings)
        from .model_ui import ModelPanel
        self.models = ModelPanel(self, tabs)
        tabs.add(self.models.frame, text="AI models")
        self._tabs = tabs
        from .pairing_ui import PairingPanel
        self.pairing = PairingPanel(self, tabs)
        tabs.add(self.pairing.frame, text="Connection")
        from .setup_ui import SetupPanel
        self.setup = SetupPanel(self, tabs)
        tabs.insert(0, self.setup.frame, text="Setup")
        if not controller.config.setup_completed:
            tabs.select(self.setup.frame)
        self._build_diagnostics(diagnostics)
        self._build_archives(archives)
        self.message = tk.StringVar(value="Results and logs stay in your companion profile.")
        ttk.Label(outer, textvariable=self.message, wraplength=790).pack(anchor="w", pady=(12, 3))
        ttk.Label(outer, text=f"Data: {controller.profile}", wraplength=790, foreground="#555555").pack(anchor="w")
        root.bind("<Configure>", self._resize_text, add="+")
        root.after(50, self._pump)
        if autostart:
            if startup_check:
                self.runtime_text.set("Local runtime: checking installed files…")
            self._run(self._start_runtime, self._apply_snapshot)
        elif not controller.config.setup_completed:
            root.after(100, self.setup.scan)

    def _check_files(self):
        if not self._files_checked:
            self._startup_check()
            self._files_checked = True

    def _start_runtime(self):
        self._check_files()
        if self._close_requested.is_set():
            return self.controller.snapshot()
        return self.controller.start()

    def _button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command)
        button.pack(side="left", padx=(0, 8))
        self._buttons.append(button)
        return button

    def _build_execution(self, frame):
        ttk.Label(frame, text="Run an existing recipe locally", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Scenario editing and detailed analysis remain in the dashboard. Import a scenario JSON or use a bundled check.", wraplength=750).pack(anchor="w", pady=(5, 10))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.preset = ttk.Combobox(row, state="readonly", width=45,
            values=["Synthetic installation check", *[p.id for p in STRESS_PRESETS]])
        self.preset.current(0)
        self.preset.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._button(row, "Load preset", self._load_preset)
        self._button(row, "Import scenario…", self._import_scenario)
        self.recipe_text = tk.StringVar()
        ttk.Label(frame, textvariable=self.recipe_text, wraplength=750).pack(anchor="w", pady=8)
        self._describe_recipe()
        choices = ttk.Frame(frame)
        choices.pack(fill="x", pady=(0, 8))
        ttk.Label(choices, text="Execution").pack(side="left", padx=(0, 6))
        self.mode = ttk.Combobox(choices, state="readonly", values=["Synthetic test", "CARLA"], width=18)
        self.mode.current(0)
        self.mode.pack(side="left", padx=(0, 16))
        ttk.Label(choices, text="Agent").pack(side="left", padx=(0, 6))
        self.agent = ttk.Combobox(choices, state="readonly", values=list(self._agent_ids), width=30)
        self.agent.current(0)
        self.agent.pack(side="left", fill="x", expand=True)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(0, 10))
        self.run_button = self._button(actions, "Run recipe", self._submit)
        self._button(actions, "Register trusted agent…", self._register_agent)
        self._button(actions, "Manage AI…", lambda: self._tabs.select(self.models.frame))
        ttk.Label(frame, text="Recent local runs (latest 100)").pack(anchor="w")
        columns = ("name", "mode", "status", "progress")
        self.runs = ttk.Treeview(frame, columns=columns, show="headings", height=7, selectmode="browse")
        for name, label, width in (("name", "Scenario", 300), ("mode", "Mode", 70),
                                    ("status", "Status", 95), ("progress", "Progress", 70)):
            self.runs.heading(name, text=label)
            self.runs.column(name, width=width, minwidth=55, stretch=name == "name")
        self.runs.pack(side="top", fill="both", expand=True)
        # Tree wheel/keyboard scrolling remains available with compact layouts.
        self.runs.bind("<<TreeviewSelect>>", self._show_run)
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(8, 0))
        self._button(controls, "Cancel selected", self._cancel)
        self._button(controls, "Export HTML", lambda: self._export("html"))
        self._button(controls, "Export JSON", lambda: self._export("json"))

    def _build_settings(self, frame):
        ttk.Label(frame, text="CARLA configuration", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(frame, text="Stop the local runtime before saving. CARLA stays open when the runtime stops.", wraplength=710).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 16))
        config = self.controller.config
        self.executable = tk.StringVar(value=config.carla_executable or "")
        self.port = tk.StringVar(value=str(config.carla_port))
        self.quality = tk.StringVar(value=config.carla_quality)
        self.auto_start = tk.BooleanVar(value=config.carla_auto_start)
        ttk.Label(frame, text="CARLA executable").grid(row=2, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(frame, textvariable=self.executable).grid(row=2, column=1, sticky="ew")
        browse = ttk.Button(frame, text="Browse…", command=self._browse_carla)
        browse.grid(row=2, column=2, padx=(8, 0))
        self._buttons.append(browse)
        ttk.Label(frame, text="Local RPC port").grid(row=3, column=0, sticky="w", pady=14)
        ttk.Entry(frame, textvariable=self.port, width=10).grid(row=3, column=1, sticky="w")
        ttk.Label(frame, text="Startup graphics").grid(row=4, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=self.quality, values=["Low", "Epic"], state="readonly", width=10).grid(row=4, column=1, sticky="w")
        ttk.Checkbutton(frame, text="Start CARLA when a live run needs it", variable=self.auto_start).grid(row=5, column=0, columnspan=3, sticky="w", pady=16)
        row = ttk.Frame(frame)
        row.grid(row=6, column=0, columnspan=3, sticky="w")
        self._button(row, "Save settings", self._save_settings)
        ttk.Label(frame, text="Choose the root CarlaUE4.exe in the complete CARLA 0.9.16 folder. Saving checks file layout; use Diagnostics to verify the server version. An existing server keeps its current graphics setting.", wraplength=710).grid(row=7, column=0, columnspan=3, sticky="w", pady=16)
        frame.columnconfigure(1, weight=1)

    def _build_diagnostics(self, frame):
        ttk.Label(frame, text="Local checks", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Checks do not start CARLA or run an experiment. RPC checks inspect the configured local server. Logs are in the profile’s logs folder.", wraplength=750).pack(anchor="w", pady=8)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 8))
        self._button(row, "Check prerequisites", lambda: self._diagnostics(False))
        self._button(row, "Check CARLA RPC", lambda: self._diagnostics(True))
        self._button(row, "Save diagnostics", self._save_diagnostics)
        self.diagnostic_text = tk.Text(frame, wrap="word", height=18, font=("Consolas", 10), state="disabled")
        self.diagnostic_text.pack(fill="both", expand=True)

    def _build_archives(self, frame):
        ttk.Label(frame, text="Import existing evidence", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Export a finalized JSON run report from your existing dashboard, then import it here. The report is archived separately; its jobs, code registrations and raw recordings are not imported. Your original database stays in place.", wraplength=750).pack(anchor="w", pady=8)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 10))
        self._button(row, "Import run report…", self._import_report)
        self._button(row, "Refresh list", self._refresh_imports)
        self.archive_list = tk.Listbox(frame, height=7)
        self.archive_list.pack(fill="x")
        self.archive_list.bind("<<ListboxSelect>>", self._show_archive)
        self.archive_text = tk.Text(frame, wrap="word", height=12, state="disabled")
        self.archive_text.pack(fill="both", expand=True, pady=(8, 0))
        self._refresh_imports()

    def _resize_text(self, event):
        if event.widget is not self.root:
            return
        width = max(250, event.width - 85)
        if width == self._wrap_width:
            return
        self._wrap_width = width
        def visit(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Label) and int(child.cget("wraplength") or 0) > 0:
                    child.configure(wraplength=width)
                visit(child)
        visit(self.root)

    def _run(self, action, success=None):
        if self._future is not None or self._destroyed:
            return
        for button in self._buttons:
            button.state(["disabled"])
        self._success = success
        self._future = self._executor.submit(action)
        self.pairing.sync_controls()
        self.models.sync_controls()

    def _refresh_controls(self):
        busy = self._future is not None or self._closing
        for button in self._buttons:
            button.state(["disabled"] if busy else ["!disabled"])
        self.pairing.sync_controls()
        self.models.sync_controls()

    def _pump(self):
        if self._destroyed:
            return
        self.models.sync_controls()
        if self._future is not None and self._future.done():
            future, success = self._future, self._success
            self._future = self._success = None
            failed = False
            try:
                value = future.result()
                if success:
                    success(value)
            except Exception as error:
                failed = True
                self.logger.exception("Companion action failed")
                self.message.set(str(error))
                if not self._closing:
                    messagebox.showerror("Poltergeist Companion", str(error), parent=self.root)
                else:
                    self._closing = False
                    self._close_requested.clear()
                    messagebox.showerror("Could not close safely", str(error), parent=self.root)
            if self._destroyed:
                return
            refresh_connection = self.pairing.action_finished(failed=failed)
            self._refresh_controls()
            if self._closing:
                self._begin_close()
            elif refresh_connection:
                # A failed connection action can retain a partially started
                # relay. Read its ownership before offering another Connect.
                self._run(self.controller.snapshot, self._apply_snapshot)
        self._poll_count += 1
        if self._future is None and not self._closing and self._poll_count % 20 == 0:
            self._run(self.controller.snapshot, self._apply_snapshot)
        self.root.after(50, self._pump)

    def _apply_snapshot(self, snapshot):
        self._last_snapshot = snapshot
        self.pairing.show(snapshot.get("pairing", {"state": "unconfigured"}))
        queue = snapshot["queue"]
        self.pairing.show_connection(snapshot["relay"], queue)
        active = " · one active run" if queue["active_id"] else ""
        self.runtime_text.set(f"Local runtime: {queue['state']}{active} · {queue['pending']} waiting")
        if queue["error"]:
            self.message.set(queue["error"])
        wanted = {run["id"] for run in snapshot["runs"]}
        for existing in self.runs.get_children():
            if existing not in wanted:
                self.runs.delete(existing)
        for index, run in enumerate(snapshot["runs"]):
            values = (run["name"], "Synthetic" if run["mode"] == "test" else "CARLA", run["status"], f"{run['progress']:.0%}")
            if self.runs.exists(run["id"]):
                self.runs.item(run["id"], values=values)
                self.runs.move(run["id"], "", index)
            else:
                self.runs.insert("", index, iid=run["id"], values=values)
        old = self.agent.get()
        self._agent_ids = {"Baseline controller": None, **{f"{a['name']} · {a['id'][:8]}": a["id"] for a in snapshot["agents"]}}
        self.agent.configure(values=list(self._agent_ids))
        self.agent.set(old if old in self._agent_ids else "Baseline controller")

    def _stop(self):
        self.pairing.reset_consent()
        def stop():
            clean = self.controller.stop()
            return clean, self.controller.snapshot()
        def stopped(result):
            clean, snapshot = result
            self.message.set("Local runtime stopped. Waiting jobs remain saved; an active run was asked to cancel." if clean else "A worker is still stopping. Keep this window open and retry Stop; the profile remains locked.")
            self._apply_snapshot(snapshot)
        self._run(stop, stopped)

    def request_close(self):
        if self._closing:
            return
        self._closing = True
        self.pairing.reset_consent()
        self._close_requested.set()
        self.message.set("Waiting for the local worker to stop safely…")
        if self._future is None:
            self._begin_close()

    def _begin_close(self):
        def close():
            clean = self.controller.close()
            return clean, None if clean else self.controller.snapshot()
        def closed(result):
            clean, snapshot = result
            self._closing = False
            if not clean:
                self._close_requested.clear()
                self.message.set("Local work is still stopping. Keep this window open and retry Close after it returns. Its profile remains locked.")
                self._apply_snapshot(snapshot)
                return
            self._destroyed = True
            # The only submitted action is already done. Join its idle thread
            # before Tcl objects can be destroyed/finalized on another thread.
            self._executor.shutdown(wait=True)
            self.setup.close()
            self.pairing.close()
            self.models.close()
            # Python's cyclic GC can run on any worker thread. Release our Tk
            # variables here, on the GUI thread, before a closed window's cycles
            # can be collected later while another window/worker is running.
            self._release_variables(self.setup)
            self._release_variables(self.pairing)
            self._release_variables(self.models)
            self._release_variables(self)
            self.root.destroy()
        self._run(close, closed)

    @staticmethod
    def _release_variables(owner):
        for name in list(vars(owner)):
            value = getattr(owner, name)
            if isinstance(value, tk.Variable):
                setattr(owner, name, None)

    def _describe_recipe(self):
        recipe = self._recipe
        self.recipe_text.set(f"{recipe.name} · {recipe.map} · seed {recipe.seed} · {recipe.duration_s:g} s\n{recipe.description or ''}")

    def _load_preset(self):
        selected = self.preset.get()
        self._recipe = installation_recipe() if selected == "Synthetic installation check" else next(p for p in STRESS_PRESETS if p.id == selected).scenario()
        self._describe_recipe()

    def _import_scenario(self):
        path = filedialog.askopenfilename(parent=self.root, title="Import scenario JSON", filetypes=[("JSON", "*.json")])
        if path:
            def loaded(recipe):
                self._recipe = recipe
                self._describe_recipe()
                self.message.set("Recipe imported. Choose the execution mode, then Run recipe.")
            self._run(lambda: validate_scenario(read_json(path)), loaded)

    def _submit(self):
        mode = "carla" if self.mode.get() == "CARLA" else "test"
        recipe, agent_id = self._recipe, self._agent_ids[self.agent.get()]
        self._run(lambda: self.controller.submit(recipe, mode=mode, agent_id=agent_id),
                  lambda stored: self.message.set(f"Accepted local run {stored.simulation.id}"))

    def _selected(self):
        selection = self.runs.selection()
        if not selection:
            self.message.set("Select a local run first.")
            return None
        return selection[0]

    def _show_run(self, event=None):
        selected = self.runs.selection()
        if selected and self._last_snapshot:
            run = next((r for r in self._last_snapshot["runs"] if r["id"] == selected[0]), None)
            if run:
                self.message.set(run["error"] or f"Run {run['id']} · {run['status']} · {run['progress']:.0%}")

    def _cancel(self):
        selected = self._selected()
        if selected:
            self._run(lambda: self.controller.cancel(selected), lambda stored: self.message.set(f"Cancellation requested; current status: {stored.simulation.status.value}."))

    def _export(self, format):
        selected = self._selected()
        if selected:
            self._run(lambda: self.controller.export(selected, format=format), lambda path: self.message.set(f"Export saved: {path}"))

    def _register_agent(self):
        path = filedialog.askdirectory(parent=self.root, title="Select a trusted agent folder containing agent.json")
        if path and messagebox.askokcancel("Trust this local Python agent", f"Register {path}? Selecting this agent for a run executes its Python code with your user permissions, using the companion interpreter. Dependencies must already be available. Only approve code you trust.", parent=self.root):
            self._run(lambda: self.controller.register_agent(path, trusted_local_code=True),
                lambda agent: self.message.set(f"Registered {agent.snapshot.manifest.name}. Static inspection does not verify inference."))

    def _browse_carla(self):
        path = filedialog.askopenfilename(parent=self.root, title="Select the complete CARLA installation’s root CarlaUE4.exe", filetypes=[("CARLA launcher", "CarlaUE4.exe")])
        if path:
            self.executable.set(path)

    def _save_settings(self):
        try:
            config = CompanionConfig(carla_executable=self.executable.get().strip() or None,
                carla_port=int(self.port.get()), carla_quality=self.quality.get(), carla_auto_start=self.auto_start.get(),
                setup_completed=self.controller.config.setup_completed,
                coordinator_origin=self.controller.config.coordinator_origin)
        except (ValueError, TypeError) as error:
            messagebox.showerror("Invalid settings", str(error), parent=self.root)
            return
        self._run(lambda: self.controller.configure(config), lambda _: self.message.set("Settings saved. Start the local runtime to apply them."))

    def _diagnostics(self, check_carla):
        def show(report):
            self._last_diagnostics = report
            lines = []
            for key, title in (("synthetic", "Synthetic prerequisites"), ("carla", "CARLA prerequisites")):
                group = report["readiness"][key]
                lines.extend([f"{title}: {group['status'].replace('_', ' ')}", group["summary"], ""])
                for check in group["checks"]:
                    lines.append(f"{check['label']} — {check['status'].replace('_', ' ')}")
                    lines.append(check["detail"])
                    if check.get("next_step"):
                        lines.append(f"Next: {check['next_step']}")
                    lines.append("")
            self._set_text(self.diagnostic_text, "\n".join(lines))
            self.message.set("Diagnostics collected. Execution remains untested until an experiment finishes.")
        self._run(lambda: self.controller.diagnostics(check_carla=check_carla), show)

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _save_diagnostics(self):
        if self._last_diagnostics is None:
            self.message.set("Run a diagnostic check first.")
            return
        from uuid import uuid4
        report = self._last_diagnostics
        def save():
            directory = self.controller.profile / "diagnostics"
            directory.mkdir(exist_ok=True)
            path = directory / f"{uuid4().hex}.json"
            with path.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2)
            return path
        self._run(save, lambda path: self.message.set(f"Diagnostics saved locally: {path}"))

    def _import_report(self):
        path = filedialog.askopenfilename(parent=self.root, title="Import an exported JSON run report", filetypes=[("JSON", "*.json")])
        if path:
            def imported(target):
                self.message.set(f"Read-only report archived: {target}")
                self._refresh_imports()
            self._run(lambda: self.controller.import_report(path), imported)

    def _refresh_imports(self):
        # Filenames only; report content is bounded and loaded on explicit selection.
        paths = sorted((self.controller.profile / "imported-reports").glob("*.json"))[-100:]
        self._imports = {path.name: path for path in paths}
        self.archive_list.delete(0, "end")
        for name in self._imports:
            self.archive_list.insert("end", name)

    def _show_archive(self, event=None):
        selected = self.archive_list.curselection()
        if selected:
            path = self._imports[self.archive_list.get(selected[0])]
            self._run(lambda: read_json(path), lambda report: self._set_text(self.archive_text, json.dumps(report, indent=2)))
