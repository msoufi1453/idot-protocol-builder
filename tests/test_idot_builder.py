"""Tests for idot_builder.  Run:  python -m pytest tests

The iDOT software itself cannot be run here, so these tests pin down everything else:
  * numbers are written exactly like the iDOT software writes them (checked against every number
    in the template) and µL -> L never drifts (checked exhaustively on a 1 nL grid);
  * rebuilding the template's own wells reproduces the template byte for byte;
  * a real sheet goes CSV -> .idotx -> XML parser and comes back identical (independent oracle);
  * random sheets do the same; the self-check catches every kind of tampering we could think of.
"""

import codecs
import csv
import random
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

import pytest

import idot_builder as ib

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "iDOT_template.idotx"
SHEET = ROOT / "iDOT_pipetting_sheet.csv"


@pytest.fixture(scope="module")
def template():
    return ib.load_template(TEMPLATE)


def template_text() -> str:
    return TEMPLATE.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")


def template_wells():
    contents = ib.read_project(TEMPLATE.read_bytes())
    return {
        well: [(s["SourceLiquid"], ib.liters_to_ul(s["Volume"])) for s in steps]
        for well, steps in contents.wells.items()
        if steps
    }


def build(template, csv_text, initials="MoS", title="test"):
    plan = ib.make_plan(ib.parse_sheet(csv_text), initials)
    layout = ib.layout_source_plate(plan, template)
    data = ib.encode(template, ib.build_text(template, plan.wells, layout.wells, title))
    return plan, layout, data


def wells_in_file(data):
    """{well: [(liquid, µL)]} for non-empty wells, read with a plain XML parser."""
    return {
        well: [(s["SourceLiquid"], ib.liters_to_ul(s["Volume"])) for s in steps]
        for well, steps in ib.read_project(data).wells.items()
        if steps
    }


# =========================================================================== numbers


def test_format_reproduces_every_number_in_the_template():
    text = template_text()
    numbers = re.findall(r">([-+0-9.E]+)<", text) + re.findall(r'Volume="([^"]+)"', text)
    assert len(numbers) > 100
    for s in numbers:
        assert ib.format_double(float(s)) == s


@pytest.mark.parametrize(
    "ul, expected",
    [
        ("4.934", "4.934E-06"),  # the user's examples
        ("50.5", "5.05E-05"),
        ("4.9335", "4.9335E-06"),
        ("0.345", "3.45E-07"),
        ("14.08", "1.408E-05"),
        ("1.725", "1.725E-06"),
        ("17.25", "1.725E-05"),
        ("1.971", "1.971E-06"),  # the iDOT GUI itself wrote 1.9710000000000003E-06 here (float noise)
        ("0.035", "3.5E-08"),
        ("0.001", "1E-09"),
        ("0.1", "1E-07"),
        ("15", "1.5E-05"),
        ("80", "8E-05"),
        ("299", "0.000299"),
        ("100", "0.0001"),
        ("1.7250000000000001", "1.725E-06"),  # Excel float noise below double precision disappears
    ],
)
def test_volume_conversion_examples(ul, expected):
    assert ib.format_volume_ul(D(ul)) == expected


def test_volume_conversion_is_exact_on_a_1_nl_grid_up_to_100_ul():
    for n in range(1, 100_001):
        ul = D(n).scaleb(-3)
        s = ib.format_volume_ul(ul)
        assert D(s).scaleb(6) == ul, (ul, s)
        assert re.fullmatch(r"\d(\.\d+)?E-\d\d|0\.\d+", s), s


def test_volume_conversion_is_exact_for_random_4_decimal_values():
    rng = random.Random(1)
    for _ in range(20_000):
        ul = D(rng.randint(1, 10_000_000)).scaleb(-4)  # 0.0001 .. 1000 µL
        assert ib.liters_to_ul(ib.format_volume_ul(ul)) == ul


def test_concentration_formula_matches_every_step_in_the_template():
    steps = re.findall(
        r"<AssayVolume>([^<]+)</AssayVolume>\s*<Volume>([^<]+)</Volume>\s*<Concentration>([^<]+)</Concentration>",
        template_text(),
    )
    assert len(steps) == 35
    for assay, volume, concentration in steps:
        assert ib.concentration_str(volume, assay) == concentration


