"""Synthetic report fixtures for tests.

Every document in this module is completely fictional and clearly labelled as
synthetic. No real patient data is ever used in tests.
"""

from __future__ import annotations

CBC_TEXT = """SYNTHETIC SAMPLE REPORT - FOR TESTING ONLY (NOT A REAL PATIENT)
Complete Blood Count (CBC)
Report Date: 12/08/2026

Test Name                Result      Unit          Reference Range
Hemoglobin               11.2        g/dL          13.0 - 17.0
Total RBC Count          4.30        mill/cumm     4.50 - 5.50
WBC Count                7,200       cells/cumm    4,000 - 11,000
Platelet Count           210,000     cells/cumm    150,000 - 450,000
Hematocrit               35.1        %             40.0 - 50.0
MCV                      82.5        fL            80.0 - 100.0
MCH                      27.4        pg            27.0 - 32.0
MCHC                     31.6        g/dL          32.0 - 36.0
RDW                      13.8        %             11.5 - 14.5
"""

CBC_FOLLOWUP_TEXT = """SYNTHETIC SAMPLE REPORT - FOR TESTING ONLY (NOT A REAL PATIENT)
Complete Blood Count (CBC)
Report Date: 12/11/2026

Test Name                Result      Unit          Reference Range
Hemoglobin               12.4        g/dL          13.0 - 17.0
Total RBC Count          4.50        mill/cumm     4.50 - 5.50
WBC Count                6,900       cells/cumm    4,000 - 11,000
Platelet Count           215,000     cells/cumm    150,000 - 450,000
Hematocrit               38.4        %             40.0 - 50.0
MCV                      82.9        fL            80.0 - 100.0
MCH                      27.6        pg            27.0 - 32.0
MCHC                     31.9        g/dL          32.0 - 36.0
RDW                      13.9        %             11.5 - 14.5
"""

LIPID_TEXT = """SYNTHETIC SAMPLE REPORT - FOR TESTING ONLY (NOT A REAL PATIENT)
Lipid Profile (Fasting)
Report Date: 05/09/2026

Test Name                Result      Unit          Reference Range
Total Cholesterol        224         mg/dL         < 200
Triglycerides            168         mg/dL         < 150
HDL Cholesterol          38          mg/dL         40 - 60
LDL Cholesterol          148         mg/dL         < 100
"""


def build_pdf_bytes(text: str) -> bytes:
    """Render synthetic report text into a real, uploadable PDF."""
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
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 6, line.encode("latin-1", "replace").decode("latin-1"))
    return bytes(pdf.output())
