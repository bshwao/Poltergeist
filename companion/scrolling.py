"""A native scroll surface that keeps keyboard-focused controls in view."""

import tkinter as tk
from tkinter import ttk


_FOCUS_BORDER = 2


class ScrollPanel:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.canvas = tk.Canvas(self.frame, highlightthickness=_FOCUS_BORDER, borderwidth=0,
                                takefocus=True, yscrollincrement=16)
        self.scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(self.canvas, padding=12)
        self._item = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(
            self._item, width=max(1, event.width - 2 * _FOCUS_BORDER)))
        self.content.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        for key, count, units in (("<Prior>", -1, "pages"), ("<Next>", 1, "pages"),
                                   ("<Up>", -1, "units"), ("<Down>", 1, "units")):
            self.canvas.bind(key, lambda event, n=count, unit=units: self._scroll(n, unit))
        self.canvas.bind("<Home>", lambda event: self._end(0))
        self.canvas.bind("<End>", lambda event: self._end(1))
        self.canvas.bind("<MouseWheel>", self._wheel)

    def _scroll(self, count, units):
        self.canvas.yview_scroll(count, units)
        return "break"

    def _end(self, fraction):
        self.canvas.yview_moveto(fraction)
        return "break"

    def _wheel(self, event):
        if event.delta:
            count = max(1, abs(event.delta) // 40)
            return self._scroll(-count if event.delta > 0 else count, "units")

    def bind_content(self):
        def visit(widget):
            widget.bind("<FocusIn>", self._focus, add="+")
            # Text, lists and selectors keep their own native wheel behavior.
            if not isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox)):
                widget.bind("<MouseWheel>", self._wheel, add="+")
            for child in widget.winfo_children():
                visit(child)
        visit(self.content)

    def _focus(self, event):
        # Tk also sends FocusIn to ancestors of the focused control. Scrolling
        # a tall container into view can immediately hide its focused child.
        if str(self.canvas.tk.call("focus")) != str(event.widget):
            return
        top = event.widget.winfo_rooty() - self.canvas.winfo_rooty() - _FOCUS_BORDER
        widget_height = event.widget.winfo_height()
        bottom = top + widget_height
        height = self.canvas.winfo_height() - 2 * _FOCUS_BORDER
        # Leave a unit of room so scroll-increment rounding cannot clip focus.
        # Align tall controls at the top so the user sees their heading and first row.
        if widget_height > height or top < 0:
            delta = top - 16
        else:
            delta = bottom - height + 16 if bottom > height else 0
        if delta:
            self.canvas.yview_moveto(
                (self.canvas.canvasy(_FOCUS_BORDER) + delta) / max(1, self.content.winfo_height()))