def test_additional_volume_formula_matches_every_source_well_in_the_template():
    pairs = re.findall(r'Volume="([^"]+)" AdditionalVolume="([^"]+)"', template_text())
    assert len(pairs) == 17
    for volume, additional in pairs:
        assert ib.additional_volume_str(volume) == additional


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_format_double_rejects_non_finite(bad):
    with pytest.raises(ValueError):
        ib.format_double(bad)


# =========================================================================== names and wells

SHEET_NAMES = [
    ("tSB0146 (dead)", "tSB0146_dead"),
    ("Libicher EM (7x|exact composition1)", "Libicher_EM_7x_exact_composition1"),
    ("Amino acid mix (16.66x|6 mM each )", "Amino_acid_mix_16_66x_6_mM_each"),
    ("dNTPs (100 mM total, 25 mM each)", "dNTPs_100_mM_total_25_mM_each"),
    (
        "rNTPS (50x|18.75 mM ATP, 12.5 mM GTP, 6.25 mM CTP, 6.25 mM UTP)",
        "rNTPS_50x_18_75_mM_ATP_12_5_mM_GTP_6_25_mM_CTP_6_25_mM_UTP",
    ),
    ("tRNA (62.72 g/l E. coli tRNA extract)", "tRNA_62_72_g_l_E_coli_tRNA_extract"),
    ("PUREfrex 1.0 Solution 1 (2x)", "PUREfrex_1_0_Solution_1_2x"),
    ("Hepes-KOH, pH 8.0", "Hepes_KOH_pH_8_0"),
    ("pSB0195 (sfGFP-pepper)", "pSB0195_sfGFP_pepper"),
    ("potassium glutamate ", "potassium_glutamate"),
    ("hemi-magnesium glutamate", "hemi_magnesium_glutamate"),
    ("20 AAs (Nitin)", "20_AAs_Nitin"),
    ("10-formyl-5,6,7,8-tetrahydrofolic acid [1 g/L]", "10_formyl_5_6_7_8_tetrahydrofolic_acid_1_g_L"),
    ("10-formyl-5,6,7,8-tetrahydrofolic acid [100 g/L]", "10_formyl_5_6_7_8_tetrahydrofolic_acid_100_g_L"),
    ("ddH20", "ddH20"),
    ("ATP", "ATP"),
    # beyond the current sheet
    ("5 µM Mg²⁺", "5_uM_Mg2"),
    ("Café/Crème", "Cafe_Creme"),
    ("Straße", "Stra_e"),
    ("a@b#c$d%e&f*g=h+i~j", "a_b_c_d_e_f_g_h_i_j"),
    ("  __x__  ", "x"),
    ("<tag> & 'quote' \"dq\"", "tag_quote_dq"),
]


@pytest.mark.parametrize("raw, expected", SHEET_NAMES)
def test_sanitize_name(raw, expected):
    assert ib.sanitize_name(raw) == expected


def test_liquid_name_gets_initials_prefix():
    assert ib.liquid_name("MoS", "Libicher EM (7x|exact composition1)") == "MoS_Libicher_EM_7x_exact_composition1"


def test_sanitized_names_only_ever_contain_safe_characters():
    rng = random.Random(2)
    alphabet = "abcXYZ019 _-.,;:|/\\()[]{}<>&\"'@#$%^*+=~`!?µμ²³°éüßø\t\u00a0中文"
    for _ in range(5000):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        name = ib.sanitize_name(raw)
        assert name == "" or re.fullmatch(r"[A-Za-z0-9]+(_[A-Za-z0-9]+)*", name), (raw, name)


@pytest.mark.parametrize("raw", ["", "???", " - ", "中文"])
def test_name_without_letters_or_digits_is_rejected(raw):
    with pytest.raises(ib.SheetError):
        ib.liquid_name("MoS", raw)


@pytest.mark.parametrize("ok", ["MoS", "AB", "X", "ab12", " MoS "])
def test_valid_initials(ok):
    assert ib.check_initials(ok) == ok.strip()


@pytest.mark.parametrize("bad", ["", "Mo S", "M.S", "M_S", "Mo/S", "ABCDEFGHIJK", "Mø"])
def test_invalid_initials(bad):
    with pytest.raises(ib.IdotError):
        ib.check_initials(bad)


