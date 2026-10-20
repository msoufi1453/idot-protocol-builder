"""Drive the real Tk window (no clicks needed) and check the files made for testing in the iDOT software."""

from decimal import Decimal as D
from pathlib import Path

import pytest

import idot_builder as ib
import make_test_files as mtf

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "iDOT_pipetting_sheet.csv"


@pytest.fixture(scope="module")
def tk_root():
    # One Tk per process: re-creating Tk() after destroy() intermittently fails on Windows
    # ("invalid command name tcl_findLibrary").  The real GUI only ever creates one.
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def gui(tk_root, monkeypatch):
    import tkinter as tk

    import idot_gui

    dialogs = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(idot_gui.messagebox, name, lambda *a, _n=name, **k: dialogs.append((_n, a)))
    window = tk.Toplevel(tk_root)
    window.withdraw()
    app = idot_gui.App(window)
    yield app, dialogs, monkeypatch, idot_gui
    window.destroy()


def log_text(app):
    return app.log.get("1.0", "end")


def test_gui_suggests_a_file_name_until_the_user_types_one(gui):
    app, _, _, _ = gui
    app.csv_var.set(str(SHEET))
    app.initials_var.set("MoS")
    assert app.name_var.get() == ib.default_output_name(SHEET, "MoS")
    app.name_var.set("my run")
    app.initials_var.set("AB")
    assert app.name_var.get() == "my run"
    app.name_var.set("")  # cleared: suggestions resume
    app.initials_var.set("CD")
    assert app.name_var.get() == ib.default_output_name(SHEET, "CD")


def test_gui_generates_a_verified_file(gui, tmp_path):
    app, dialogs, monkeypatch, idot_gui = gui
    app.csv_var.set(str(SHEET))
    app.out_dir_var.set(str(tmp_path))
    app.initials_var.set("MoS")
    app.name_var.set("gui_run")
    path = app.generate()
    assert path == tmp_path / "gui_run.idotx" and path.exists()
    assert dialogs[-1][0] == "showwarning"  # the real sheet has 2 warnings (duplicate row, split water)
    assert "Saved and verified" in log_text(app) and "MoS_ddH20" in log_text(app)
    before = path.read_bytes()

    monkeypatch.setattr(idot_gui.messagebox, "askyesno", lambda *a, **k: False)
    assert app.generate() is None  # declined overwrite: file untouched
    assert path.read_bytes() == before

    app.initials_var.set("AB")
    monkeypatch.setattr(idot_gui.messagebox, "askyesno", lambda *a, **k: True)
    assert app.generate() == path
    assert b"AB_ddH20" in path.read_bytes()


def test_gui_reports_missing_and_bad_input(gui, tmp_path):
    app, dialogs, _, _ = gui
    assert app.generate() is None
    kind, (title, message) = dialogs[-1]
    assert kind == "showerror" and "pipetting sheet" in message and "initials" in message

    app.csv_var.set(str(SHEET))
    app.out_dir_var.set(str(tmp_path))
    app.initials_var.set("M.S")
    assert app.generate() is None
    assert dialogs[-1][0] == "showerror" and "Initials" in dialogs[-1][1][1]

    bad = tmp_path / "bad.csv"
    bad.write_text(",1A\nWater,abc\n", encoding="utf-8")
    app.csv_var.set(str(bad))
    app.initials_var.set("MoS")
    assert app.generate() is None
    assert "NOT SAVED" in log_text(app) and "abc" in dialogs[-1][1][1]
    assert not list(tmp_path.glob("*.idotx"))


def test_test_files(tmp_path):
    written = mtf.make_all(tmp_path)
    assert [p.name for p in written] == [f"{c.name}.idotx" for c in mtf.CASES]
    assert len(written) == 7
    plan_md = (tmp_path / "TEST_PLAN.md").read_text(encoding="utf-8")
    for case in mtf.CASES:
        assert f"## {case.name}.idotx" in plan_md

    def wells(name):
        contents = ib.read_project((tmp_path / f"{name}.idotx").read_bytes())
        return contents, {
            w: [(s["SourceLiquid"], ib.liters_to_ul(s["Volume"])) for s in steps]
            for w, steps in contents.wells.items()
            if steps
        }

    t01, t01_wells = wells("T01_template_roundtrip")
    assert t01_wells == mtf.template_wells(ib.DEFAULT_TEMPLATE)
    assert len(t01.source) == 17 and t01.title == "T01_template_roundtrip"
    libicher = next(e for e in t01.source if e["Liquid"] == "MoS_Libicher")
    assert libicher["Volume"] == "4.934E-06"

    _, t02 = wells("T02_new_liquid_names")
    assert list(t02) == ["A1"] and len(t02["A1"]) == 3

    _, t03 = wells("T03_long_liquid_names")
    assert max(len(name) for name, _ in t03["A1"]) == 62

    _, t04 = wells("T04_volume_precision")
    assert t04 == {"A1": [("MoS_Libicher", D("4.9335"))], "B1": [("MoS_Water", D("50.5"))]}

    t05, t05_wells = wells("T05_plate_map_96")
    assert len(t05_wells) == 96
    for well, value in {"A1": "0.101", "H1": "0.801", "A12": "0.112", "D7": "0.407", "H12": "0.812"}.items():
        assert t05_wells[well] == [("MoS_Water", D(value))]
    assert [(e["Position"], e["Volume"]) for e in t05.source] == [("A1", "4.3824E-05")]

    t06, _ = wells("T06_split_source_wells")
    assert [(e["Position"], e["Liquid"], e["Volume"]) for e in t06.source] == [
        ("A1", "MoS_Water", "5.3334E-05"),
        ("B1", "MoS_Water", "5.3334E-05"),
        ("C1", "MoS_Water", "5.3334E-05"),
    ]

    _, t07 = wells("T07_full_sheet_MoS")
    assert len(t07) == 16 and sum(len(s) for s in t07.values()) == 232
    assert (tmp_path / "inputs" / "T07_full_sheet_MoS.csv").read_bytes() == SHEET.read_bytes()
