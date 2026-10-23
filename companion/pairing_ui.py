"""Explicit local account approval, with all enrollment I/O on the action thread."""

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
        self.permissions = {}
        ttk.Label(body, text="Connect your account", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(body, text="Pairing preview · Remote execution and the hosted dashboard are still in development.",
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
            ttk.Checkbutton(body, text=label, variable=variable).pack(anchor="w")
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
        approval = ttk.Frame(body)
        approval.pack(fill="x", pady=8)
        self.approve_button = app._button(approval, "Approve reviewed account", self.approve)
        app._button(approval, "Deny claim", self.deny)
        self.revoke_button = app._button(approval, "Revoke pairing", self.revoke)

    def close(self):
        # These variables live in a container, beyond the window's direct scan.
        self.permissions.clear()

    def _scroll(self, event):
        if str(event.widget).startswith(str(self.frame) + "."):
            self._canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _run(self, action):
        def checked():
            self.app._check_files()
            if not self.app._close_requested.is_set():
                return action()
        self.app._run(checked, lambda value: self.show(value) if value is not None else None)

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
        self._run(self.app.controller.revoke_pairing)

    def show(self, status):
        self._displayed = status
        state = status["state"]
        self.code.set(status.get("code", ""))
        self.saved_origin.set(f"Saved coordinator: {status.get('origin', 'none')}")
        self.approved_permissions.set("")
        self.account.set("No account claim has been reviewed.")
        summaries = {
            "unconfigured": "Save your coordinator HTTPS address to begin pairing.",
            "unpaired": "Ready to create a pairing code. Enter it in your account, then refresh here.",
            "attempt": f"Pairing code expires at {status.get('expires_at', '')}. Refresh after claiming it in your account.",
            "pending_approval": "Approval was saved on this computer. Refresh / resume to confirm its outcome safely.",
            "paired": "Account paired. Remote execution remains disabled in this preview.",
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
            self.approved_permissions.set("Approved permissions: " + ", ".join(label for scope, label, _ in PERMISSIONS if scope in selected))
