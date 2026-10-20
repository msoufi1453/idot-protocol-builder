"""Build iDOT protocol files (.idotx) from a pipetting sheet (CSV).

An .idotx file is UTF-8 XML with a byte-order mark and Windows (CRLF) line
endings.  The output is a copy of the template in which exactly three things
are rewritten; every other byte is copied unchanged:

1. ``Title`` on the root element, set to the output file name (the iDOT
   software does the same when it saves a project).
2. The ``<ChildProtocolSteps>`` of every target well: one MixingProtocolStep
   stanza (MIXING_STEP below) per liquid in that well.  Wells the sheet does not
   use are emptied, so the template's example wells never leak into the output.
3. The source plate ``<LiquidContent>``: one SourceLiquid entry per liquid,
   holding the total volume dispensed from it.  A liquid that does not fit in
   one source well is split over several.

Before anything is written, verify_output() re-reads the generated file with an
XML parser and checks every well, every volume and the source plate against the
plan, and that nothing outside the three edited regions changed.

Volumes in the sheet are in µL; the file stores litres, written the way the
iDOT software (.NET) writes doubles: 4.934 µL -> "4.934E-06", 50.5 µL ->
"5.05E-05".  Volumes are kept as Decimal until the moment they are written, so
the µL -> L conversion never picks up floating point noise.

Command line:  python idot_builder.py sheet.csv --initials MoS --out-dir <folder>
"""

from __future__ import annotations

import argparse
import codecs
import csv
import io
import math
import re
import sys
import textwrap
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from xml.sax.saxutils import escape

DEFAULT_TEMPLATE = Path(__file__).resolve().with_name("iDOT_template.idotx")

ROWS = "ABCDEFGH"
COLUMNS = range(1, 13)
# Column-major (A1, B1, ... H1, A2, ...): the order the iDOT software lists wells in.
ALL_WELLS = [f"{row}{col}" for col in COLUMNS for row in ROWS]

# Derived values, reverse-engineered from the template (they reproduce all of its numbers exactly):
#   Concentration    = Volume / AssayVolume * 0.001
#   AdditionalVolume = Volume * 5 / 100       (5 % extra in every source well)
CONCENTRATION_FACTOR = 0.001
ADDITIONAL_PERCENT = 5

ONE_NL = Decimal("0.001")  # in µL
WELL_TOTAL_WARNING_UL = Decimal(300)

# One stanza per liquid per well.  The placeholders are filled in by render_steps().
MIXING_STEP = """\
<MixingProtocolStep Identifier="Constant">
  <SourceLiquid>{source_liquid}</SourceLiquid>
  <VolumeConcentration>Volume</VolumeConcentration>
  <UncoupleAssayVolume>false</UncoupleAssayVolume>
  <AssayVolume>{assay_volume}</AssayVolume>
  <Volume>{volume}</Volume>
  <Concentration>{concentration}</Concentration>
</MixingProtocolStep>
"""
SOURCE_LIQUID = (
    '<SourceLiquid Position="{position}" Volume="{volume}" '
    'AdditionalVolume="{additional}" Liquid="{liquid}" />\n'
)

_STEPS = (
    r"<ChildProtocolSteps\s*/>"
    r"|<ChildProtocolSteps>(?:(?!</ChildProtocolSteps>).)*</ChildProtocolSteps>"
)
_WELL_STEPS_RE = re.compile(
    r'(?P<head><WellgroupProtocolStep Identifier="(?P<well>[^"]*)">\n(?P<indent>[ \t]*))'
    r"(?P<steps>" + _STEPS + r")",
    re.S,
)
_LIQUID_CONTENT_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<content><LiquidContent\s*/>"
    r"|<LiquidContent>(?:(?!</LiquidContent>).)*</LiquidContent>)",
    re.S | re.M,
)
_TITLE_RE = re.compile(r'(<IdotProjectFile\b[^>]*?\sTitle=")([^"]*)(")')
_SAFE_NAME_RE = re.compile(r"[A-Za-z0-9_]+")
_INITIALS_RE = re.compile(r"[A-Za-z0-9]{1,10}")
_NUMBER_RE = re.compile(r"\+?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")
_SUMMARY_ROW_NAMES = {"total", "totals", "sum"}