@pytest.mark.parametrize(
    "label, well",
    [("1A", "A1"), ("12H", "H12"), ("2b", "B2"), (" 3 C ", "C3"), ("01A", "A1"), ("A1", "A1"), ("h12", "H12"), ("A01", "A1")],
)
def test_parse_well(label, well):
    assert ib.parse_well(label) == well


@pytest.mark.parametrize("label", ["13A", "0A", "1I", "A13", "A0", "I1", "AA1", "1", "A", "", "1A1", "A-1", "123A"])
def test_parse_well_rejects(label):
    with pytest.raises(ib.SheetError):
        ib.parse_well(label)


def test_well_order_is_column_major():
    assert ib.ALL_WELLS[:9] == ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1", "A2"]
    assert len(ib.ALL_WELLS) == len(set(ib.ALL_WELLS)) == 96


def test_template_well_positions_match_their_names():
    root = ET.fromstring(TEMPLATE.read_bytes()[3:])
    steps = root.findall("TargetPlates/TargetPlate/ProtocolSteps/WellgroupProtocolStep")
    assert [s.get("Identifier") for s in steps] == ib.ALL_WELLS
    for s in steps:
        begin = s.find("CellGroupList/CellGroup/BeginPosition")
        assert (int(begin.get("X")), int(begin.get("Y"))) == ib.well_xy(s.get("Identifier"))


# =========================================================================== the real sheet


@pytest.fixture(scope="module")
def sheet():
    return ib.read_sheet(SHEET)


@pytest.fixture(scope="module")
def plan(sheet):
    return ib.make_plan(sheet, "MoS")


def test_real_sheet_shape(sheet, plan):
    assert sheet.wells == [f"{r}{c}" for c in (1, 2) for r in ib.ROWS]
    assert len(sheet.rows) == 29
    assert len(plan.liquids) == 28  # the [1 g/L] folate row appears twice
    assert list(plan.wells) == sheet.wells
    assert plan.n_steps == 232


def test_real_sheet_values(plan):
    a1 = dict(plan.wells["A1"])
    assert a1["MoS_Libicher_EM_7x_exact_composition1"] == D("4.9335")
    assert a1["MoS_ddH20"] == D("14.801")
    assert dict(plan.wells["B2"]) == {
        "MoS_tSB0146_dead": D("1.73"),
        "MoS_PUREfrex_1_0_Solution_1_2x": D("17.25"),
        "MoS_HBC620": D("0.345"),
        "MoS_ddH20": D("15.180"),
    }
    assert "MoS_pSB0195_sfGFP_pepper" not in dict(plan.wells["B2"])  # empty cell in the sheet
    assert dict(plan.wells["H2"])["MoS_10_formyl_5_6_7_8_tetrahydrofolic_acid_100_g_L"] == D("3.450")
    counts = {well: len(steps) for well, steps in plan.wells.items()}
    assert counts["A1"] == counts["B1"] == 10
    assert counts["C1"] == 18
    assert counts["A2"] == counts["B2"] == 4
    assert plan.well_total_ul("A1") == D("34.5005")  # hand-summed
    assert plan.well_total_ul("A2") == D("34.5")


def test_real_sheet_duplicate_folate_rows_share_one_liquid(plan):
    name = "MoS_10_formyl_5_6_7_8_tetrahydrofolic_acid_1_g_L"
    wells = [w for w, steps in plan.wells.items() if name in dict(steps)]
    assert wells == ["E1", "F1", "G1", "C2"]  # E1 from line 19; F1, G1, C2 from line 26
    assert any("lines 19 and 26" in w for w in plan.warnings)


def test_real_sheet_steps_follow_sheet_row_order(plan):
    # the exact order is checked against the independent oracle below; here: first and last rows
    assert plan.wells["A1"][0][0] == "MoS_tSB0146_dead"
    assert all(steps[-1][0] == "MoS_ddH20" for steps in plan.wells.values())


def test_real_sheet_source_plate(plan, template):
    layout = ib.layout_source_plate(plan, template)
    assert [sw.position for sw in layout.wells] == ib.ALL_WELLS[:30]
    water = [sw for sw in layout.wells if sw.liquid == "MoS_ddH20"]
    assert plan.liquid_total_ul("MoS_ddH20") == D("190.438")  # hand-summed
    assert [sw.volume_ul for sw in water] == [D("63.480")] * 3
    assert [sw.position for sw in water] == ["D4", "E4", "F4"]
    assert dict((sw.liquid, sw.volume_ul) for sw in layout.wells)["MoS_PUREfrex_1_0_Solution_1_2x"] == D("41.625")


