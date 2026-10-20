"""Embed iDOT_template.idotx into iDOT_protocol_builder.html (run again only if the template changes)."""

import base64
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
HTML = HERE / "iDOT_protocol_builder.html"
TEMPLATE = HERE / "iDOT_template.idotx"
_LINE = re.compile(r'const TEMPLATE_B64 = "[^"]*";')


def embedded_template(html: str) -> bytes:
    return base64.b64decode(re.search(r'const TEMPLATE_B64 = "([^"]*)";', html).group(1))


def main() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert len(_LINE.findall(html)) == 1, "template line not found"
    encoded = base64.b64encode(TEMPLATE.read_bytes()).decode("ascii")
    HTML.write_text(_LINE.sub(lambda _: f'const TEMPLATE_B64 = "{encoded}";', html), encoding="utf-8", newline="\n")
    print(f"embedded {TEMPLATE.name} ({TEMPLATE.stat().st_size} bytes) into {HTML.name}")


if __name__ == "__main__":
    main()
