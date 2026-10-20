"""Small window for turning an iDOT pipetting sheet (CSV) into an .idotx protocol file."""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

sys.dont_write_bytecode = True  # keep __pycache__ out of the synced folder
import idot_builder as ib  # noqa: E402


class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=12)
        master.title("iDOT protocol builder")
        self.csv_var = tk.StringVar()
        self.out_dir_var = tk.StringVar()
        self.initials_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self._name_edited = False  # stop suggesting a file name once the user typed one
        self._suggesting = False
        self._build()
        self.csv_var.trace_add("write", lambda *_: self._suggest_name())
        self.initials_var.trace_add("write", lambda *_: self._suggest_name())
        self.name_var.trace_add("write", lambda *_: self._name_typed())

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        self.grid(row=0, column=0, sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        def path_row(row: int, label: str, var: tk.StringVar, browse) -> None:
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(self, textvariable=var).grid(row=row, column=1, sticky="ew", padx=6)
            ttk.Button(self, text="Browse…", command=browse).grid(row=row, column=2, sticky="ew")

        path_row(0, "Pipetting sheet (.csv)", self.csv_var, self._browse_csv)
        path_row(1, "Save in folder", self.out_dir_var, self._browse_out_dir)

        ttk.Label(self, text="Your initials").grid(row=2, column=0, sticky="w", pady=3)
        initials = ttk.Frame(self)
        initials.grid(row=2, column=1, columnspan=2, sticky="w", padx=6)
        ttk.Entry(initials, textvariable=self.initials_var, width=10).pack(side="left")
        ttk.Label(initials, text="prefix for every liquid name, e.g. MoS → MoS_ATP", foreground="gray").pack(
            side="left", padx=8
        )

        ttk.Label(self, text="File name").grid(row=3, column=0, sticky="w", pady=3)
        name = ttk.Frame(self)
        name.grid(row=3, column=1, sticky="ew", padx=6)
        name.columnconfigure(0, weight=1)
        ttk.Entry(name, textvariable=self.name_var).grid(row=0, column=0, sticky="ew")
        ttk.Label(name, text=".idotx").grid(row=0, column=1, padx=(4, 0))

        buttons = ttk.Frame(self)
        buttons.grid(row=5, column=0, columnspan=3, sticky="w", pady=(12, 8))
        tk.Button(
            buttons,
            text="Generate .idotx",
            command=self.generate,
            bg="#1f6fd1",
            fg="white",
            activebackground="#1858a8",
            activeforeground="white",
            font=("Segoe UI", 13, "bold"),
            relief="flat",
            padx=28,
            pady=10,
            cursor="hand2",
        ).pack(side="left")
        ttk.Button(buttons, text="Open folder", command=self._open_folder).pack(side="left", padx=10)

        self.log = ScrolledText(self, height=22, width=110, font=("Consolas", 9), state="disabled", wrap="none")
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew")
        xscroll = ttk.Scrollbar(self, orient="horizontal", command=self.log.xview)
        xscroll.grid(row=7, column=0, columnspan=3, sticky="ew")
        self.log.configure(xscrollcommand=xscroll.set)
        self.rowconfigure(6, weight=1)
        self._write_log(
            "1. Choose the pipetting sheet: header row 1A, 1B, ... ; one row per liquid; volumes in µL.\n"
            "2. Choose where to save, enter your initials, and press Generate.\n"
            "Every file is re-read and checked against the sheet before it is reported as saved.\n"
        )

    # ------------------------------------------------------------------ actions

    def generate(self) -> Path | None:
        """Build the file.  Returns its path, or None if nothing was written."""
        csv_path = self.csv_var.get().strip()
        out_dir = self.out_dir_var.get().strip()
        initials = self.initials_var.get().strip()
        template = ib.DEFAULT_TEMPLATE  # fixed: every file is built from the shipped template

        missing = []
        if not csv_path or not Path(csv_path).is_file():
            missing.append("Choose the pipetting sheet (.csv).")
        if not out_dir or not Path(out_dir).is_dir():
            missing.append("Choose the folder to save the file in.")
        if not initials:
            missing.append("Enter your initials.")
        if not template.is_file():
            missing.append(f"The template {template.name} is missing from the program folder.")
        if missing:
            messagebox.showerror("Missing information", "\n".join(missing))
            return None

        try:
            ib.check_initials(initials)
            filename = ib.output_filename(self.name_var.get() or ib.default_output_name(csv_path, initials))
        except ib.IdotError as exc:
            messagebox.showerror("Check your input", str(exc))
            return None
        target = Path(out_dir) / filename
        overwrite = False
        if target.exists():
            if not messagebox.askyesno("Replace file?", f"{target.name} already exists in that folder.\n\nReplace it?"):
                return None
            overwrite = True

        try:
            result = ib.generate(csv_path, initials, out_dir, filename, template, overwrite=overwrite)
        except ib.IdotError as exc:
            self._write_log(f"\nNOT SAVED: {exc}\n")
            messagebox.showerror("Could not create the file", str(exc))
            return None
        except Exception:  # an unexpected bug: show the details rather than failing silently
            details = traceback.format_exc()
            self._write_log(f"\nNOT SAVED (unexpected error):\n{details}\n")
            messagebox.showerror("Unexpected error", details)
            return None

        self._write_log(result.report() + "\n", replace=True)
        if result.warnings:
            messagebox.showwarning(
                "Saved, with warnings", f"Saved {result.path.name}.\n\nPlease read the {len(result.warnings)} warning(s) in the log."
            )
        else:
            messagebox.showinfo("Saved", f"Saved and verified:\n{result.path}")
        return result.path

    def _browse_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Pipetting sheet", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path:
            self.csv_var.set(path)
            if not self.out_dir_var.get().strip():
                self.out_dir_var.set(str(Path(path).parent))

    def _browse_out_dir(self) -> None:
        path = filedialog.askdirectory(title="Save the .idotx file in")
        if path:
            self.out_dir_var.set(path)

    def _open_folder(self) -> None:
        folder = self.out_dir_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("No folder", "Choose the folder to save in first.")
        elif sys.platform == "win32":
            os.startfile(folder)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])

    # ------------------------------------------------------------------ helpers

    def _suggest_name(self) -> None:
        csv_path = self.csv_var.get().strip()
        if self._name_edited or not csv_path:
            return
        self._suggesting = True
        try:
            self.name_var.set(ib.default_output_name(csv_path, self.initials_var.get()))
        finally:
            self._suggesting = False

    def _name_typed(self) -> None:
        if not self._suggesting:
            self._name_edited = bool(self.name_var.get().strip())

    def _write_log(self, text: str, replace: bool = False) -> None:
        self.log.configure(state="normal")
        if replace:
            self.log.delete("1.0", "end")
        self.log.insert("end", text)
        self.log.see("1.0" if replace else "end")
        self.log.configure(state="disabled")


def main() -> None:
    if sys.platform == "win32":
        try:  # sharp text on high-resolution screens
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.minsize(760, 480)
    root.mainloop()


if __name__ == "__main__":
    main()
