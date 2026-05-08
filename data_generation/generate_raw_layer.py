"""
generate_raw_layer.py
multisource-cde-mdr-pipeline | Day 2 — RAW layer (v3 — final)

Changes in v3:
  - Priority renamed: "Critical" → "Very High" (avoids confusion with critical path)
  - ISO 19650-2 full naming convention: project_code, originator_code, volume_code,
    level_code, type_code, role_code assembled into iso19650_filename
  - type_code derived from document_type (DR/SP/CA/RP/DS/SH/CT)
  - volume_code and level_code added as fields
  - All timestamps ISO 8601 UTC (trailing Z)

Run from anywhere — paths are relative to this script's location.
Output: <script_dir>/raw_documents.csv
"""

import csv
import random
import uuid
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from faker import Faker

fake = Faker()
random.seed(42)
Faker.seed(42)

# ── Project constants ──────────────────────────────────────────────────────────
PROJECT_CODE = "PROJ1"

# ── ISO 19650-2 suitability code mapping ───────────────────────────────────────
ISO19650_STATUS_MAP = {
    "REQUIREMENT_DRAFT":      "S0",
    "PLANNED":                "S0",
    "IN_PROGRESS":            "S0",
    "SUBMITTED":              "S2",
    "UNDER_REVIEW":           "S3",
    "REVISION_REQUIRED":      "S3",
    "APPROVED_DRAFT":         "S4",
    "APPROVED_CUSTOMER":      "S4",
    "APPROVED_AUTHORITY":     "S4",
    "APPROVED_CERTIFICATION": "S4",
    "APPROVED_FINAL":         "S5",
    "SUPERSEDED":             "S7",
    "OVERDUE":                "S0",
    "ON_HOLD":                "S0",
}

# ── ISO 19650-2 document type codes ───────────────────────────────────────────
# Maps document_type keywords → ISO type code
TYPE_CODE_MAP = {
    # Drawings
    "Drawing":     "DR",
    "Diagram":     "DR",
    "Layout":      "DR",
    "Isometric":   "DR",
    "P&ID":        "DR",
    # Specifications
    "Specification": "SP",
    # Calculations
    "Calculation": "CA",
    # Reports
    "Report":      "RP",
    "Assessment":  "RP",
    "Narrative":   "RP",
    # Datasheets
    "Datasheet":   "DS",
    # Schedules / lists / indices
    "Schedule":    "SH",
    "List":        "SH",
    "Index":       "SH",
    "Take-Off":    "SH",
    "Matrix":      "SH",
    # Certificates / ITPs
    "Certificate": "CT",
    "ITP":         "CT",
}

def get_type_code(doc_type: str) -> str:
    for keyword, code in TYPE_CODE_MAP.items():
        if keyword in doc_type:
            return code
    return "RP"   # default: report

# ── ISO 19650-2 discipline role codes ─────────────────────────────────────────
DISCIPLINE_ROLE_CODES = {
    "Mechanical":      "ME",
    "Electrical":      "EL",
    "Instrumentation": "IN",
    "Civil":           "CI",
}

# ── ISO 19650-2 originator codes (from company name) ──────────────────────────
ORIGINATOR_CODES = {
    "Alpha Engineering GmbH": "ALPHAENG",
    "Beta Konstruktion AS":   "BETAKONS",
    "Gamma EPC Ltd":          "GAMMAEPC",
    "Delta Technics BV":      "DELTATECH",
}

# ── Volume codes (project areas) ──────────────────────────────────────────────
# ZZ = whole project / not area-specific
VOLUME_CODES = ["ZZ", "01", "02", "03"]
VOLUME_WEIGHTS = [0.40, 0.25, 0.20, 0.15]

# ── Level codes ───────────────────────────────────────────────────────────────
# 00 = not applicable (most engineering docs), XX = multiple levels
LEVEL_CODES = {
    "Mechanical":      ["00"],
    "Electrical":      ["00"],
    "Instrumentation": ["00"],
    "Civil":           ["00", "01", "02", "B1"],   # Civil has actual levels
}

def build_iso19650_filename(
    project_code: str,
    originator_code: str,
    volume_code: str,
    level_code: str,
    type_code: str,
    role_code: str,
    doc_number: str,    # e.g. DOC-MEC-001 → extract just the number part
    revision: str,
) -> str:
    """
    Assemble ISO 19650-2 compliant filename.
    Format: [Project]-[Originator]-[Volume]-[Level]-[Type]-[Role]-[Number]-[Revision]
    Example: PROJ1-ALPHAENG-ZZ-00-DR-ME-000123-A
    """
    # Extract numeric part only from doc_number (DOC-MEC-001 → 001 → zero-pad to 6)
    num_part = doc_number.split("-")[-1].zfill(6)
    return f"{project_code}-{originator_code}-{volume_code}-{level_code}-{type_code}-{role_code}-{num_part}-{revision}"