class IdotError(Exception):
    """A problem the user can fix.  The message is shown as-is in the GUI."""


class SheetError(IdotError):
    pass


class TemplateError(IdotError):
    pass


class VerificationError(IdotError):
    pass


# --------------------------------------------------------------------------- numbers


def format_double(x: float) -> str:
    """Write a double like .NET's round-trip format: 15 significant digits, 17 if needed."""
    if not math.isfinite(x):
        raise ValueError(f"not a finite number: {x!r}")
    text = format(x, ".15G")
    if float(text) != x:
        text = format(x, ".17G")
    return text


def format_volume_ul(ul: Decimal) -> str:
    """µL -> the litre string stored in .idotx files: 4.934 -> '4.934E-06'."""
    return format_double(float(ul.scaleb(-6)))


def liters_to_ul(text: str) -> Decimal:
    """'4.934E-06' -> Decimal('4.934')."""
    return Decimal(text).scaleb(6)


def concentration_str(volume: str, assay_volume: str) -> str:
    return format_double(float(volume) / float(assay_volume) * CONCENTRATION_FACTOR)


def additional_volume_str(volume: str) -> str:
    return format_double(float(volume) * ADDITIONAL_PERCENT / 100)


def fmt_ul(ul: Decimal) -> str:
    """Human-readable µL: Decimal('34.5000') -> '34.5'."""
    return format(ul.normalize(), "f")


# --------------------------------------------------------------------------- names and wells


def sanitize_name(raw: str) -> str:
    """'Hepes-KOH, pH 8.0' -> 'Hepes_KOH_pH_8_0'.  Only A-Z, a-z, 0-9 and single underscores remain."""
    text = raw.replace("µ", "u").replace("μ", "u")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def check_initials(initials: str) -> str:
    initials = initials.strip()
    if not _INITIALS_RE.fullmatch(initials):
        raise IdotError("Initials must be 1-10 letters or digits, without spaces or symbols (e.g. MoS).")
    return initials


def liquid_name(initials: str, raw: str) -> str:
    """('MoS', 'Libicher EM (7x|exact composition1)') -> 'MoS_Libicher_EM_7x_exact_composition1'."""
    core = sanitize_name(raw)
    if not core:
        raise SheetError(f'The liquid name "{raw}" has no letters or digits.')
    return f"{initials}_{core}"


def parse_well(label: str) -> str:
    """'1A' (sheet style) or 'A1' -> 'A1' (iDOT style)."""
    text = re.sub(r"\s+", "", label)
    match = re.fullmatch(r"(\d{1,2})([A-Za-z])", text)
    if match:
        col, row = match.groups()
    else:
        match = re.fullmatch(r"([A-Za-z])(\d{1,2})", text)
        if not match:
            raise SheetError(f'"{label}" is not a well name (expected e.g. 1A or A1).')
        row, col = match.groups()
    row, col = row.upper(), int(col)
    if row not in ROWS or col not in COLUMNS:
        raise SheetError(f'Well "{label}" is not on a 96-well plate (rows A-H, columns 1-12).')
    return f"{row}{col}"


def well_xy(well: str) -> tuple[int, int]:
    """'D7' -> (6, 3): the X (column) and Y (row) the iDOT file uses, both 0-based."""
    return int(well[1:]) - 1, ROWS.index(well[0])


def _row_major_key(well: str) -> tuple[int, int]:
    x, y = well_xy(well)
    return y, x


def _check_safe_name(name: str) -> None:
    if not _SAFE_NAME_RE.fullmatch(name):
        raise IdotError(f'Liquid name "{name}" may only contain letters, digits and _.')


# --------------------------------------------------------------------------- pipetting sheet


