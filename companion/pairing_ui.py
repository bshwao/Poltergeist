"""Explicit local account approval, with all enrollment I/O on the action thread."""

from copy import deepcopy
import tkinter as tk
from tkinter import ttk

from shared.models.coordination import DevicePolicy, PermissionScope


PERMISSIONS = (
    (PermissionScope.STATUS, "View companion status", True),
    (PermissionScope.RESULT, "Read experiment results", True),
    (PermissionScope.EXECUTE_TEST, "Run synthetic experiments", False),
    (PermissionScope.EXECUTE_CARLA, "Run CARLA experiments", False),
    (PermissionScope.CANCEL, "Cancel remote experiments", False),
)


class PairingPanel:
    def __init__(self, app, parent):
        self.app = app
        self._displayed = {"state": "unconfigured"}
        self._relay = {"state": "disconnected", "owned": False, "admission": False,
                       "running": False, "remote_execution_enabled": False}
        self._runtime_accepting = False
        self._identity = None
        self._connection_pending = False
        self._connection_uncertain = False
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
        app.root.bind("<MouseWheel>", self._scroll, add="+")
        self.origin = tk.StringVar(value=app.controller.config.coordinator_origin or "")
        self.display_name = tk.StringVar(value="My Poltergeist companion")
        self.summary = tk.StringVar(value="Save your coordinator address to begin pairing.")
        self.code = tk.StringVar(value="")
        self.account = tk.StringVar(value="No account claim has been reviewed.")
        self.saved_origin = tk.StringVar(value="Saved coordinator: none")
        self.approved_permissions = tk.StringVar(value="")
        self.approved_limits = tk.StringVar(value="")
        self.connection = tk.StringVar(value="Connection: disconnected. Remote requests are paused.")
        self.allow_remote_execution = tk.BooleanVar(value=False)
        self.permissions = {}
        self.permission_controls = {}
        ttk.Label(body, text="Connect your account", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(body, text="Private connection preview. Pair this computer, then connect when you want to accept account requests.",
            wraplength=740).pack(anchor="w", pady=(6, 12))
        address = ttk.Frame(body)
        address.pack(fill="x")
        ttk.Label(address, text="Coordinator HTTPS address").pack(side="left", padx=(0, 8))
        ttk.Entry(address, textvariable=self.origin).pack(side="left", fill="x", expand=True, padx=(0, 8))
        app._button(address, "Save address", self.save_origin)
        name = ttk.Frame(body)
        name.pack(fill="x", pady=(10, 8))
        ttk.Label(name, text="Computer name").pack(side="left", padx=(0, 8))
        ttk.Entry(name, textvariable=self.display_name).pack(side="left", fill="x", expand=True)
        ttk.Label(body, text="Permissions you allow this account to request").pack(anchor="w", pady=(6, 4))
        for scope, label, enabled in PERMISSIONS:
            variable = tk.BooleanVar(value=enabled)
            self.permissions[scope] = variable
            control = ttk.Checkbutton(body, text=label, variable=variable)
            control.pack(anchor="w")
            self.permission_controls[scope] = control
        ttk.Label(body, text="Remote experiments are limited to the baseline controller, 120 seconds and 10 waiting runs. Map reloads, graphics changes and raw recordings are disabled.",
            wraplength=740).pack(anchor="w", pady=8)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(0, 8))
        self.begin_button = app._button(actions, "Create pairing code", self.begin)
        self.refresh_button = app._button(actions, "Refresh / resume", self.refresh)
        app._button(actions, "Discard unapproved code", self.discard)
        ttk.Label(body, textvariable=self.code, font=("Consolas", 18, "bold")).pack(anchor="w", pady=4)
        ttk.Label(body, textvariable=self.summary, wraplength=740).pack(anchor="w", pady=4)
        ttk.Label(body, textvariable=self.saved_origin, wraplength=740).pack(anchor="w", pady=4)
        ttk.Label(body, textvariable=self.account, wraplength=740).pack(anchor="w", pady=4)
        ttk.Label(body, textvariable=self.approved_permissions, wraplength=740).pack(anchor="w", pady=4)
        ttk.Label(body, textvariable=self.approved_limits, wraplength=740).pack(anchor="w", pady=4)
        approval = ttk.Frame(body)
        approval.pack(fill="x", pady=8)
        self.approve_button = app._button(approval, "Approve reviewed account", self.approve)
        app._button(approval, "Deny claim", self.deny)
        self.revoke_button = app._button(approval, "Revoke pairing", self.revoke)
        ttk.Label(body, text="Account connection", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(12, 4))
        ttk.Label(body, textvariable=self.connection, wraplength=740).pack(anchor="w", pady=4)
        self.consent_control = ttk.Checkbutton(body,
            text="Allow approved remote requests for this connection", variable=self.allow_remote_execution)
        self.consent_control.pack(anchor="w", pady=4)
        ttk.Label(body, text="Review the saved coordinator, account and approved permissions above. Without this check, Connect accepts no account requests. Disconnect before changing consent. Consent is cleared when you disconnect, stop or close this companion.",
            wraplength=740).pack(anchor="w", pady=4)
        connections = ttk.Frame(body)
        connections.pack(fill="x", pady=8)
        self.connect_button = app._button(connections, "Connect", self.connect)
        self.disconnect_button = app._button(connections, "Disconnect", self.disconnect)
        self.sync_controls()

    def close(self):
        self.reset_consent()
        # These variables live in a container, beyond the window's direct scan.
        self.permissions.clear()
        self.permission_controls.clear()

    def _scroll(self, event):
        if str(event.widget).startswith(str(self.frame) + "."):
            self._canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _run(self, action):
        def checked():
            self.app._check_files()
            if not self.app._close_requested.is_set():
                return action()
        self.app._run(checked, lambda value: self.show(value) if value is not None else None)

    def reset_consent(self):
        self.allow_remote_execution.set(False)

    def action_finished(self, *, failed=False):
        refresh = self._connection_pending and failed
        if refresh:
            self.reset_consent()
            self._connection_uncertain = True
            self.connection.set("Connection status needs review. Use Disconnect before connecting again.")
        self._connection_pending = False
        self.sync_controls()
        return refresh

    def sync_controls(self):
        busy = self.app._future is not None or self.app._closing
        owned = self._relay.get("owned") is True
        ready = (self._displayed.get("state") == "paired" and self._runtime_accepting
                 and not owned and not self._connection_uncertain)
        self.connect_button.state(["!disabled"] if ready and not busy else ["disabled"])
        self.disconnect_button.state(["!disabled"] if (owned or self._connection_uncertain) and not busy else ["disabled"])
        self.consent_control.state(["!disabled"] if ready and not busy else ["disabled"])
        editable = self._displayed.get("state") in {"unpaired", "attempt"} and not busy
        for control in self.permission_controls.values():
            control.state(["!disabled"] if editable else ["disabled"])

    def show_connection(self, relay, queue):
        previous_owned = self._relay.get("owned") is True
        self._relay = dict(relay)
        self._connection_uncertain = False
        self._runtime_accepting = queue.get("accepting") is True
        if ((previous_owned and not self._relay.get("owned")) or
                queue.get("state") in {"stopped", "stopping"} or
                self._relay.get("state") == "disconnecting"):
            self.reset_consent()
        state = self._relay.get("state")
        if state == "connected":
            detail = ("Approved remote requests are allowed." if self._relay.get("remote_execution_enabled") is True
                      else "Remote requests are paused.")
            text = "Connection: connected. " + detail
        elif state == "connecting":
            text = "Connection: connecting to the saved coordinator."
        elif state == "offline":
            text = "Connection: offline; retrying. No requests can arrive until the connection is restored."
        elif state == "disconnecting":
            text = "Connection: disconnecting. New remote requests are blocked; retry Disconnect if cleanup is still pending."
        elif state == "error":
            text = "Connection: unavailable. Disconnect and review the saved pairing before reconnecting."
        else:
            text = "Connection: disconnected. Remote requests are paused."
        self.connection.set(text)
        self.sync_controls()

    def connect(self):
        if (self.app._future is not None or self.app._closing or self._relay.get("owned") or self._connection_uncertain or
                self._displayed.get("state") != "paired" or not self._runtime_accepting):
            self.app.message.set("Start the local runtime and review the paired account before connecting.")
            return
        # Tcl variables and the reviewed display are captured on the GUI thread.
        allow = self.allow_remote_execution.get()
        displayed = deepcopy(self._displayed)
        self._connection_pending = True
        def connect():
            self.app._check_files()
            if self.app._close_requested.is_set():
                return self.app.controller.snapshot()
            self.app.controller.connect_relay(allow_remote_execution=allow, displayed_status=displayed)
            return self.app.controller.snapshot()
        self.app._run(connect, self.app._apply_snapshot)

    def disconnect(self):
        if self.app._future is not None or self.app._closing:
            return
        self.reset_consent()
        self._connection_pending = True
        def disconnect():
            clean = self.app.controller.disconnect_relay()
            return clean, self.app.controller.snapshot()
        def disconnected(result):
            clean, snapshot = result
            self.app._apply_snapshot(snapshot)
            self.app.message.set("Account disconnected. Accepted local work may continue." if clean else
                "New remote requests are blocked. Keep this window open and retry Disconnect; cleanup is still pending.")
        self.app._run(disconnect, disconnected)

    def _scopes(self):
        return [scope for scope, variable in self.permissions.items() if variable.get()]

    def save_origin(self):
        origin = self.origin.get().strip() or None
        self._run(lambda: self.app.controller.configure_pairing(origin))

    def begin(self):
        if self._displayed["state"] != "unpaired":
            self.app.message.set("Save an address and review any existing pairing before creating a code.")
            return
        name, scopes = self.display_name.get().strip(), self._scopes()
        if not name or not scopes:
            self.app.message.set("Choose a computer name and at least one permission.")
            return
        self._run(lambda: self.app.controller.begin_pairing(name, scopes))

    def refresh(self):
        self._run(lambda: self.app.controller.pairing_status(refresh=True))

    def approve(self):
        claim = self._displayed.get("claim")
        if not claim or claim["state"] != "claimed" or claim.get("claimed_account") is None:
            self.app.message.set("Refresh and review the claiming account before approving.")
            return
        scopes = self._scopes()
        if not scopes or not set(scopes).issubset(set(claim["requested_scopes"])):
            self.app.message.set("Select at least one permission from this pairing request.")
            return
        policy = DevicePolicy(scopes=scopes)
        self._run(lambda: self.app.controller.approve_pairing(claim, policy))

    def deny(self):
        claim = self._displayed.get("claim")
        if not claim:
            self.app.message.set("Refresh the current pairing before denying it.")
            return
        self._run(lambda: self.app.controller.deny_pairing(claim))

    def discard(self):
        self._run(self.app.controller.discard_pairing_attempt)

    def revoke(self):
        if self.app._future is not None or self.app._closing:
            return
        self.reset_consent()
        self._connection_pending = True
        def revoke():
            self.app._check_files()
            if not self.app._close_requested.is_set():
                self.app.controller.revoke_pairing()
            return self.app.controller.snapshot()
        self.app._run(revoke, self.app._apply_snapshot)

    def show(self, status):
        identity = {key: deepcopy(status.get(key)) for key in
                    ("origin", "device_id", "owner_id", "ownership_generation", "policy")}
        if identity != self._identity or status.get("state") != "paired":
            self.reset_consent()
        self._identity = identity
        self._displayed = deepcopy(status)
        state = status["state"]
        self.code.set(status.get("code", ""))
        self.saved_origin.set(f"Saved coordinator: {status.get('origin', 'none')}")
        self.approved_permissions.set("")
        self.approved_limits.set("")
        self.account.set("No account claim has been reviewed.")
        summaries = {
            "unconfigured": "Save your coordinator HTTPS address to begin pairing.",
            "unpaired": "Ready to create a pairing code. Enter it in your account, then refresh here.",
            "attempt": f"Pairing code expires at {status.get('expires_at', '')}. Refresh after claiming it in your account.",
            "pending_approval": "Approval was saved on this computer. Refresh / resume to confirm its outcome safely.",
            "paired": "Account paired. Review the approved permissions, then choose whether to allow requests when you connect. Revoke and pair again to change these permissions.",
            "error": status.get("message", "Saved pairing could not be read."),
        }
        self.summary.set(summaries.get(state, "Refresh to review pairing."))
        claim = status.get("claim")
        if claim:
            owner = claim.get("claimed_account")
            if owner:
                self.account.set(f"Account requesting access: {owner['username']}\nAccount ID: {owner['account_id']}\nReview this account and your selected permissions before approving.")
            if claim["state"] in ("denied", "expired", "cancelled"):
                self.code.set("")
                self.summary.set(f"Pairing {claim['state']}. Discard the unapproved code to start again.")
        if status.get("owner_id"):
            self.account.set(f"Approved account ID: {status['owner_id']}\nDevice ID: {status['device_id']}")
            selected = set(status["policy"]["scopes"])
            for scope, variable in self.permissions.items():
                variable.set(scope in selected)
            self.approved_permissions.set("Approved permissions: " + ", ".join(label for scope, label, _ in PERMISSIONS if scope in selected))
            policy = status["policy"]
            self.approved_limits.set(f"Approved limits: {policy['max_duration_s']:g} simulated seconds, {policy['max_ticks']} ticks and {policy['max_waiting_runs']} waiting remote runs. Baseline controller only.")
        self.sync_controls()