# ── Translation table (source_system → source_status → canonical_status) ──────
TRANSLATION_TABLE = {
    "Windchill": {
        "In Work":            "IN_PROGRESS",
        "In Review":          "UNDER_REVIEW",
        "Released":           "APPROVED_FINAL",
        "Revised":            "REVISION_REQUIRED",
        "Baselined":          "APPROVED_DRAFT",
        "Cancelled":          "ON_HOLD",
        "Issued for Client":  "APPROVED_CUSTOMER",
        "Authority Approved": "APPROVED_AUTHORITY",
    },
    "SharePoint": {
        "Draft":              "IN_PROGRESS",
        "Pending Review":     "SUBMITTED",
        "Under Review":       "UNDER_REVIEW",
        "Approved":           "APPROVED_DRAFT",
        "Client Approved":    "APPROVED_CUSTOMER",
        "Final Approved":     "APPROVED_FINAL",
        "Rejected":           "REVISION_REQUIRED",
        "On Hold":            "ON_HOLD",
    },
    "Aveva": {
        "Issued for Review":         "SUBMITTED",
        "In Progress":               "IN_PROGRESS",
        "Approved for Construction": "APPROVED_FINAL",
        "Returned for Comment":      "REVISION_REQUIRED",
        "Superseded":                "SUPERSEDED",
        "For Information":           "APPROVED_DRAFT",
        "Authority Issue":           "APPROVED_AUTHORITY",
        "Certified":                 "APPROVED_CERTIFICATION",
    },
}

TERMINAL_STATUSES = {
    "APPROVED_FINAL", "APPROVED_CUSTOMER", "APPROVED_AUTHORITY",
    "APPROVED_CERTIFICATION", "SUPERSEDED"
}

# ── Source system config ───────────────────────────────────────────────────────
SOURCE_SYSTEMS = [
    {"source_system": "Windchill",  "source_system_instance": "Windchill_PartnerA", "weight": 0.30},
    {"source_system": "Windchill",  "source_system_instance": "Windchill_PartnerB", "weight": 0.25},
    {"source_system": "SharePoint", "source_system_instance": "SharePoint_PMC",     "weight": 0.25},
    {"source_system": "Aveva",      "source_system_instance": "Aveva_EPC",          "weight": 0.20},
]

INSTANCE_TO_COMPANY = {
    "Windchill_PartnerA": "Alpha Engineering GmbH",
    "Windchill_PartnerB": "Beta Konstruktion AS",
    "SharePoint_PMC":     "Gamma EPC Ltd",
    "Aveva_EPC":          "Delta Technics BV",
}

# ── Discipline → document types ────────────────────────────────────────────────
DISCIPLINE_DOC_TYPES = {
    "Mechanical": [
        "P&ID", "Equipment Datasheet", "Mechanical Completion Certificate",
        "Piping Isometric", "Equipment Layout", "Valve List",
        "Line List", "Pump Datasheet", "Heat Exchanger Datasheet",
    ],
    "Electrical": [
        "Single Line Diagram", "Cable Schedule", "Electrical Equipment List",
        "Load List", "Lighting Layout", "Protection Relay Setting",
        "Motor Control Centre Layout", "Earthing Drawing",
    ],
    "Instrumentation": [
        "Instrument Index", "Loop Diagram", "Cause & Effect Matrix",
        "Instrument Datasheet", "Control Narrative", "HAZOP Report",
        "SIL Assessment", "Instrument Hook-Up Drawing", "ITP",
    ],
    "Civil": [
        "Foundation Drawing", "Structural Steel Drawing", "Civil Specification",
        "Geotechnical Report", "Drainage Layout", "Grading Plan",
        "Civil Material Take-Off", "Structural Calculation",
    ],
}

# ── Approval class ─────────────────────────────────────────────────────────────
CERTIFYING_BODIES = [
    "TÜV SÜD", "TÜV Rheinland", "DNV", "Lloyd's Register",
    "Bureau Veritas", "SGS", "Intertek",
]