@dataclass
class SheetRow:
    line: int  # line number in the CSV, for messages
    name: str  # liquid name as written in the sheet (whitespace tidied)
    volumes: dict[str, Decimal]  # well -> µL, non-zero entries only


@dataclass
class Sheet:
    wells: list[str]  # header order
    rows: list[SheetRow]
    warnings: list[str] = field(default_factory=list)


def decode_text(data: bytes) -> str:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")  # Excel's "CSV (comma delimited)" on Windows


def parse_volume(cell: str, where: str) -> Decimal | None:
    """'1.725' -> Decimal('1.725'); '' or '0' -> None.  A decimal comma ('1,725') is accepted."""
    text = cell.strip().replace(" ", "").replace(" ", "")
    if not text:
        return None
    if text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    if text.startswith("-"):
        raise SheetError(f'{where}: negative volume "{cell.strip()}".')
    if not _NUMBER_RE.fullmatch(text):
        raise SheetError(f'{where}: "{cell.strip()}" is not a number (volumes are in µL, without units).')
    value = Decimal(text)
    return value if value != 0 else None


def read_sheet(path: str | Path) -> Sheet:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise SheetError(f"Cannot read the pipetting sheet {path}: {exc.strerror or exc}") from exc
    return parse_sheet(decode_text(data))