def independent_oracle(csv_path, initials):
    """Re-derive {well: [(liquid, µL)]} from the CSV with deliberately simple, separate code."""
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    wells = [f"{h.strip()[-1]}{h.strip()[:-1]}" for h in rows[0][1:]]
    expected = defaultdict(dict)
    for row in rows[1:]:
        name = initials + "_" + re.sub(r"[^A-Za-z0-9]+", "_", row[0]).strip("_")
        for well, cell in zip(wells, row[1:]):
            if cell.strip() and D(cell) != 0:
                expected[well][name] = expected[well].get(name, D(0)) + D(cell)
    return {well: list(v.items()) for well, v in expected.items()}


def test_end_to_end_real_sheet_matches_independent_oracle(tmp_path):
    result = ib.generate(SHEET, "MoS", tmp_path, "real")
    data = result.path.read_bytes()
    assert result.path.name == "real.idotx"
    assert wells_in_file(data) == independent_oracle(SHEET, "MoS")
    contents = ib.read_project(data)
    assert contents.title == "real"
    assert all(not contents.wells[w] for w in ib.ALL_WELLS if w not in result.plan.wells)


# =========================================================================== sheet parsing


def test_semicolon_sheet_with_decimal_commas():
    sheet = ib.parse_sheet(";1A;1B\nWater;1,5;2\n")
    assert sheet.rows[0].volumes == {"A1": D("1.5"), "B1": D("2")}


def test_tab_separated_sheet():
    sheet = ib.parse_sheet("\t1A\t1B\nWater\t1.5\t2\n")
    assert sheet.rows[0].volumes == {"A1": D("1.5"), "B1": D("2")}


def test_letter_first_well_headers():
    sheet = ib.parse_sheet(",A1,B12\nWater,1,2\n")
    assert sheet.rows[0].volumes == {"A1": D(1), "B12": D(2)}


def test_blank_lines_trailing_columns_and_quotes():
    sheet = ib.parse_sheet('\n\n,1A,1B,,\n"Hepes-KOH, pH 8.0",1,,,\n\n,,,,\nWater, 2 ,,,\n')
    assert [(r.name, r.volumes) for r in sheet.rows] == [("Hepes-KOH, pH 8.0", {"A1": D(1)}), ("Water", {"A1": D(2)})]


def test_zeros_are_skipped_and_all_zero_rows_dropped():
    sheet = ib.parse_sheet(",1A,1B\nWater,0,1\nNothing,0,0.000\n")
    assert [(r.name, r.volumes) for r in sheet.rows] == [("Water", {"B1": D(1)})]
    assert any("Nothing" in w for w in sheet.warnings)


def test_names_are_whitespace_tidied():
    sheet = ib.parse_sheet(",1A\n  tRNA   mix  ,1\n")
    assert sheet.rows[0].name == "tRNA mix"


@pytest.mark.parametrize(
    "text, message",
    [
        ("", "empty"),
        ("\n , , \n", "empty"),
        (",1A\nWater,-1\n", "negative"),
        (",1A\nWater,abc\n", 'Line 2 ("Water"), well A1'),
        (",1A\nWater,1.725 uL\n", "not a number"),
        (",1A\nWater,1.2.3\n", "not a number"),
        (",1A\nWater,nan\n", "not a number"),
        (",1A\nWater,\"1,234.5\"\n", "not a number"),
        (",1A,A1\nWater,1,2\n", "appears twice"),
        (",1A,13A\nWater,1,2\n", "not on a 96-well plate"),
        (",1A,foo\nWater,1,2\n", "not a well name"),
        ("Liquid,,\nWater,1,2\n", "no well names"),
        (",1A\nWater,1,2\n", "column without a well name"),
        (",1A\n,1\n", "no liquid name"),
        (",1A\nTotal,10\n", "summary rows"),
    ],
)
def test_bad_sheets_are_rejected_with_a_useful_message(text, message):
    with pytest.raises(ib.SheetError, match=re.escape(message)):
        ib.parse_sheet(text)


def test_sheet_without_any_volume_is_rejected():
    with pytest.raises(ib.SheetError, match="no volumes"):
        ib.make_plan(ib.parse_sheet(",1A\nWater,\n"), "MoS")