def get_approval_class(doc_type: str) -> str:
    authority_triggers = {
        "HAZOP Report", "SIL Assessment", "Geotechnical Report", "Civil Specification"
    }
    certification_triggers = {
        "Pump Datasheet", "Heat Exchanger Datasheet", "Structural Calculation",
        "Structural Steel Drawing", "Foundation Drawing",
        "Mechanical Completion Certificate", "Protection Relay Setting",
    }
    customer_triggers = {
        "P&ID", "Cause & Effect Matrix", "Control Narrative", "ITP",
        "Equipment Layout", "Single Line Diagram",
    }
    if doc_type in authority_triggers:   return "AUTHORITY"
    if doc_type in certification_triggers: return "CERTIFICATION"
    if doc_type in customer_triggers:    return "CUSTOMER"
    return random.choices(
        ["INTERNAL", "CUSTOMER", "AUTHORITY", "CERTIFICATION"],
        weights=[0.45, 0.35, 0.12, 0.08], k=1
    )[0]

def get_certification_fields(approval_class: str) -> tuple:
    if approval_class == "CERTIFICATION":
        return True, random.choice(CERTIFYING_BODIES), fake.name()
    if approval_class == "CUSTOMER" and random.random() < 0.15:
        return True, random.choice(CERTIFYING_BODIES), fake.name()
    return False, "", ""

# ── Confidentiality (ISO 19650-5) ──────────────────────────────────────────────
CONFIDENTIAL_DOC_TYPES = {
    "Geotechnical Report", "HAZOP Report", "SIL Assessment",
    "Control Narrative", "Cause & Effect Matrix",
}
RESTRICTED_DOC_TYPES = {
    "Equipment Datasheet", "Pump Datasheet", "Heat Exchanger Datasheet",
    "Instrument Datasheet",
}

def get_confidentiality(approval_class: str, doc_type: str) -> tuple:
    if doc_type in CONFIDENTIAL_DOC_TYPES or approval_class == "AUTHORITY":
        return "CONFIDENTIAL", True
    if doc_type in RESTRICTED_DOC_TYPES or approval_class == "CERTIFICATION":
        return "RESTRICTED", random.random() < 0.5
    if approval_class == "INTERNAL":
        return "INTERNAL", False
    conf = random.choices(["INTERNAL", "CONFIDENTIAL"], weights=[0.75, 0.25], k=1)[0]
    return conf, conf == "CONFIDENTIAL"

# ── File format ────────────────────────────────────────────────────────────────
def get_file_format(doc_type: str) -> str:
    if any(k in doc_type for k in ["Drawing", "Diagram", "Layout", "Isometric", "P&ID"]):
        return random.choice(["DWG", "PDF"])
    if any(k in doc_type for k in ["Schedule", "List", "Index", "Take-Off", "Matrix"]):
        return random.choice(["XLSX", "PDF"])
    return "PDF"

# ── Revision sequences ─────────────────────────────────────────────────────────
REVISION_SEQUENCES = [
    ["A"], ["A", "B"], ["A", "B", "C"],
    ["A", "B", "0"], ["A", "B", "C", "0"],
    ["0"], ["0", "1"],
]

# ── Document numbering ─────────────────────────────────────────────────────────
DISCIPLINE_CODES = {
    "Mechanical":      "MEC",
    "Electrical":      "ELE",
    "Instrumentation": "INS",
    "Civil":           "CIV",
}
discipline_counters = {d: 0 for d in DISCIPLINE_CODES}

def next_doc_number(discipline: str) -> str:
    discipline_counters[discipline] += 1
    return f"DOC-{DISCIPLINE_CODES[discipline]}-{discipline_counters[discipline]:03d}"

# ── Timestamp helpers (ISO 8601 UTC) ───────────────────────────────────────────
TODAY = date(2026, 5, 8)

def random_past_date(days_back_min=30, days_back_max=400) -> date:
    return TODAY - timedelta(days=random.randint(days_back_min, days_back_max))