def parse_sheet(text: str) -> Sheet:
    """Header row: an empty cell, then wells (1A, 1B, ...).  Each further row: liquid name, then µL per well."""
    first = next((line for line in text.splitlines() if line.strip(" ,;\t")), None)
    if first is None:
        raise SheetError("The pipetting sheet is empty.")
    delimiter = max([",", ";", "\t"], key=first.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    columns: dict[int, str] | None = None  # cell index -> well
    rows: list[SheetRow] = []
    warnings: list[str] = []
    for record in reader:
        line = reader.line_num
        cells = [cell.strip() for cell in record]
        if not any(cells):
            continue
        if columns is None:
            columns = {}
            for index, label in enumerate(cells[1:], start=1):
                if not label:
                    continue
                try:
                    well = parse_well(label)
                except SheetError as exc:
                    raise SheetError(f"Header (line {line}): {exc}") from None
                if well in columns.values():
                    raise SheetError(f"Header (line {line}): well {well} appears twice.")
                columns[index] = well
            if not columns:
                raise SheetError(f"The header row (line {line}) has no well names (expected 1A, 1B, ...).")
            continue

        name = " ".join(cells[0].split()) if cells else ""
        volumes: dict[str, Decimal] = {}
        for index, cell in enumerate(cells[1:], start=1):
            if not cell:
                continue
            if index not in columns:
                raise SheetError(f'Line {line}: the value "{cell}" is in a column without a well name.')
            volume = parse_volume(cell, f'Line {line} ("{name}"), well {columns[index]}')
            if volume is not None:
                volumes[columns[index]] = volume
        if not name:
            raise SheetError(f"Line {line} has values but no liquid name.")
        if name.lower() in _SUMMARY_ROW_NAMES:
            raise SheetError(f'Line {line} is called "{name}". Remove summary rows from the sheet.')
        if not volumes:
            warnings.append(f'"{name}" (line {line}) has no volumes and was skipped.')
            continue
        rows.append(SheetRow(line, name, volumes))

    if columns is None:
        raise SheetError("The pipetting sheet is empty.")
    return Sheet(wells=list(columns.values()), rows=rows, warnings=warnings)


# --------------------------------------------------------------------------- plan


@dataclass
class Plan:
    liquids: dict[str, str]  # iDOT liquid name -> name in the sheet, in order of first use
    wells: dict[str, list[tuple[str, Decimal]]]  # well -> [(iDOT liquid name, µL)], plate order
    warnings: list[str] = field(default_factory=list)

    def liquid_total_ul(self, liquid: str) -> Decimal:
        return sum((ul for steps in self.wells.values() for name, ul in steps if name == liquid), Decimal(0))

    def well_total_ul(self, well: str) -> Decimal:
        return sum((ul for _, ul in self.wells.get(well, [])), Decimal(0))

    @property
    def n_steps(self) -> int:
        return sum(len(steps) for steps in self.wells.values())


def make_plan(sheet: Sheet, initials: str) -> Plan:
    initials = check_initials(initials)
    seen: dict[str, tuple[str, SheetRow]] = {}  # lower-case iDOT name -> (iDOT name, first row)
    liquids: dict[str, str] = {}
    per_well: dict[str, dict[str, Decimal]] = defaultdict(dict)
    warnings: list[str] = []

    for row in sheet.rows:
        name = liquid_name(initials, row.name)
        key = name.lower()
        if key in seen:
            name, first = seen[key]
            if first.name != row.name:
                raise SheetError(
                    f'Lines {first.line} ("{first.name}") and {row.line} ("{row.name}") would both become '
                    f"the liquid {name}. Rename one of them, or make the names identical if they are the "
                    "same liquid."
                )
            warnings.append(
                f'"{row.name}" is listed on lines {first.line} and {row.line}; '
                f"both rows are dispensed from the same source liquid {name}."
            )
        else:
            seen[key] = (name, row)
            liquids[name] = row.name
        for well, ul in row.volumes.items():
            steps = per_well[well]
            if name in steps:
                warnings.append(
                    f"Well {well} gets {name} from more than one line; the volumes were added up "
                    f"({fmt_ul(steps[name])} + {fmt_ul(ul)} µL)."
                )
                steps[name] += ul
            else:
                steps[name] = ul

    if not liquids:
        raise SheetError("The pipetting sheet contains no volumes.")
    wells = {well: list(per_well[well].items()) for well in ALL_WELLS if well in per_well}
    plan = Plan(liquids=liquids, wells=wells, warnings=warnings)
    for well in wells:
        total = plan.well_total_ul(well)
        if total > WELL_TOTAL_WARNING_UL:
            warnings.append(f"Well {well} adds up to {fmt_ul(total)} µL. Is the sheet really in µL?")
    return plan


# --------------------------------------------------------------------------- source plate


@dataclass(frozen=True)
class SourceWell:
    position: str
    liquid: str
    volume_ul: Decimal

    @property
    def with_extra_ul(self) -> Decimal:
        return self.volume_ul * (100 + ADDITIONAL_PERCENT) / 100


def split_volume(total_ul: Decimal, max_well_ul: Decimal) -> list[Decimal]:
    """Split a liquid over as few source wells as possible, keeping volume + 5 % within each well.

    A liquid that fits in one well keeps its exact volume.  Otherwise the wells
    get equal shares, rounded up to whole nL so the sum is never short.
    """
    factor = Decimal(100 + ADDITIONAL_PERCENT) / 100
    n = max(1, math.ceil(total_ul * factor / max_well_ul))
    while True:
        share = total_ul if n == 1 else (total_ul / n).quantize(ONE_NL, rounding=ROUND_CEILING)
        if share * factor <= max_well_ul:
            return [share] * n
        n += 1


@dataclass
class SourceLayout:
    wells: list[SourceWell]
    warnings: list[str] = field(default_factory=list)


def layout_source_plate(plan: Plan, template: Template) -> SourceLayout:
    """Give every liquid its own source well(s), filling the plate column by column (A1, B1, ... H1, A2, ...)."""
    positions = iter(ALL_WELLS)
    wells: list[SourceWell] = []
    warnings: list[str] = []
    if template.source_plate_type != "S_100":
        warnings.append(
            f'Source plate type "{template.source_plate_type}" has not been tested; '
            "a 96-well layout (A1-H12) was assumed."
        )
    for liquid in plan.liquids:
        total = plan.liquid_total_ul(liquid)
        shares = split_volume(total, template.max_source_well_ul)
        placed = []
        for share in shares:
            position = next(positions, None)
            if position is None:
                raise IdotError("The liquids do not fit on one 96-well source plate.")
            wells.append(SourceWell(position, liquid, share))
            placed.append(position)
        if len(shares) > 1:
            warnings.append(
                f"{liquid} needs {fmt_ul(total)} µL, more than one source well holds "
                f"({fmt_ul(template.max_source_well_ul)} µL including 5 % extra), so it is split over "
                f"{len(shares)} source wells ({', '.join(placed)}) of {fmt_ul(shares[0])} µL each."
            )
    return SourceLayout(wells, warnings)


# --------------------------------------------------------------------------- template


@dataclass
class Template:
    text: str  # without byte-order mark, line endings normalised to \n
    bom: bool
    newline: str
    assay_volume: str  # copied verbatim into every step, e.g. "1.5E-05"
    max_source_well_ul: Decimal
    source_plate_type: str
    wells: list[str]  # target wells, in file order


def load_template(path: str | Path) -> Template:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise TemplateError(f"Cannot read the template {path}: {exc.strerror or exc}") from exc
    return parse_template(data)


def parse_template(data: bytes) -> Template:
    bom = data.startswith(codecs.BOM_UTF8)
    body = data[len(codecs.BOM_UTF8):] if bom else data
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TemplateError("The template is not a UTF-8 .idotx file.") from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")
    if "\r" in text:
        raise TemplateError("The template has mixed line endings.")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise TemplateError(f"The template is not valid XML: {exc}") from exc
    if root.tag != "IdotProjectFile":
        raise TemplateError("The template is not an iDOT project file.")

    plates = root.findall("TargetPlates/TargetPlate")
    sources = root.findall("SourcePlates/SourcePlate")
    if len(plates) != 1 or len(sources) != 1:
        raise TemplateError("The template must have exactly one target plate and one source plate.")
    plate, source = plates[0], sources[0]
    assay_volume = (plate.findtext("AssayVolume") or "").strip()
    max_volume = (source.findtext("MaxSourceWellVolume") or "").strip()
    try:
        float(assay_volume)
        max_source_well_ul = liters_to_ul(max_volume)
    except (ValueError, ArithmeticError):
        raise TemplateError("The template has no valid AssayVolume / MaxSourceWellVolume.") from None

    wells = []
    for step in plate.findall("ProtocolSteps/WellgroupProtocolStep"):
        ident = step.get("Identifier", "")
        cells = step.findall("CellGroupList/CellGroup")
        try:
            well = parse_well(ident)
        except SheetError:
            well = None
        xy = None
        if len(cells) == 1:
            begin, end = cells[0].find("BeginPosition"), cells[0].find("EndPosition")
            if begin is not None and end is not None and begin.attrib == end.attrib:
                xy = (int(begin.get("X", -1)), int(begin.get("Y", -1)))
        if well != ident or xy != well_xy(ident):
            raise TemplateError(f'Template well group "{ident}" is not a single well; use an unmodified template.')
        wells.append(well)
    if [m.group("well") for m in _WELL_STEPS_RE.finditer(text)] != wells:
        raise TemplateError("The template's well layout is not in the expected format.")
    if len(_LIQUID_CONTENT_RE.findall(text)) != 1 or len(_TITLE_RE.findall(text)) != 1:
        raise TemplateError("The template's source plate or title is not in the expected format.")
    return Template(
        text=text,
        bom=bom,
        newline=newline,
        assay_volume=assay_volume,
        max_source_well_ul=max_source_well_ul,
        source_plate_type=(source.findtext("PlateType") or "").strip(),
        wells=wells,
    )


# --------------------------------------------------------------------------- building the file


def render_steps(steps: list[tuple[str, Decimal]], indent: str, assay_volume: str) -> str:
    if not steps:
        return "<ChildProtocolSteps />"
    parts = ["<ChildProtocolSteps>\n"]
    for liquid, ul in steps:
        _check_safe_name(liquid)
        volume = format_volume_ul(ul)
        stanza = MIXING_STEP.format(
            source_liquid=liquid,
            assay_volume=assay_volume,
            volume=volume,
            concentration=concentration_str(volume, assay_volume),
        )
        parts.append(textwrap.indent(stanza, indent + "  "))
    parts.append(indent + "</ChildProtocolSteps>")
    return "".join(parts)


def render_source_wells(source_wells: list[SourceWell], indent: str) -> str:
    if not source_wells:
        return "<LiquidContent />"
    parts = ["<LiquidContent>\n"]
    for sw in sorted(source_wells, key=lambda s: _row_major_key(s.position)):  # same order as the iDOT software
        _check_safe_name(sw.liquid)
        volume = format_volume_ul(sw.volume_ul)
        parts.append(
            indent
            + "  "
            + SOURCE_LIQUID.format(
                position=sw.position, volume=volume, additional=additional_volume_str(volume), liquid=sw.liquid
            )
        )
    parts.append(indent + "</LiquidContent>")
    return "".join(parts)


def build_text(
    template: Template,
    wells: dict[str, list[tuple[str, Decimal]]],
    source_wells: list[SourceWell],
    title: str,
) -> str:
    missing = sorted(set(wells) - set(template.wells), key=ALL_WELLS.index)
    if missing:
        raise TemplateError(f"The template has no well {', '.join(missing)}.")

    def fill_well(m: re.Match) -> str:
        return m.group("head") + render_steps(wells.get(m.group("well"), []), m.group("indent"), template.assay_volume)

    text = _WELL_STEPS_RE.sub(fill_well, template.text)
    text = _LIQUID_CONTENT_RE.sub(
        lambda m: m.group("indent") + render_source_wells(source_wells, m.group("indent")), text, count=1
    )
    return _TITLE_RE.sub(lambda m: m.group(1) + escape(title, {'"': "&quot;"}) + m.group(3), text, count=1)


def encode(template: Template, text: str) -> bytes:
    data = text.replace("\n", template.newline).encode("utf-8")
    return codecs.BOM_UTF8 + data if template.bom else data


# --------------------------------------------------------------------------- reading back / verification


@dataclass
class ProjectContents:
    title: str
    wells: dict[str, list[dict[str, str]]]  # well -> steps, each step = {child tag: text}
    source: list[dict[str, str]]  # SourceLiquid attributes


def read_project(data: bytes) -> ProjectContents:
    """Parse an .idotx file with a real XML parser, independently of how it was built."""
    body = data[len(codecs.BOM_UTF8):] if data.startswith(codecs.BOM_UTF8) else data
    root = ET.fromstring(body)
    wells = {}
    for step in root.iterfind("TargetPlates/TargetPlate/ProtocolSteps/WellgroupProtocolStep"):
        wells[step.get("Identifier")] = [
            {child.tag: (child.text or "") for child in mixing}
            for mixing in step.iterfind("ChildProtocolSteps/MixingProtocolStep")
        ]
    source = [dict(e.attrib) for e in root.iterfind("SourcePlates/SourcePlate/LiquidContent/SourceLiquid")]
    return ProjectContents(title=root.get("Title", ""), wells=wells, source=source)


def _mask_edited_regions(text: str) -> str:
    text = _WELL_STEPS_RE.sub(lambda m: m.group("head") + "<<STEPS>>", text)
    text = _LIQUID_CONTENT_RE.sub(lambda m: m.group("indent") + "<<SOURCE>>", text, count=1)
    return _TITLE_RE.sub(lambda m: m.group(1) + "<<TITLE>>" + m.group(3), text, count=1)


def verify_output(
    data: bytes,
    template: Template,
    wells: dict[str, list[tuple[str, Decimal]]],
    source_wells: list[SourceWell],
    title: str,
) -> None:
    """Raise VerificationError unless `data` is exactly the intended file."""
    problems: list[str] = []
    if data.startswith(codecs.BOM_UTF8) != template.bom:
        problems.append("byte-order mark differs from the template")
    body = data[len(codecs.BOM_UTF8):] if data.startswith(codecs.BOM_UTF8) else data
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise VerificationError("The generated file is not valid UTF-8.") from None
    if template.newline == "\r\n" and re.search(r"(?<!\r)\n", text):
        problems.append("line endings are not all CRLF")
    normalised = text.replace("\r\n", "\n")
    if re.search(r"\{(source_liquid|assay_volume|volume|concentration)\}", normalised):
        problems.append("an unfilled placeholder is left in the file")
    if _mask_edited_regions(normalised) != _mask_edited_regions(template.text):
        problems.append("parts of the file outside the wells and source plate differ from the template")
    try:
        contents = read_project(data)
    except ET.ParseError as exc:
        raise VerificationError(f"The generated file is not valid XML: {exc}") from None

    if contents.title != title:
        problems.append(f"title is {contents.title!r}, expected {title!r}")
    if list(contents.wells) != template.wells:
        problems.append("the list of target wells differs from the template")
    for well in template.wells:
        expected = [(liquid, float(ul.scaleb(-6))) for liquid, ul in wells.get(well, [])]
        steps = contents.wells.get(well, [])
        actual = [(s.get("SourceLiquid"), float(s.get("Volume", "nan"))) for s in steps]
        if actual != expected:
            problems.append(f"well {well}: file has {actual}, expected {expected}")
        for s in steps:
            if (s.get("VolumeConcentration"), s.get("UncoupleAssayVolume"), s.get("AssayVolume")) != (
                "Volume",
                "false",
                template.assay_volume,
            ):
                problems.append(f"well {well}: unexpected step settings {s}")

    expected_source = sorted((sw.position, sw.liquid, float(sw.volume_ul.scaleb(-6))) for sw in source_wells)
    actual_source = sorted((e.get("Position"), e.get("Liquid"), float(e.get("Volume", "nan"))) for e in contents.source)
    if actual_source != expected_source:
        problems.append(f"source plate: file has {actual_source}, expected {expected_source}")
    positions = [e.get("Position") for e in contents.source]
    if len(set(positions)) != len(positions):
        problems.append("a source well is used twice")
    max_l = float(template.max_source_well_ul.scaleb(-6))
    provided: dict[str, float] = defaultdict(float)
    for e in contents.source:
        volume, extra = float(e["Volume"]), float(e["AdditionalVolume"])
        provided[e["Liquid"]] += volume
        if extra != float(additional_volume_str(e["Volume"])):
            problems.append(f"source well {e['Position']}: AdditionalVolume is not 5 % of Volume")
        if volume + extra > max_l * (1 + 1e-12):
            problems.append(f"source well {e['Position']} is overfilled")
    required: dict[str, float] = defaultdict(float)
    for steps in contents.wells.values():
        for s in steps:
            required[s["SourceLiquid"]] += float(s["Volume"])
    for liquid, need in required.items():
        if provided.get(liquid, 0.0) < need * (1 - 1e-12):
            problems.append(f"source plate holds too little {liquid}")
    for liquid in set(provided) - set(required):
        problems.append(f"source plate contains {liquid}, which no well uses")

    if problems:
        raise VerificationError("The generated file failed its self-check:\n- " + "\n- ".join(problems))


def write_project(
    path: Path,
    template: Template,
    wells: dict[str, list[tuple[str, Decimal]]],
    source_wells: list[SourceWell],
    overwrite: bool = False,
) -> bytes:
    """Build, verify, write and re-read one .idotx file."""
    path = Path(path)
    if path.suffix.lower() != ".idotx":
        raise IdotError(f"{path.name}: the output must be an .idotx file.")
    title = path.stem
    data = encode(template, build_text(template, wells, source_wells, title))
    verify_output(data, template, wells, source_wells, title)
    if path.exists() and not overwrite:
        raise IdotError(f"{path} already exists.")
    try:
        path.write_bytes(data)
        written = path.read_bytes()
    except OSError as exc:
        raise IdotError(f"Cannot write {path}: {exc.strerror or exc} (is it open in another program?)") from exc
    if written != data:
        raise VerificationError(f"{path} was not written correctly; please try again.")
    return data


# --------------------------------------------------------------------------- top level


def output_filename(name: str) -> str:
    """'plate 3' -> 'plate 3.idotx'.  The extension is always .idotx, never .txt."""
    name = name.strip()
    if name.lower().endswith(".idotx"):
        name = name[: -len(".idotx")]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(".")
    if not name:
        raise IdotError("Enter a file name.")
    return name + ".idotx"


def default_output_name(csv_path: str | Path, initials: str, today: date | None = None) -> str:
    parts = [Path(csv_path).stem, initials.strip(), (today or date.today()).strftime("%Y%m%d")]
    return "_".join(p for p in parts if p)


@dataclass
class Result:
    path: Path
    sheet: Sheet
    plan: Plan
    source: SourceLayout
    template: Template

    @property
    def warnings(self) -> list[str]:
        return self.sheet.warnings + self.plan.warnings + self.source.warnings

    def report(self) -> str:
        plan = self.plan
        width = max(len(n) for n in plan.liquids.values())
        lines = [
            f"{len(plan.liquids)} liquids, {len(plan.wells)} wells, {plan.n_steps} dispense steps.",
            "",
            "Liquid names (sheet -> iDOT):",
        ]
        lines += [f"  {sheet_name:<{width}}  ->  {name}" for name, sheet_name in plan.liquids.items()]
        lines += ["", f"Source plate ({self.template.source_plate_type}), column by column:"]
        name_width = max(len(sw.liquid) for sw in self.source.wells)
        for sw in self.source.wells:
            with_extra = sw.with_extra_ul.quantize(ONE_NL, rounding=ROUND_CEILING)
            lines.append(
                f"  {sw.position:<4}{sw.liquid:<{name_width}}  {fmt_ul(sw.volume_ul):>9} µL"
                f"   (with 5 % extra: {fmt_ul(with_extra)} µL)"
            )
        lines += ["", "Target wells:"]
        for well, steps in plan.wells.items():
            lines.append(f"  {well:<4}{len(steps):>3} liquids  {fmt_ul(plan.well_total_ul(well)):>9} µL")
        if self.warnings:
            lines += ["", "Warnings:"] + [f"  - {w}" for w in self.warnings]
        lines += ["", f"Saved and verified: {self.path}"]
        return "\n".join(lines)


def generate(
    csv_path: str | Path,
    initials: str,
    out_dir: str | Path,
    filename: str | None = None,
    template_path: str | Path | None = None,
    overwrite: bool = False,
) -> Result:
    """Pipetting sheet -> verified .idotx file in out_dir."""
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        raise IdotError(f"The output folder does not exist: {out_dir}")
    initials = check_initials(initials)
    template = load_template(template_path or DEFAULT_TEMPLATE)
    sheet = read_sheet(csv_path)
    plan = make_plan(sheet, initials)
    source = layout_source_plate(plan, template)
    path = out_dir / output_filename(filename or default_output_name(csv_path, initials))
    write_project(path, template, plan.wells, source.wells, overwrite=overwrite)
    return Result(path=path, sheet=sheet, plan=plan, source=source, template=template)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an iDOT .idotx protocol from a pipetting sheet (CSV).")
    parser.add_argument("csv", help="pipetting sheet: header 1A,1B,...; one row per liquid; volumes in µL")
    parser.add_argument("--initials", required=True, help="prefix for liquid names, e.g. MoS")
    parser.add_argument("--out-dir", default=".", help="folder to save the .idotx file in")
    parser.add_argument("--name", help="output file name (default: <sheet>_<initials>_<date>)")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="template .idotx file")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing file")
    args = parser.parse_args(argv)
    try:
        result = generate(args.csv, args.initials, args.out_dir, args.name, args.template, args.overwrite)
    except IdotError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(result.report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
