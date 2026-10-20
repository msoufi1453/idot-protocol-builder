# iDOT protocol builder

Turns a pipetting sheet (CSV) into an iDOT project file (`.idotx`), starting from `iDOT_template.idotx`.

## Use

**Any computer (Windows, Mac, Linux):** open `iDOT_protocol_builder.html` in a web browser (double-click it).
It needs no installation and no internet; the template is built into the page and nothing is uploaded.
That one file is all you need to share. In Chrome or Edge you choose the folder when saving; other browsers
save to the Downloads folder. It produces exactly the same files as the Python version.

**Python version (Windows):** double-click `Start_iDOT_GUI.bat` (or run `python idot_gui.py`), then:

1. **Pipetting sheet**: header row `,1A,1B,...` (column number + row letter; `A1` style works too),
   then one row per liquid with volumes in µL. Empty cells and zeros are skipped.
2. **Save in folder**, **your initials** (e.g. `MoS`), and press **Generate .idotx**.

Liquid names become `<initials>_<name>` with every run of special characters replaced by `_`:
`Hepes-KOH, pH 8.0` → `MoS_Hepes_KOH_pH_8_0`. The log shows the full name mapping, what to load in each
source well, and the total volume per target well.

Every file is built from `iDOT_template.idotx` in this folder. Keep that file here and do not edit it.

### Opening the file in the iDOT software: liquid classes

When a liquid name is new to the iDOT liquid library, the software asks you to pick a **liquid class**
for it. Choose the class that matches the liquid (**for water and water-like buffers it is 0.95**), then
let the software save the liquid. The next time you use the same name, it won't ask again.

Command line: `python idot_builder.py sheet.csv --initials MoS --out-dir <folder>`

## What the tool changes in the template

Only three things; every other byte (UTF-8 with BOM, CRLF line endings, settings) is copied unchanged:

- each target well's steps: one `MixingProtocolStep` per liquid (wells not in the sheet are emptied);
- the source plate: one entry per liquid holding the total dispensed volume, placed column by column
  (A1, B1, ... H1, A2, ...). A liquid that does not fit in one source well (80 µL including the 5 % extra)
  is split over several wells;
- the project title, set to the file name.

Rows with the same liquid name (e.g. two `10-formyl... [1 g/L]` rows) share one source liquid. Two different
names that would turn into the same iDOT name are refused.

Before a file is reported as saved it is re-read with an XML parser and every well, volume and source well is
checked against the sheet.

## Tests

`python -m pytest tests` runs the automated tests. If `iDOT_template.idotx` ever changes, run
`python build_html.py` to embed the new template in the HTML page. `python make_test_files.py` writes the files for checking
in the iDOT software to `iDOT_test_files/`, with `TEST_PLAN.md` describing what to look for in each.
