"""Synthetic demo reports.

These are completely fictional laboratory reports created for demonstration and
testing. They do not belong to any real person, contain no personal data, and
are clearly labelled as synthetic wherever they are shown.
"""

from __future__ import annotations

from dataclasses import dataclass

DEMO_NOTICE = (
    "This is completely synthetic demo data created for testing purposes. "
    "It does not belong to any real person."
)


@dataclass(frozen=True)
class DemoReport:
    kind: str
    title: str
    description: str
    filename: str
    text: str


_CBC = """DEMO DIAGNOSTICS - SYNTHETIC LABORATORY REPORT (NOT A REAL PATIENT)
Complete Blood Count (CBC)
Report Date: 12/08/2026

Test Name                Result      Unit          Reference Range
Hemoglobin               10.8        g/dL          13.0 - 17.0
Total RBC Count          4.10        mill/cumm     4.50 - 5.50
WBC Count                8,400       cells/cumm    4,000 - 11,000
Platelet Count           250,000     cells/cumm    150,000 - 450,000
Hematocrit               33.5        %             40.0 - 50.0
MCV                      81.7        fL            80.0 - 100.0
MCH                      26.3        pg            27.0 - 32.0
MCHC                     32.2        g/dL          32.0 - 36.0
RDW                      14.1        %             11.5 - 14.5

Interpretation: Values are printed as measured on this synthetic sample.
"""

_LIPID = """DEMO DIAGNOSTICS - SYNTHETIC LABORATORY REPORT (NOT A REAL PATIENT)
Lipid Profile (Fasting)
Report Date: 12/08/2026

Test Name                Result      Unit          Reference Range
Total Cholesterol        224         mg/dL         < 200
Triglycerides            190         mg/dL         < 150
HDL Cholesterol          38          mg/dL         40 - 60
LDL Cholesterol          148         mg/dL         < 100
VLDL Cholesterol         38          mg/dL         10 - 30

Interpretation: Values are printed as measured on this synthetic sample.
"""

_THYROID = """DEMO DIAGNOSTICS - SYNTHETIC LABORATORY REPORT (NOT A REAL PATIENT)
Thyroid Profile
Report Date: 12/08/2026

Test Name                Result      Unit          Reference Range
TSH                      6.80        mIU/L         0.40 - 4.00
Total T3                 1.10        ng/mL         0.80 - 2.00
Total T4                 7.20        ug/dL         5.10 - 14.10
Free T3                  2.90        pg/mL         2.30 - 4.20
Free T4                  1.10        ng/dL         0.80 - 1.80

Interpretation: Values are printed as measured on this synthetic sample.
"""

_METABOLIC = """DEMO DIAGNOSTICS - SYNTHETIC LABORATORY REPORT (NOT A REAL PATIENT)
Basic Metabolic Panel
Report Date: 12/08/2026

Test Name                Result      Unit          Reference Range
Glucose, Fasting         112         mg/dL         70 - 100
HbA1c                    6.4         %             4.0 - 5.6
Urea                     28          mg/dL         15 - 40
Creatinine               1.10        mg/dL         0.60 - 1.30
Sodium                   139         mmol/L        135 - 145
Potassium                4.2         mmol/L        3.5 - 5.1
Chloride                 101         mmol/L        98 - 107
Calcium                  9.4         mg/dL         8.5 - 10.5

Interpretation: Values are printed as measured on this synthetic sample.
"""

DEMO_REPORTS: dict[str, DemoReport] = {
    "cbc": DemoReport(
        kind="cbc",
        title="Complete Blood Count (CBC)",
        description=(
            "Synthetic CBC panel with hemoglobin, red and white cell counts, "
            "platelets and red cell indices."
        ),
        filename="Synthetic CBC Report (demo).pdf",
        text=_CBC,
    ),
    "lipid": DemoReport(
        kind="lipid",
        title="Lipid Profile",
        description=(
            "Synthetic fasting lipid panel with total cholesterol, HDL, LDL, "
            "VLDL and triglycerides."
        ),
        filename="Synthetic Lipid Profile (demo).pdf",
        text=_LIPID,
    ),
    "thyroid": DemoReport(
        kind="thyroid",
        title="Thyroid Profile",
        description="Synthetic thyroid panel with TSH, total and free T3/T4.",
        filename="Synthetic Thyroid Profile (demo).pdf",
        text=_THYROID,
    ),
    "metabolic": DemoReport(
        kind="metabolic",
        title="Basic Metabolic Panel",
        description=(
            "Synthetic metabolic panel with fasting glucose, HbA1c, kidney "
            "markers and electrolytes."
        ),
        filename="Synthetic Metabolic Panel (demo).pdf",
        text=_METABOLIC,
    ),
}
