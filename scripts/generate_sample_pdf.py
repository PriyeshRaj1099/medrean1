"""Generate a synthetic sample laboratory report PDF for local testing.

The generated file contains only fictional data from the bundled demo
fixtures. It is safe to upload through the normal report upload flow.

Usage:
    python scripts/generate_sample_pdf.py --kind cbc --out sample_report.pdf
    python scripts/generate_sample_pdf.py --kind lipid   # prints default path
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.demo_reports import DEMO_REPORTS  # noqa: E402


def build_pdf(text: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("helvetica", size=11)
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line and all(ord(ch) < 256 for ch in line):
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 6, line)
        elif line:
            # Core fonts are latin-1 only; transliterate what we can.
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 6, line.encode("latin-1", "replace").decode("latin-1"))
    return bytes(pdf.output())


def main(args: argparse.Namespace) -> int:
    fixture = DEMO_REPORTS.get(args.kind)
    if fixture is None:
        options = ", ".join(sorted(DEMO_REPORTS))
        print(f"Unknown kind '{args.kind}'. Available kinds: {options}")
        return 1
    out_path = Path(args.out) if args.out else PROJECT_ROOT / fixture.filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(build_pdf(fixture.text))
    print(f"Wrote synthetic {fixture.title} PDF ({out_path.stat().st_size} bytes) to {out_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a synthetic sample report PDF")
    parser.add_argument("--kind", default="cbc", help="fixture kind (cbc, lipid, thyroid, metabolic)")
    parser.add_argument("--out", default=None, help="output file path")
    raise SystemExit(main(parser.parse_args()))
