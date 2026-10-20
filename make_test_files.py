"""Write the .idotx files to check by hand in the iDOT software, plus TEST_PLAN.md describing each one.

Each file answers one question the local tests cannot (only the iDOT software can say what it accepts).
T02-T07 go through ib.generate(), exactly like the GUI; their input sheets are kept in inputs/.

    python make_test_files.py [output folder]
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.dont_write_bytecode = True
import idot_builder as ib  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "iDOT_test_files"
REAL_SHEET = HERE / "iDOT_pipetting_sheet.csv"


@dataclass
class Case:
    name: str
    question: str
    contents: str
    check: str
    csv: str | Path | None = None  # sheet text or file; None = rebuilt straight from the template


def plate_map_csv() -> str:
    header = "," + ",".join(f"{col}{row}" for col in ib.COLUMNS for row in ib.ROWS)
    volumes = ",".join(f"0.{ib.ROWS.index(row) + 1}{col:02d}" for col in ib.COLUMNS for row in ib.ROWS)
    return f"{header}\nWater,{volumes}\n"


CASES = [
    Case(
        "T01_template_roundtrip",
        "Does the iDOT software open a file written by this tool at all?",
        "Wells A1, B1, C1 rebuilt from iDOT_template.idotx (same liquids, same volumes); all other wells "
        "empty. The source plate is recomputed: 17 liquids, placed column by column from A1, each holding "
        "exactly what the wells use.",
        "Opens without errors. A1, B1, C1 match iDOT_template.idotx. Source plate: the liquids now sit in "
        "A1, B1, C1, ... and hold what is dispensed, e.g. MoS_Libicher 4.934 µL (the template said "
        "9.868 µL, apparently left over from a deleted well). If this file fails, the others will too.",
    ),
    Case(
        "T02_new_liquid_names",
        "Does the software accept liquid names that are not in its liquid library yet?",
        "A1 only: MoS_pSB0195_sfGFP_pepper 1.725 µL, MoS_tSB0130_PovSak 1.725 µL, MoS_HBC620 0.345 µL.",
        "Opens. A1 has exactly these 3 liquids (none of the template's 6); B1 and C1 are empty (they are "
        "filled in the template). Note whether it asks to save the new liquids and which liquid class "
        "they get.",
        csv=",1A\npSB0195 (sfGFP-pepper),1.725\ntSB0130 (PovSak),1.725\nHBC620,0.345\n",
    ),
    Case(
        "T03_long_liquid_names",
        "Are long names accepted and shown in full? The real sheet produces names of up to 62 characters.",
        "A1 only: MoS_rNTPS_50x_18_75_mM_ATP_12_5_mM_GTP_6_25_mM_CTP_6_25_mM_UTP (62 characters) 0.69 µL, "
        "MoS_10_formyl_5_6_7_8_tetrahydrofolic_acid_100_g_L (50) 1.035 µL, "
        "MoS_tRNA_62_72_g_l_E_coli_tRNA_extract (38) 3.588 µL.",
        "Opens and the names are not cut off. If it errors or shortens them, tell me the longest name "
        "that survives and I will add automatic shortening.",
        csv=(
            ',1A\n"rNTPS (50x|18.75 mM ATP, 12.5 mM GTP, 6.25 mM CTP, 6.25 mM UTP)",0.690\n'
            '"10-formyl-5,6,7,8-tetrahydrofolic acid [100 g/L]",1.035\n'
            "tRNA (62.72 g/l E. coli tRNA extract),3.588\n"
        ),
    ),
    Case(
        "T04_volume_precision",
        "Are volumes converted correctly, including the sheet's 0.1 nL precision (4.9335 µL)?",
        "A1: MoS_Libicher 4.9335 µL (stored as 4.9335E-06). B1: MoS_Water 50.5 µL (stored as 5.05E-05).",
        "A1 shows 4.9335 µL (tell me if it shows 4.934 instead, i.e. the software rounds to 1 nL). "
        "B1 shows 50.5 µL.",
        csv=",1A,1B\nLibicher,4.9335,\nWater,,50.5\n",
    ),
    Case(
        "T05_plate_map_96",
        "Does every sheet column end up in the right well, over the whole plate (no swapped rows and columns)?",
        "All 96 wells, MoS_Water only. The volume encodes the well: 0.RCC µL, where R = row number "
        "(A=1 ... H=8) and CC = column. A1 = 0.101 µL, H1 = 0.801, A12 = 0.112, D7 = 0.407, H12 = 0.812. "
        "Source: MoS_Water 43.824 µL in A1.",
        "Spot-check A1, H1, A12, D7, H12 (and any other well you like).",
        csv=plate_map_csv(),
    ),
    Case(
        "T06_split_source_wells",
        "Can one liquid sit in several source wells? The real sheet needs this: ddH20 adds up to 190.4 µL, "
        "but one source well holds 80 µL.",
        "A1-H1: MoS_Water 20 µL each (160 µL in total). Source plate: MoS_Water in A1, B1 and C1, "
        "53.334 µL each.",
        "Opens. The source plate shows MoS_Water in A1, B1, C1; wells A1-H1 show 20 µL. If the software has "
        "a check / validate / simulate step, run it: does it complain about too little MoS_Water?",
        csv=",1A,1B,1C,1D,1E,1F,1G,1H\nWater,20,20,20,20,20,20,20,20\n",
    ),
    Case(
        "T07_full_sheet_MoS",
        "The real thing: iDOT_pipetting_sheet.csv with initials MoS.",
        "16 wells (A1-H2), 28 liquids, 232 dispense steps. ddH20 is split over source wells D4, E4, F4. "
        "The two '10-formyl-5,6,7,8-tetrahydrofolic acid [1 g/L]' rows share one source liquid.",
        "Opens. Spot-check: A1 has 10 liquids, incl. MoS_Libicher_EM_7x_exact_composition1 4.9335 µL and "
        "MoS_ddH20 14.801 µL; C1 has 18 liquids; B2 has 4 (MoS_tSB0146_dead 1.73, "
        "MoS_PUREfrex_1_0_Solution_1_2x 17.25, MoS_HBC620 0.345, MoS_ddH20 15.18 µL); H2 has "
        "MoS_10_formyl_5_6_7_8_tetrahydrofolic_acid_100_g_L 3.45 µL. Every well totals about 34.5 µL "
        "while the template's assay volume is 15 µL (its own A1 already held 25 µL): tell me if the "
        "software warns about that.",
        csv=REAL_SHEET,
    ),
]


def template_wells(template_path: Path) -> dict[str, list[tuple[str, Decimal]]]:
    contents = ib.read_project(template_path.read_bytes())
    return {
        well: [(s["SourceLiquid"], ib.liters_to_ul(s["Volume"])) for s in steps]
        for well, steps in contents.wells.items()
        if steps
    }


def make_all(out_dir: Path = DEFAULT_OUT, template_path: Path = ib.DEFAULT_TEMPLATE) -> list[Path]:
    inputs = out_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    template = ib.load_template(template_path)
    written = []
    for case in CASES:
        path = out_dir / f"{case.name}.idotx"
        if case.csv is None:
            wells = template_wells(template_path)
            liquids = {name: name for steps in wells.values() for name, _ in steps}
            plan = ib.Plan(liquids=liquids, wells=wells)
            ib.write_project(path, template, wells, ib.layout_source_plate(plan, template).wells, overwrite=True)
        else:
            sheet = inputs / f"{case.name}.csv"
            sheet.write_bytes(case.csv.read_bytes() if isinstance(case.csv, Path) else case.csv.encode("utf-8"))
            ib.generate(sheet, "MoS", out_dir, path.name, template_path, overwrite=True)
        written.append(path)
    (out_dir / "TEST_PLAN.md").write_text(render_test_plan(), encoding="utf-8")
    return written


def render_test_plan() -> str:
    lines = [
        "# iDOT test files: what to check",
        "",
        "Open each file in the iDOT software and inspect it; there is no need to dispense anything.",
        "Please try all of them, even if one fails: each answers a different question.",
        "When the software offers to save new liquids to its library, answer No for T02-T06 (test liquids).",
        "",
        "Report back one line per file, e.g. `T03 failed: <exact error text>` (a screenshot is fine too).",
        "",
        "| File | Question |",
        "|---|---|",
    ]
    lines += [f"| {c.name}.idotx | {c.question} |" for c in CASES]
    for case in CASES:
        lines += [
            "",
            f"## {case.name}.idotx",
            "",
            f"**Question:** {case.question}",
            "",
            f"**In the file:** {case.contents}",
            "",
            f"**Check:** {case.check}",
            "",
            "**Result:** ",
        ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    for p in make_all(out):
        print(f"wrote {p}")
    print(f"wrote {out / 'TEST_PLAN.md'}")
