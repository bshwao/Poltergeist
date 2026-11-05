"""Companion typography matching the lab, retaining native Windows controls."""

from tkinter import font, ttk


def configure_design(root):
    # Named fonts respect Tk's display scaling. Keep the platform's native theme
    # and system colors so Windows contrast settings still apply to controls.
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        font.nametofont(name, root=root).configure(family="Segoe UI", size=10)
    style = ttk.Style(root)
    style.configure("Lab.Title.TLabel", font=("Segoe UI", 20, "bold"))
    style.configure("Lab.Runtime.TLabel", font=("Segoe UI", 11, "bold"), padding=(0, 4))
    style.configure("TButton", padding=(10, 6))
    style.configure("TNotebook.Tab", padding=(9, 7))
    row_font = font.nametofont("TkDefaultFont", root=root)
    style.configure("Treeview", font="TkDefaultFont", rowheight=max(28, row_font.metrics("linespace") + 8))
    style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