def test_cp1252_sheet_from_excel(tmp_path):
    path = tmp_path / "s.csv"
    path.write_bytes(",1A\n5 µM ATP,1\n".encode("cp1252"))
    plan = ib.make_plan(ib.read_sheet(path), "MoS")
    assert list(plan.liquids) == ["MoS_5_uM_ATP"]


def test_utf16_sheet(tmp_path):
    path = tmp_path / "s.csv"
    path.write_bytes("\t1A\nWater\t1\n".encode("utf-16"))
    assert ib.read_sheet(path).rows[0].volumes == {"A1": D(1)}


def test_missing_sheet_file_gives_sheet_error(tmp_path):
    with pytest.raises(ib.SheetError, match="Cannot read"):
        ib.read_sheet(tmp_path / "nope.csv")


def test_same_liquid_twice_in_one_well_is_summed_with_a_warning():
    plan = ib.make_plan(ib.parse_sheet(",1A,1B\nATP,1,\nWater,5,5\nATP,0.5,2\n"), "MoS")
    assert plan.wells["A1"] == [("MoS_ATP", D("1.5")), ("MoS_Water", D(5))]
    assert plan.wells["B1"] == [("MoS_Water", D(5)), ("MoS_ATP", D(2))]
    assert any("added up" in w and "A1" in w for w in plan.warnings)


@pytest.mark.parametrize("a, b", [("Hepes-KOH pH8", "Hepes KOH pH8"), ("ATP", "atp"), ("Mg (1 M)", "Mg 1 M")])
def test_different_names_that_collide_are_rejected(a, b):
    with pytest.raises(ib.SheetError, match="would both become"):
        ib.make_plan(ib.parse_sheet(f',1A\n"{a}",1\n"{b}",2\n'), "MoS")


def test_suspiciously_large_well_total_warns():
    plan = ib.make_plan(ib.parse_sheet(",1A\nWater,345\n"), "MoS")
    assert any("really in µL" in w for w in plan.warnings)


# =========================================================================== source plate


@pytest.mark.parametrize(
    "total, expected",
    [
        ("1", ["1"]),
        ("76.19", ["76.19"]),  # 76.19 * 1.05 = 79.9995 fits
        ("76.191", ["38.096", "38.096"]),  # 80.00055 does not
        ("160", ["53.334"] * 3),
        ("190.438", ["63.480"] * 3),
        ("228.571", ["57.143"] * 4),  # 3 x 76.190333 would round up to 76.191, which no longer fits
    ],
)
def test_split_volume(total, expected):
    assert ib.split_volume(D(total), D(80)) == [D(e) for e in expected]


def test_split_volume_properties():
    rng = random.Random(3)
    for _ in range(5000):
        total = D(rng.randint(1, 2_000_000)).scaleb(-3)
        shares = ib.split_volume(total, D(80))
        n = len(shares)
        assert len(set(shares)) == 1
        assert total <= sum(shares) <= total + n * ib.ONE_NL
        assert all(s * D("1.05") <= 80 for s in shares)
        if n > 1:  # one well fewer would not have worked
            fewer = (total / (n - 1)).quantize(ib.ONE_NL, rounding="ROUND_CEILING") if n > 2 else total
            assert fewer * D("1.05") > 80


def test_source_plate_runs_out_of_wells(template):
    csv_text = ",1A\n" + "".join(f"L{i},1\n" for i in range(97))
    plan = ib.make_plan(ib.parse_sheet(csv_text), "MoS")
    with pytest.raises(ib.IdotError, match="do not fit"):
        ib.layout_source_plate(plan, template)


def test_96_liquids_fill_the_source_plate_exactly(template, tmp_path):
    csv_text = ",1A\n" + "".join(f"L{i},0.1\n" for i in range(96))
    plan, layout, data = build(template, csv_text)
    assert sorted(sw.position for sw in layout.wells) == sorted(ib.ALL_WELLS)
    ib.verify_output(data, template, plan.wells, layout.wells, "test")


# =========================================================================== template and file format


def test_template_is_read_correctly(template):
    assert template.bom and template.newline == "\r\n"
    assert template.assay_volume == "1.5E-05"
    assert template.max_source_well_ul == D(80)
    assert template.source_plate_type == "S_100"
    assert template.wells == ib.ALL_WELLS


