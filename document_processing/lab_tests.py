"""Dictionary of common laboratory tests: aliases, units, topics, plain-language notes.

This drives extraction (matching names in reports) and gives the analysis layer
a short factual, education-only description per test. It intentionally contains
NO reference ranges: the range printed on the user's report always wins.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LabTest:
    key: str
    name: str
    aliases: tuple[str, ...]  # lowercase; matched longest-first
    typical_units: tuple[str, ...]
    topic: str  # knowledge-base topic key
    description: str  # one-line educational description (no diagnosis language)


LAB_TESTS: tuple[LabTest, ...] = (
    # ---- Complete blood count ----
    LabTest(
        key="hemoglobin",
        name="Hemoglobin",
        aliases=("hemoglobin", "haemoglobin", "hgb", "hb", "hb%"),
        typical_units=("g/dl", "g/l", "gm/dl"),
        topic="hemoglobin",
        description=(
            "Hemoglobin is the protein in red blood cells that carries oxygen "
            "from the lungs to the rest of the body."
        ),
    ),
    LabTest(
        key="rbc",
        name="RBC Count",
        aliases=("rbc", "red blood cell count", "total rbc", "rbc count", "erythrocyte count"),
        typical_units=("million/ul", "10^6/ul", "mill/cumm", "million/cumm", "x10^6/ul"),
        topic="cbc",
        description="The RBC count measures how many red blood cells are present in a volume of blood.",
    ),
    LabTest(
        key="wbc",
        name="WBC Count",
        aliases=("wbc", "tlc", "total leucocyte count", "total leukocyte count", "white blood cell count", "wbc count"),
        typical_units=("cells/ul", "10^3/ul", "/ul", "cumm", "/cmm", "x10^9/l"),
        topic="cbc",
        description="The WBC count measures white blood cells, which are part of the body's immune response.",
    ),
    LabTest(
        key="platelets",
        name="Platelet Count",
        aliases=("platelet count", "platelets", "plt", "platelet"),
        typical_units=("10^3/ul", "lakhs/cumm", "lakh/cumm", "/ul", "x10^9/l"),
        topic="cbc",
        description="Platelets are cell fragments that help blood to clot after an injury.",
    ),
    LabTest(
        key="hematocrit",
        name="Hematocrit",
        aliases=("hematocrit", "haematocrit", "pcv", "packed cell volume", "hct"),
        typical_units=("%",),
        topic="cbc",
        description="Hematocrit is the share of blood volume occupied by red blood cells.",
    ),
    LabTest(
        key="mcv",
        name="MCV",
        aliases=("mcv", "mean corpuscular volume", "mean cell volume"),
        typical_units=("fl", "femtoliters", "cu micron"),
        topic="cbc",
        description="MCV describes the average size of red blood cells.",
    ),
    LabTest(
        key="mch",
        name="MCH",
        aliases=("mch", "mean corpuscular hemoglobin", "mean cell hemoglobin"),
        typical_units=("pg",),
        topic="cbc",
        description="MCH describes the average amount of hemoglobin inside a red blood cell.",
    ),
    LabTest(
        key="mchc",
        name="MCHC",
        aliases=("mchc", "mean corpuscular hemoglobin concentration", "mean cell hemoglobin concentration"),
        typical_units=("g/dl", "g/l", "%"),
        topic="cbc",
        description="MCHC describes the average concentration of hemoglobin within red blood cells.",
    ),
    LabTest(
        key="rdw",
        name="RDW",
        aliases=("rdw", "red cell distribution width", "rdw-cv"),
        typical_units=("%",),
        topic="cbc",
        description="RDW describes how much red blood cells vary in size.",
    ),
    # ---- Glucose / diabetes ----
    LabTest(
        key="glucose_fasting",
        name="Glucose (Fasting)",
        aliases=("fasting glucose", "glucose fasting", "glucose, fasting", "fbs", "fasting blood sugar", "glucose-f", "fasting plasma glucose"),
        typical_units=("mg/dl", "mmol/l"),
        topic="glucose",
        description="Fasting glucose measures the amount of glucose (sugar) in blood after not eating for several hours.",
    ),
    LabTest(
        key="glucose_pp",
        name="Glucose (Post Prandial)",
        aliases=("post prandial glucose", "glucose post prandial", "pps", "ppbs", "glucose-pp", "postprandial glucose", "glucose, pp"),
        typical_units=("mg/dl", "mmol/l"),
        topic="glucose",
        description="Post-prandial glucose measures blood glucose a set time after eating.",
    ),
    LabTest(
        key="glucose_random",
        name="Glucose (Random)",
        aliases=("random glucose", "glucose random", "glucose, random", "rbs", "blood glucose", "glucose"),
        typical_units=("mg/dl", "mmol/l"),
        topic="glucose",
        description="A glucose measurement shows the amount of sugar in the blood at the time of testing.",
    ),
    LabTest(
        key="hba1c",
        name="HbA1c",
        aliases=("hba1c", "glycated hemoglobin", "glycosylated hemoglobin", "hb a1c", "a1c"),
        typical_units=("%", "mmol/mol"),
        topic="hba1c",
        description="HbA1c reflects average blood glucose over roughly the previous two to three months.",
    ),
    # ---- Kidney / metabolic ----
    LabTest(
        key="creatinine",
        name="Creatinine",
        aliases=("creatinine", "s. creatinine", "serum creatinine", "creat"),
        typical_units=("mg/dl", "umol/l", "μmol/l"),
        topic="kidney",
        description="Creatinine is a waste product filtered by the kidneys and is commonly used to assess kidney function.",
    ),
    LabTest(
        key="urea",
        name="Urea / BUN",
        aliases=("urea", "bun", "blood urea", "blood urea nitrogen", "s. urea", "serum urea"),
        typical_units=("mg/dl", "mmol/l", "mg%"),
        topic="kidney",
        description="Urea (or BUN) is a waste product produced when the body breaks down protein; the kidneys remove it from the blood.",
    ),
    LabTest(
        key="uric_acid",
        name="Uric Acid",
        aliases=("uric acid", "s. uric acid", "serum uric acid"),
        typical_units=("mg/dl", "umol/l"),
        topic="kidney",
        description="Uric acid is a waste product formed when the body breaks down purines.",
    ),
    # ---- Liver ----
    LabTest(
        key="alt",
        name="ALT (SGPT)",
        aliases=("alt", "sgpt", "alt (sgpt)", "sgpt (alt)", "alanine aminotransferase", "alanine transaminase", "alat"),
        typical_units=("u/l", "iu/l", "ui/l"),
        topic="liver",
        description="ALT is an enzyme found mainly in the liver; its level can be measured as part of liver function testing.",
    ),
    LabTest(
        key="ast",
        name="AST (SGOT)",
        aliases=("ast", "sgot", "ast (sgot)", "sgot (ast)", "aspartate aminotransferase", "aspartate transaminase", "asat"),
        typical_units=("u/l", "iu/l", "ui/l"),
        topic="liver",
        description="AST is an enzyme present in the liver and other tissues; it is commonly measured with ALT in liver panels.",
    ),
    LabTest(
        key="alp",
        name="Alkaline Phosphatase",
        aliases=("alkaline phosphatase", "alp", "s. alp", "serum alkaline phosphatase"),
        typical_units=("u/l", "iu/l"),
        topic="liver",
        description="Alkaline phosphatase (ALP) is an enzyme found in the liver, bile ducts and bones.",
    ),
    LabTest(
        key="bilirubin_total",
        name="Bilirubin (Total)",
        aliases=("bilirubin total", "total bilirubin", "bilirubin (total)", "s. bilirubin total", "bilirubin-total", "t. bilirubin"),
        typical_units=("mg/dl", "umol/l"),
        topic="liver",
        description="Total bilirubin measures the combined amount of direct and indirect bilirubin in the blood.",
    ),
    LabTest(
        key="bilirubin_direct",
        name="Bilirubin (Direct)",
        aliases=("bilirubin direct", "direct bilirubin", "bilirubin (direct)", "bilirubin-conjugated", "conjugated bilirubin", "d. bilirubin"),
        typical_units=("mg/dl", "umol/l"),
        topic="liver",
        description="Direct (conjugated) bilirubin is the form of bilirubin processed by the liver.",
    ),
    LabTest(
        key="bilirubin_indirect",
        name="Bilirubin (Indirect)",
        aliases=("bilirubin indirect", "indirect bilirubin", "bilirubin (indirect)", "unconjugated bilirubin", "i. bilirubin"),
        typical_units=("mg/dl", "umol/l"),
        topic="liver",
        description="Indirect (unconjugated) bilirubin is the form of bilirubin before the liver processes it.",
    ),
    LabTest(
        key="albumin",
        name="Albumin",
        aliases=("albumin", "s. albumin", "serum albumin"),
        typical_units=("g/dl", "g/l"),
        topic="liver",
        description="Albumin is the main protein made by the liver and helps keep fluid in the blood vessels.",
    ),
    LabTest(
        key="total_protein",
        name="Total Protein",
        aliases=("total protein", "s. total protein", "serum total protein"),
        typical_units=("g/dl", "g/l"),
        topic="liver",
        description="Total protein measures the combined amount of albumin and globulin in the blood.",
    ),
    # ---- Lipids ----
    LabTest(
        key="cholesterol_total",
        name="Total Cholesterol",
        aliases=("total cholesterol", "cholesterol total", "cholesterol, total", "t. cholesterol", "cholesterol"),
        typical_units=("mg/dl", "mmol/l"),
        topic="lipid",
        description="Total cholesterol measures the combined cholesterol carried in different lipoprotein particles in the blood.",
    ),
    LabTest(
        key="hdl",
        name="HDL Cholesterol",
        aliases=("hdl", "hdl cholesterol", "hdl-c", "hdl cholesterol (direct)", "high density lipoprotein"),
        typical_units=("mg/dl", "mmol/l"),
        topic="lipid",
        description="HDL is a lipoprotein that carries cholesterol away from tissues toward the liver.",
    ),
    LabTest(
        key="ldl",
        name="LDL Cholesterol",
        aliases=("ldl", "ldl cholesterol", "ldl-c", "ldl cholesterol (direct)", "low density lipoprotein"),
        typical_units=("mg/dl", "mmol/l"),
        topic="lipid",
        description="LDL is a lipoprotein that carries cholesterol to tissues; its level is a common measurement in lipid testing.",
    ),
    LabTest(
        key="triglycerides",
        name="Triglycerides",
        aliases=("triglycerides", "triglyceride", "tgl", "tg", "s. triglycerides"),
        typical_units=("mg/dl", "mmol/l"),
        topic="lipid",
        description="Triglycerides are a type of fat in the blood that the body uses for energy.",
    ),
    LabTest(
        key="vldl",
        name="VLDL Cholesterol",
        aliases=("vldl", "vldl cholesterol", "vldl-c"),
        typical_units=("mg/dl", "mmol/l"),
        topic="lipid",
        description="VLDL is a lipoprotein that mainly carries triglycerides from the liver to tissues.",
    ),
    # ---- Thyroid ----
    LabTest(
        key="tsh",
        name="TSH",
        aliases=("tsh", "thyroid stimulating hormone", "thyrotropin", "s. tsh"),
        typical_units=("uiu/ml", "miu/l", "μiu/ml", "uIU/mL"),
        topic="thyroid",
        description="TSH is a hormone made by the pituitary gland that signals the thyroid gland to produce thyroid hormones.",
    ),
    LabTest(
        key="t3_total",
        name="T3 (Total)",
        aliases=("t3 total", "total t3", "t3 (total)", "triiodothyronine", "t3"),
        typical_units=("ng/dl", "nmol/l"),
        topic="thyroid",
        description="T3 (triiodothyronine) is a thyroid hormone involved in regulating metabolism.",
    ),
    LabTest(
        key="t4_total",
        name="T4 (Total)",
        aliases=("t4 total", "total t4", "t4 (total)", "thyroxine", "t4"),
        typical_units=("ug/dl", "µg/dl", "nmol/l"),
        topic="thyroid",
        description="T4 (thyroxine) is the main hormone produced by the thyroid gland.",
    ),
    LabTest(
        key="ft3",
        name="T3 (Free)",
        aliases=("free t3", "ft3", "f t3", "t3 free", "free triiodothyronine"),
        typical_units=("pg/ml", "pmol/l"),
        topic="thyroid",
        description="Free T3 measures the unbound portion of T3 circulating in the blood.",
    ),
    LabTest(
        key="ft4",
        name="T4 (Free)",
        aliases=("free t4", "ft4", "f t4", "t4 free", "free thyroxine"),
        typical_units=("ng/dl", "pmol/l"),
        topic="thyroid",
        description="Free T4 measures the unbound portion of T4 circulating in the blood.",
    ),
    # ---- Electrolytes / minerals ----
    LabTest(
        key="sodium",
        name="Sodium",
        aliases=("sodium", "s. sodium", "na+", "serum sodium", "na"),
        typical_units=("mmol/l", "meq/l"),
        topic="electrolytes",
        description="Sodium is an electrolyte that helps balance fluid and support nerve and muscle function.",
    ),
    LabTest(
        key="potassium",
        name="Potassium",
        aliases=("potassium", "s. potassium", "k+", "serum potassium", "k"),
        typical_units=("mmol/l", "meq/l"),
        topic="electrolytes",
        description="Potassium is an electrolyte important for nerve signals and muscle contraction, including the heart.",
    ),
    LabTest(
        key="chloride",
        name="Chloride",
        aliases=("chloride", "s. chloride", "cl-", "serum chloride"),
        typical_units=("mmol/l", "meq/l"),
        topic="electrolytes",
        description="Chloride is an electrolyte that helps maintain fluid and acid-base balance.",
    ),
    LabTest(
        key="calcium",
        name="Calcium",
        aliases=("calcium", "s. calcium", "serum calcium", "total calcium", "ca"),
        typical_units=("mg/dl", "mmol/l", "meq/l"),
        topic="electrolytes",
        description="Calcium is a mineral involved in bone health, muscle function and nerve signaling.",
    ),
    # ---- Vitamins ----
    LabTest(
        key="vitamin_d",
        name="Vitamin D (25-OH)",
        aliases=("vitamin d", "25-oh vitamin d", "25 hydroxy vitamin d", "vit d", "25-oh-d", "vitamin d3", "vit d3", "25 oh vitamin d3"),
        typical_units=("ng/ml", "nmol/l"),
        topic="vitamins",
        description="Vitamin D is a nutrient the body uses for calcium absorption and bone health; the 25-OH test is the usual way it is measured.",
    ),
    LabTest(
        key="vitamin_b12",
        name="Vitamin B12",
        aliases=("vitamin b12", "vit b12", "b12", "cyanocobalamin", "cobalamin"),
        typical_units=("pg/ml", "pmol/l"),
        topic="vitamins",
        description="Vitamin B12 is a vitamin needed for healthy blood cells and normal nerve function.",
    ),
    LabTest(
        key="vitamin_b9",
        name="Folate (Vitamin B9)",
        aliases=("folate", "folic acid", "vitamin b9", "serum folate"),
        typical_units=("ng/ml", "nmol/l"),
        topic="vitamins",
        description="Folate is a B vitamin involved in making blood cells.",
    ),
    # ---- Inflammatory markers ----
    LabTest(
        key="esr",
        name="ESR",
        aliases=("esr", "erythrocyte sedimentation rate", "s. esr"),
        typical_units=("mm/hr", "mm/hour", "mm/1hr"),
        topic="inflammation",
        description="ESR is a general test that can rise with inflammation and is interpreted alongside other findings.",
    ),
    LabTest(
        key="crp",
        name="CRP",
        aliases=("crp", "c-reactive protein", "c reactive protein", "hs-crp", "hs crp"),
        typical_units=("mg/l", "mg/dl"),
        topic="inflammation",
        description="CRP is a protein made by the liver that can rise with inflammation in the body.",
    ),
)

# Longest aliases first so "bilirubin direct" wins over "bilirubin".
ALIAS_INDEX: tuple[tuple[str, LabTest], ...] = tuple(
    sorted(
        ((alias, test) for test in LAB_TESTS for alias in test.aliases),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)

TESTS_BY_KEY: dict[str, LabTest] = {test.key: test for test in LAB_TESTS}

TESTS_BY_TOPIC: dict[str, list[LabTest]] = {}
for _test in LAB_TESTS:
    TESTS_BY_TOPIC.setdefault(_test.topic, []).append(_test)