def to_utc_iso(d: date) -> str:
    dt = datetime(
        d.year, d.month, d.day,
        random.randint(6, 18), random.randint(0, 59), random.randint(0, 59),
        tzinfo=timezone.utc
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Main generation ────────────────────────────────────────────────────────────
def generate_records(n: int = 60) -> list:
    records = []
    disciplines = list(DISCIPLINE_DOC_TYPES.keys())
    discipline_weights = [0.30, 0.25, 0.30, 0.15]

    for _ in range(n):
        discipline = random.choices(disciplines, weights=discipline_weights, k=1)[0]
        doc_type   = random.choice(DISCIPLINE_DOC_TYPES[discipline])

        sys_cfg                = random.choices(SOURCE_SYSTEMS, weights=[s["weight"] for s in SOURCE_SYSTEMS], k=1)[0]
        source_system          = sys_cfg["source_system"]
        source_system_instance = sys_cfg["source_system_instance"]

        rev_seq  = random.choice(REVISION_SEQUENCES)
        revision = rev_seq[random.randint(0, len(rev_seq) - 1)]

        source_status    = random.choice(list(TRANSLATION_TABLE[source_system].keys()))
        canonical_status = TRANSLATION_TABLE[source_system][source_status]
        iso19650_status  = ISO19650_STATUS_MAP[canonical_status]

        issue_date = (
            random_past_date(90, 400) if canonical_status in TERMINAL_STATUSES
            else random_past_date(5, 300)
        )

        document_id        = str(uuid.uuid4())
        doc_number         = next_doc_number(discipline)
        originator_company = INSTANCE_TO_COMPANY[source_system_instance]
        originator_code    = ORIGINATOR_CODES[originator_company]
        role_code          = DISCIPLINE_ROLE_CODES[discipline]
        type_code          = get_type_code(doc_type)
        volume_code        = random.choices(VOLUME_CODES, weights=VOLUME_WEIGHTS, k=1)[0]
        level_code         = random.choice(LEVEL_CODES[discipline])

        source_system_id   = f"{source_system_instance[:3].upper()}-{doc_number}-{revision}"
        iso19650_filename  = build_iso19650_filename(
            PROJECT_CODE, originator_code, volume_code, level_code,
            type_code, role_code, doc_number, revision
        )

        approval_class                         = get_approval_class(doc_type)
        cert_required, cert_body, cert_contact = get_certification_fields(approval_class)
        confidentiality, sensitive             = get_confidentiality(approval_class, doc_type)

        records.append({
            # ── Identity
            "document_id":              document_id,
            "source_system_id":         source_system_id,
            "source_system":            source_system,
            "source_system_instance":   source_system_instance,
            # ── Status
            "source_status":            source_status,
            "canonical_status":         canonical_status,
            "iso19650_status_code":     iso19650_status,
            # ── ISO 19650-2 naming fields
            "project_code":             PROJECT_CODE,
            "originator_code":          originator_code,
            "volume_code":              volume_code,
            "level_code":               level_code,
            "type_code":                type_code,
            "role_code":                role_code,
            "iso19650_filename":        iso19650_filename,
            # ── Document metadata
            "document_type":            doc_type,
            "discipline":               discipline,
            "revision":                 revision,
            "title":                    f"{discipline} — {doc_type} — {source_system_id}",
            "description":              f"{doc_type} for {discipline} scope, revision {revision}.",
            "file_format":              get_file_format(doc_type),
            # ── Approval & certification
            "approval_class":           approval_class,
            "certification_required":   cert_required,
            "certifying_body":          cert_body,
            "certifying_body_contact":  cert_contact,
            # ── Confidentiality (ISO 19650-5)
            "confidentiality_class":    confidentiality,
            "sensitive_information":    sensitive,
            # ── People
            "author":                   fake.name(),
            "co_author":                fake.name() if random.random() < 0.4 else "",
            "originator_company":       originator_company,
            "reviewer":                 fake.name(),
            "approver":                 fake.name(),
            # ── Dates (ISO 8601 UTC)
            "issue_date":               issue_date.isoformat(),
            "ingested_at":              to_utc_iso(issue_date + timedelta(days=random.randint(1, 5))),
        })

    return records


def write_csv(records: list, path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    print(f"✓ Written {len(records)} records → {path}")


def print_summary(records: list):
    from collections import Counter
    print("\n── RAW Layer Summary ─────────────────────────────────")
    print(f"Total records : {len(records)}")
    for label, key in [
        ("Source system instance", "source_system_instance"),
        ("Discipline",             "discipline"),
        ("Approval class",         "approval_class"),
        ("Confidentiality",        "confidentiality_class"),
        ("ISO 19650 status",       "iso19650_status_code"),
        ("Type code",              "type_code"),
        ("Volume code",            "volume_code"),
    ]:
        counts = Counter(r[key] for r in records)
        print(f"\n{label}:")
        for k, v in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"  {k:<32} {v:>3}")
    print(f"\nCertification required : {sum(1 for r in records if r['certification_required'])}")
    print(f"Sensitive information  : {sum(1 for r in records if r['sensitive_information'])}")
    print(f"\nSample ISO 19650 filenames:")
    for r in records[:3]:
        print(f"  {r['iso19650_filename']}")
    print("─────────────────────────────────────────────────────")


if __name__ == "__main__":
    script_dir = Path(__file__).parent
    records = generate_records(60)
    write_csv(records, str(script_dir / "raw_documents.csv"))
    print_summary(records)