def test_rebuilding_the_template_wells_reproduces_the_template_byte_for_byte(template):
    """The hard-coded stanza, the indentation and the number formats are exactly the iDOT software's."""
    wells = template_wells()
    assert list(wells) == ["A1", "B1", "C1"]
    text = ib.build_text(template, wells, [], "iDOT_template")

    def target_plate(t):
        return t[t.index("<TargetPlates>"): t.index("</TargetPlates>")]

    assert target_plate(text) == target_plate(template.text)


def test_source_lines_are_written_exactly_like_the_template(template):
    lines = re.findall(r"^ *<SourceLiquid Position=.*$", template_text(), re.M)
    assert len(lines) == 17
    for line in lines:
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', line))
        sw = ib.SourceWell(attrs["Position"], attrs["Liquid"], ib.liters_to_ul(attrs["Volume"]))
        assert ib.render_source_wells([sw], "      ").splitlines()[1] == line
    positions = [re.search(r'Position="(\w+)"', line).group(1) for line in lines]
    assert positions == sorted(positions, key=ib._row_major_key)  # the order the software uses


def test_output_keeps_encoding_line_endings_and_ending(tmp_path):
    data = ib.generate(SHEET, "MoS", tmp_path, "x").path.read_bytes()
    raw = TEMPLATE.read_bytes()
    assert data.startswith(codecs.BOM_UTF8) and raw.startswith(codecs.BOM_UTF8)
    body = data[3:].decode("utf-8")
    assert "\r\n" in body and not re.search(r"(?<!\r)\n", body) and "\r\r" not in body
    assert data.endswith(b"</IdotProjectFile>") and raw.endswith(b"</IdotProjectFile>")
    assert data.startswith(raw[: raw.index(b"Title=")])  # same XML declaration and root element


def test_unused_template_wells_are_emptied(template):
    plan, layout, data = build(template, ",5D\nWater,1\n")
    assert wells_in_file(data) == {"D5": [("MoS_Water", D(1))]}
    text = data.decode("utf-8-sig")
    a1 = text[text.index('Identifier="A1"'): text.index('Identifier="B1"')]
    assert "<ChildProtocolSteps />" in a1 and "MoS_Libicher" not in text


def test_everything_outside_the_edited_regions_is_unchanged(template):
    _, _, data = build(template, ",1A\nWater,1\n")
    out = data.decode("utf-8-sig").replace("\r\n", "\n")
    assert ib._mask_edited_regions(out) == ib._mask_edited_regions(template.text)
    tail = template.text[template.text.index("</LiquidContent>"):]
    assert out.endswith(tail)  # ProtocolSettings etc. byte for byte


def test_title_is_set_and_escaped(template):
    _, _, data = build(template, ",1A\nWater,1\n", title='Run & "test" <1>')
    assert ib.read_project(data).title == 'Run & "test" <1>'
    assert b'Title="Run &amp; &quot;test&quot; &lt;1&gt;"' in data


def test_unsafe_liquid_names_are_refused_by_the_writer(template):
    for bad in ["MoS_<x>", "Mo S", "MoS&", ""]:
        with pytest.raises(ib.IdotError):
            ib.build_text(template, {"A1": [(bad, D(1))]}, [ib.SourceWell("A1", "MoS_x", D(1))], "t")


def test_well_missing_from_template_is_reported(template):
    text = re.sub(r'\s*<WellgroupProtocolStep Identifier="H12">.*?</WellgroupProtocolStep>', "", template.text, flags=re.S)
    small = ib.parse_template(codecs.BOM_UTF8 + text.encode("utf-8"))
    assert "H12" not in small.wells
    with pytest.raises(ib.TemplateError, match="H12"):
        ib.build_text(small, {"H12": [("MoS_x", D(1))]}, [], "t")


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda t: "hello", "not valid XML"),
        (lambda t: t.replace("<IdotProjectFile ", "<Other ").replace("</IdotProjectFile>", "</Other>"), "not an iDOT"),
        (lambda t: t.replace("</TargetPlates>", t[t.index("<TargetPlate "): t.index("</TargetPlates>")] + "</TargetPlates>"), "exactly one"),
        (lambda t: t.replace('<EndPosition X="0" Y="0" />', '<EndPosition X="1" Y="0" />', 1), "not a single well"),
        (lambda t: t.replace('Identifier="A1"', 'Identifier="Group 1"', 1), "not a single well"),
        (lambda t: t.replace("<AssayVolume>1.5E-05</AssayVolume>\n      <WasteWell", "<AssayVolume>x</AssayVolume>\n      <WasteWell"), "AssayVolume"),
    ],
)
def test_unusable_templates_are_rejected(mutate, message):
    with pytest.raises(ib.TemplateError, match=message):
        ib.parse_template(codecs.BOM_UTF8 + mutate(template_text()).encode("utf-8"))


def test_template_without_bom_or_crlf_is_reproduced_as_is(tmp_path):
    raw = template_text().encode("utf-8")  # LF, no BOM
    path = tmp_path / "lf_template.idotx"
    path.write_bytes(raw)
    data = ib.generate(SHEET, "MoS", tmp_path, "y", template_path=path).path.read_bytes()
    assert not data.startswith(codecs.BOM_UTF8) and b"\r" not in data


# =========================================================================== self-check


TAMPERS = {
    "volume": lambda t: t.replace("<Volume>1.725E-06</Volume>", "<Volume>1.726E-06</Volume>", 1),
    "liquid name": lambda t: t.replace("<SourceLiquid>MoS_ddH20<", "<SourceLiquid>MoS_ddH2O<", 1),
    "dropped step": lambda t: re.sub(r"<MixingProtocolStep .*?</MixingProtocolStep>\r\n\s*", "", t, count=1, flags=re.S),
    "step setting": lambda t: t.replace("<VolumeConcentration>Volume<", "<VolumeConcentration>Concentration<", 1),
    "dropped source well": lambda t: re.sub(r'<SourceLiquid Position="A1"[^>]*/>\r\n\s*', "", t, count=1),
    "source volume": lambda t: re.sub(r'(Position="D4" Volume=")[^"]+', r"\g<1>6E-05", t, count=1),
    "additional volume": lambda t: re.sub(r'(AdditionalVolume=")[^"]+', r"\g<1>1E-09", t, count=1),
    "overfilled source": lambda t: re.sub(r'(Position="D4" Volume=")[^"]+(" AdditionalVolume=")[^"]+', r"\g<1>7.9E-05\g<2>3.95E-06", t, count=1),
    "outside region": lambda t: t.replace("<DispenseToWasteCycles>3<", "<DispenseToWasteCycles>4<"),
    "title": lambda t: t.replace('Title="test"', 'Title="other"'),
    "placeholder": lambda t: t.replace("<SourceLiquid>MoS_ATP<", "<SourceLiquid>{source_liquid}<", 1),
    "truncated": lambda t: t[: len(t) // 2],
    "LF endings": lambda t: t.replace("\r\n", "\n"),
}


@pytest.mark.parametrize("kind", list(TAMPERS))
def test_self_check_catches_tampering(template, kind):
    plan, layout, data = build(template, SHEET.read_text(encoding="utf-8-sig"))
    ib.verify_output(data, template, plan.wells, layout.wells, "test")  # the untouched file passes
    tampered = codecs.BOM_UTF8 + TAMPERS[kind](data[3:].decode("utf-8")).encode("utf-8")
    assert tampered != data
    with pytest.raises(ib.VerificationError):
        ib.verify_output(tampered, template, plan.wells, layout.wells, "test")


def test_self_check_catches_missing_bom(template):
    plan, layout, data = build(template, ",1A\nWater,1\n")
    with pytest.raises(ib.VerificationError, match="byte-order mark"):
        ib.verify_output(data[3:], template, plan.wells, layout.wells, "test")


# =========================================================================== random sheets

NAME_POOL = [raw for raw, _ in SHEET_NAMES[:16]] + ["Water", "GTP", "CTP", "creatine phosphate"]


@pytest.mark.parametrize("seed", range(150))
def test_random_sheets_round_trip(template, seed):
    rng = random.Random(seed)
    wells = rng.sample(ib.ALL_WELLS, rng.randint(1, 96))
    names = rng.sample(NAME_POOL, rng.randint(1, 12))
    delimiter = rng.choice([",", ";"])
    letter_first = rng.random() < 0.5
    initials = rng.choice(["MoS", "AB", "x1"])

    header = [""] + [w if letter_first else f"{w[1:]}{w[0]}" for w in wells]
    lines = [delimiter.join(header)]
    expected = defaultdict(list)
    for name in names:
        cells = [f'"{name}"']
        for well in wells:
            if rng.random() < 0.5:
                cells.append("")
                continue
            ul = D(rng.randint(1, 5_000)).scaleb(-rng.randint(3, 4))  # 0.0001 .. 5 µL
            text = str(ul)
            if delimiter == ";":
                text = text.replace(".", ",")
            cells.append(text)
            expected[well].append((ib.liquid_name(initials, name), ul))
        lines.append(delimiter.join(cells))
    if not expected:
        return
    csv_text = "\n".join(lines) + "\n"

    plan, layout, data = build(template, csv_text, initials=initials)
    ib.verify_output(data, template, plan.wells, layout.wells, "test")
    order = {w: i for i, w in enumerate(ib.ALL_WELLS)}
    assert wells_in_file(data) == dict(sorted(expected.items(), key=lambda kv: order[kv[0]]))
    for liquid in plan.liquids:
        held = sum((sw.volume_ul for sw in layout.wells if sw.liquid == liquid), D(0))
        need = plan.liquid_total_ul(liquid)
        assert need <= held <= need + 96 * ib.ONE_NL


# =========================================================================== top level


@pytest.mark.parametrize(
    "name, expected",
    [
        ("plate", "plate.idotx"),
        ("plate.idotx", "plate.idotx"),
        ("plate.IDOTX", "plate.idotx"),
        ("plate.txt", "plate.txt.idotx"),
        ("  my run 3  ", "my run 3.idotx"),
        ("a/b:c", "a_b_c.idotx"),
        ("run.", "run.idotx"),
    ],
)
def test_output_filename(name, expected):
    assert ib.output_filename(name) == expected


@pytest.mark.parametrize("name", ["", "   ", ".idotx", "..."])
def test_output_filename_rejects_empty(name):
    with pytest.raises(ib.IdotError):
        ib.output_filename(name)


def test_default_output_name():
    from datetime import date

    assert ib.default_output_name("C:/x/iDOT_pipetting_sheet.csv", "MoS", date(2026, 10, 20)) == (
        "iDOT_pipetting_sheet_MoS_20261020"
    )
    assert ib.default_output_name("sheet.csv", "", date(2026, 1, 2)) == "sheet_20260102"


def test_generate_refuses_to_overwrite_unless_asked(tmp_path):
    first = ib.generate(SHEET, "MoS", tmp_path, "run").path
    before = first.read_bytes()
    with pytest.raises(ib.IdotError, match="already exists"):
        ib.generate(SHEET, "MoS", tmp_path, "run")
    ib.generate(SHEET, "AB", tmp_path, "run", overwrite=True)
    assert first.read_bytes() != before and b"AB_ddH20" in first.read_bytes()


def test_generate_checks_inputs(tmp_path):
    with pytest.raises(ib.IdotError, match="does not exist"):
        ib.generate(SHEET, "MoS", tmp_path / "missing")
    with pytest.raises(ib.IdotError, match="Initials"):
        ib.generate(SHEET, "M S", tmp_path)
    with pytest.raises(ib.TemplateError, match="Cannot read"):
        ib.generate(SHEET, "MoS", tmp_path, template_path=tmp_path / "none.idotx")
    assert list(tmp_path.iterdir()) == []


def test_report_mentions_what_the_user_needs(tmp_path):
    report = ib.generate(SHEET, "MoS", tmp_path, "r").report()
    for text in [
        "28 liquids, 16 wells, 232 dispense steps",
        "Libicher EM (7x|exact composition1)",
        "MoS_Libicher_EM_7x_exact_composition1",
        "D4  MoS_ddH20",
        "split over 3 source wells",
        "Saved and verified",
    ]:
        assert text in report


def test_command_line(tmp_path, capsys):
    assert ib.main([str(SHEET), "--initials", "MoS", "--out-dir", str(tmp_path), "--name", "cli"]) == 0
    assert (tmp_path / "cli.idotx").exists()
    assert ib.main([str(SHEET), "--initials", "MoS", "--out-dir", str(tmp_path), "--name", "cli"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_html_version_embeds_the_unchanged_template():
    import build_html

    html = (ROOT / "iDOT_protocol_builder.html").read_text(encoding="utf-8")
    assert build_html.embedded_template(html) == TEMPLATE.read_bytes()
