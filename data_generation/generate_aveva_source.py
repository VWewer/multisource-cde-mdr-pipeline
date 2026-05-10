"""
generate_aveva_source.py
multisource-cde-mdr-pipeline | v2 pipeline — Aveva source generator

Generates source-native Aveva data for one instance:
  - Aveva_EPC  (Delta Technics BV)  — 10 documents

WHY Aveva is different from Windchill and SharePoint:
  Aveva (formerly Intergraph Smart Plant) is used by EPC contractors for
  process and instrumentation documentation. It is heavily tag-oriented —
  P&IDs and instruments are identified by tag references (e.g., T-MV-101-GA),
  not just document numbers. Revisions are numeric (0, 1, 2), not alphabetic.
  The EPC is based in continental Europe, so date formats follow DD.MM.YYYY.
  The discipline "Instrumentation & Control" is shortened to "I&C" in Aveva —
  a normalisation problem for any downstream system expecting "Instrumentation".

Aveva source-native characteristics:
  - Field names carry the aveva_ prefix
  - Document status uses Aveva vocabulary (Issued for Review, Approved for
    Construction, Returned for Comment, etc.)
  - Revisions are numeric integers: 0, 1, 2, 3
  - Dates are DD.MM.YYYY (European format — Delta Technics BV regional setting)
  - Datetimes are DD.MM.YYYY HH:MM (combined with different separator)
  - Discipline is "I&C" for Instrumentation (not "Instrumentation" or "INST")
  - Tag references are present for P&ID-type documents, blank for others

Deliberately injected DQ issues (for staged-layer detection):
  - ~1 record missing aveva_prepared_by  → DQ flag: MISSING_MANDATORY_FIELD
  - All Aveva Instrumentation records use "I&C" discipline
                                         → DQ flag: NORMALISATION_REQUIRED
  - All Aveva Civil records use "Civil & Structural"
                                         → DQ flag: NORMALISATION_REQUIRED

Output: <script_dir>/aveva_source.csv  (10 rows)

Run from anywhere — paths are relative to this script's location.
"""

import csv
import random
from datetime import date, timedelta, datetime
from pathlib import Path
from faker import Faker

fake = Faker()
random.seed(13)       # fixed seed — same output every run
Faker.seed(13)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_CODE = "PROJ1"

INSTANCE   = "Aveva_EPC"
COMPANY    = "Delta Technics BV"
DOC_COUNT  = 10

# Aveva document status vocabulary — Aveva native terminology
# Mapped to canonical statuses in the STAGED layer.
AVEVA_STATUSES = [
    "Issued for Review",
    "In Progress",
    "Approved for Construction",
    "Returned for Comment",
    "Superseded",
    "For Information",
    "Authority Issue",
    "Certified",
]

# Numeric revision sequence — key difference from Windchill (A/B/C)
# Aveva uses 0-based integers; 0 is the first issue.
REVISION_SEQUENCES = [
    [0],
    [0, 1],
    [0, 1, 2],
    [0, 1, 2, 3],
]

# Aveva discipline vocabulary — note "I&C" instead of "Instrumentation"
# and "Civil & Structural" instead of "Civil".
# These require normalisation in the STAGED layer.
AVEVA_DISCIPLINES = {
    "Mechanical":      "Mechanical",
    "Electrical":      "Electrical",
    "Instrumentation": "I&C",              # non-standard — STAGED maps to "Instrumentation"
    "Civil":           "Civil & Structural", # non-standard — STAGED maps to "Civil"
}

# As an EPC, Aveva_EPC is heaviest on Mechanical and Instrumentation (process focus)
DISCIPLINE_WEIGHTS = [0.40, 0.15, 0.35, 0.10]

# Document types per discipline
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

# Aveva document class codes — Aveva internal classification
AVEVA_DOC_CLASSES = {
    "drawing_types": "DR",
    "spec_types":    "SP",
    "calc_types":    "CA",
    "report_types":  "RP",
    "datasheet_types": "DS",
    "list_types":    "SH",
    "cert_types":    "CT",
}

# Aveva security classification vocabulary — four levels, different from both
# Windchill (Public/Internal/Restricted/Confidential) and SharePoint
# (Public/Internal Use Only/Confidential).
AVEVA_SECURITY_CLASSES = [
    "Unrestricted",
    "Restricted",
    "Confidential",
    "Highly Confidential",
]

# Approval category — same canonical set, stored with Aveva field name
APPROVAL_CATEGORIES = ["INTERNAL", "CUSTOMER", "AUTHORITY", "CERTIFICATION"]

CERTIFYING_BODIES = [
    "TUV SUD", "TUV Rheinland", "DNV", "Lloyd's Register",
    "Bureau Veritas", "SGS", "Intertek",
]

# Tag reference prefixes for P&ID-related documents (realistic Aveva tagging)
TAG_PREFIXES = ["T-MV", "T-FV", "T-PV", "T-HV", "P-", "V-", "E-", "LT-", "FT-", "PT-"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TODAY = date(2026, 5, 10)

def _log(label: str, message: str) -> None:
    """Print a timestamped log line. ASCII only — no Unicode symbols."""
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] [{label}] {message}")


def random_past_date(days_back_min: int = 30, days_back_max: int = 400) -> date:
    """Return a random past date within the given range."""
    return TODAY - timedelta(days=random.randint(days_back_min, days_back_max))


def to_eu_date(d: date) -> str:
    """
    Format a date as DD.MM.YYYY — Aveva European regional setting.

    This is a key DQ difference from Windchill (ISO 8601) and SharePoint (MM/DD/YYYY).
    The STAGED layer must detect and parse this format correctly.
    Note: DD.MM.YYYY and MM/DD/YYYY are ambiguous for days <= 12 — a real-world
    problem that requires the STAGED layer to know the source system's locale.
    """
    return d.strftime("%d.%m.%Y")


def to_eu_datetime(d: date) -> str:
    """
    Format a datetime as DD.MM.YYYY HH:MM — Aveva last_updated field format.
    Combined date-time with different separator from Windchill ISO 8601.
    """
    hour   = random.randint(6, 18)
    minute = random.randint(0, 59)
    return d.strftime("%d.%m.%Y") + f" {hour:02d}:{minute:02d}"


def get_doc_class(doc_type: str) -> str:
    """Map document type to Aveva document class code."""
    if any(k in doc_type for k in ["Drawing", "Diagram", "Layout", "Isometric", "P&ID"]):
        return "DR"
    if "Specification" in doc_type:
        return "SP"
    if "Calculation" in doc_type:
        return "CA"
    if any(k in doc_type for k in ["Report", "Assessment", "Narrative"]):
        return "RP"
    if "Datasheet" in doc_type:
        return "DS"
    if any(k in doc_type for k in ["Schedule", "List", "Index", "Take-Off", "Matrix"]):
        return "SH"
    if any(k in doc_type for k in ["Certificate", "ITP"]):
        return "CT"
    return "RP"


def get_tag_reference(doc_type: str) -> str:
    """
    Generate an Aveva tag reference for P&ID-type documents.
    Non-P&ID documents have no tag reference — this field is blank.
    """
    tagged_types = {"P&ID", "Loop Diagram", "Instrument Hook-Up Drawing", "Cause & Effect Matrix"}
    if doc_type in tagged_types:
        prefix = random.choice(TAG_PREFIXES)
        number = random.randint(100, 999)
        suffix = random.choice(["GA", "GS", "GD", "SA", "SB"])
        return f"{prefix}-{number}-{suffix}"
    return ""


def get_approval_category(doc_type: str) -> str:
    """Assign approval category based on document type."""
    authority_types = {"HAZOP Report", "SIL Assessment", "Geotechnical Report", "Civil Specification"}
    cert_types      = {
        "Pump Datasheet", "Heat Exchanger Datasheet", "Structural Calculation",
        "Structural Steel Drawing", "Foundation Drawing",
        "Mechanical Completion Certificate", "Protection Relay Setting",
    }
    customer_types  = {
        "P&ID", "Cause & Effect Matrix", "Control Narrative", "ITP",
        "Equipment Layout", "Single Line Diagram",
    }
    if doc_type in authority_types:  return "AUTHORITY"
    if doc_type in cert_types:       return "CERTIFICATION"
    if doc_type in customer_types:   return "CUSTOMER"
    return random.choices(
        ["INTERNAL", "CUSTOMER", "AUTHORITY", "CERTIFICATION"],
        weights=[0.45, 0.35, 0.12, 0.08], k=1
    )[0]


def get_security_class(approval_category: str, doc_type: str) -> str:
    """Map to Aveva security classification vocabulary."""
    confidential_types = {
        "Geotechnical Report", "HAZOP Report", "SIL Assessment",
        "Control Narrative", "Cause & Effect Matrix",
    }
    if doc_type in confidential_types or approval_category == "AUTHORITY":
        return "Highly Confidential"
    if approval_category == "CERTIFICATION":
        return random.choice(["Restricted", "Confidential"])
    if approval_category == "INTERNAL":
        return "Unrestricted"
    return random.choices(["Unrestricted", "Restricted", "Confidential"], weights=[0.5, 0.3, 0.2], k=1)[0]


def get_file_format(doc_type: str) -> str:
    """Choose a realistic file format."""
    drawing_keywords = ["Drawing", "Diagram", "Layout", "Isometric", "P&ID"]
    list_keywords    = ["Schedule", "List", "Index", "Take-Off", "Matrix"]
    if any(k in doc_type for k in drawing_keywords):
        return random.choice(["DWG", "PDF"])
    if any(k in doc_type for k in list_keywords):
        return random.choice(["XLSX", "PDF"])
    return "PDF"


# ---------------------------------------------------------------------------
# Record generation
# ---------------------------------------------------------------------------

_aveva_counter = 0

def next_aveva_id() -> str:
    """Generate the next sequential Aveva internal document ID."""
    global _aveva_counter
    _aveva_counter += 1
    return f"AVA-{_aveva_counter:06d}"


def next_aveva_doc_number(discipline: str, seq: int) -> str:
    """
    Build an Aveva document number in Aveva's own numbering format.
    Format: {PROJECT}-AVA-{DISCIPLINE_ABBR}-{SEQ:04d}
    Note: Aveva uses its own numbering, independent of Windchill's DOC-MECH-001 scheme.
    This numbering difference is what makes cross-system deduplication hard.
    """
    disc_abbr = {"Mechanical": "ME", "Electrical": "EL", "Instrumentation": "IC", "Civil": "CV"}
    return f"{PROJECT_CODE}-AVA-{disc_abbr.get(discipline, 'XX')}-{seq:04d}"


def generate_records(count: int) -> list:
    """
    Generate all Aveva source records.

    Args:
        count: Number of records to generate.

    Returns:
        List of dicts, each representing one Aveva source document.
    """
    disciplines = list(AVEVA_DISCIPLINES.keys())
    records = []

    for i in range(count):
        discipline = random.choices(disciplines, weights=DISCIPLINE_WEIGHTS, k=1)[0]
        doc_type   = random.choice(DISCIPLINE_DOC_TYPES[discipline])

        rev_seq  = random.choice(REVISION_SEQUENCES)
        revision = rev_seq[random.randint(0, len(rev_seq) - 1)]  # integer: 0, 1, 2...

        doc_status = random.choice(AVEVA_STATUSES)

        approval_category = get_approval_category(doc_type)
        security_class    = get_security_class(approval_category, doc_type)

        cert_required = approval_category == "CERTIFICATION" or (
            approval_category == "CUSTOMER" and random.random() < 0.15
        )
        cert_body = random.choice(CERTIFYING_BODIES) if cert_required else ""

        issue_date   = random_past_date(90, 400)
        updated_date = issue_date + timedelta(days=random.randint(1, 60))
        if updated_date > TODAY:
            updated_date = TODAY

        aveva_id   = next_aveva_id()
        doc_number = next_aveva_doc_number(discipline, i + 1)
        tag_ref    = get_tag_reference(doc_type)
        doc_class  = get_doc_class(doc_type)

        records.append({
            # -- Aveva identity (numeric ID + separate doc number)
            "aveva_instance":      INSTANCE,
            "aveva_id":            aveva_id,
            "aveva_doc_number":    doc_number,
            "aveva_tag_ref":       tag_ref,        # blank for non-P&ID documents
            # -- Status (Aveva vocabulary)
            "aveva_document_status": doc_status,
            "aveva_revision_no":   revision,       # integer: 0, 1, 2 (NOT alphabetic)
            # -- Timestamps (DD.MM.YYYY — European format)
            "aveva_issue_date":    to_eu_date(issue_date),
            "aveva_last_updated":  to_eu_datetime(updated_date),
            # -- People
            "aveva_prepared_by":   fake.name(),    # blanked for ~1 record (DQ)
            "aveva_checked_by":    fake.name(),
            "aveva_approved_by":   fake.name(),
            # -- Classification (Aveva vocabulary — I&C, Civil & Structural)
            "aveva_discipline":    AVEVA_DISCIPLINES[discipline],  # "I&C" for Instrumentation
            "aveva_doc_class":     doc_class,
            "aveva_doc_type":      doc_type,
            "aveva_originator_org": COMPANY,
            "aveva_security_class": security_class,
            "aveva_approval_category": approval_category,
            "aveva_certifying_body": cert_body,
            "aveva_certification_required": cert_required,
            "aveva_file_format":   get_file_format(doc_type),
        })

    return records


def inject_dq_issues(records: list) -> list:
    """
    Inject deliberate data quality problems into a small subset of records.

    WHY: In Aveva, prepared_by is a mandatory field but is sometimes omitted
    when documents are imported from a legacy system rather than created natively.

    DQ issues injected:
      - 1 record: aveva_prepared_by set to "" (missing mandatory field)

    Note: aveva_discipline values of "I&C" and "Civil & Structural" are
    structural issues present in ALL relevant records — they are not injected
    randomly but are properties of the Aveva source system. The STAGED layer
    handles them via a lookup table.

    Args:
        records: Full list of generated records.

    Returns:
        Same list with DQ issue injected.
    """
    idx = random.randrange(len(records))
    records[idx]["aveva_prepared_by"] = ""
    _log("DQ-INJECT", f"Record {records[idx]['aveva_id']}: aveva_prepared_by blanked (MISSING_MANDATORY_FIELD)")
    return records


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

FIELDNAMES = [
    "aveva_instance", "aveva_id", "aveva_doc_number", "aveva_tag_ref",
    "aveva_document_status", "aveva_revision_no",
    "aveva_issue_date", "aveva_last_updated",
    "aveva_prepared_by", "aveva_checked_by", "aveva_approved_by",
    "aveva_discipline", "aveva_doc_class", "aveva_doc_type",
    "aveva_originator_org", "aveva_security_class", "aveva_approval_category",
    "aveva_certifying_body", "aveva_certification_required",
    "aveva_file_format",
]


def write_csv(records: list, path: Path) -> None:
    """Write records to CSV. UTF-8 encoding."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(records)
    _log("SAVE", f"Written {len(records)} records -> {path}")


def print_summary(records: list) -> None:
    """Print a plain-text summary of what was generated."""
    from collections import Counter
    print("\n--- Aveva Source Summary ----------------------------------------")
    print(f"Total records : {len(records)}")

    for label, key in [
        ("Document status",    "aveva_document_status"),
        ("Discipline (Aveva)", "aveva_discipline"),
        ("Revision no",        "aveva_revision_no"),
        ("Doc class",          "aveva_doc_class"),
        ("Security class",     "aveva_security_class"),
        ("Approval category",  "aveva_approval_category"),
    ]:
        counts = Counter(str(r[key]) for r in records)
        print(f"\n{label}:")
        for k, v in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"  {k:<40} {v:>3}")

    tagged = sum(1 for r in records if r["aveva_tag_ref"])
    missing_prepared_by = sum(1 for r in records if not r["aveva_prepared_by"])
    normalisation_needed = sum(
        1 for r in records if r["aveva_discipline"] in ("I&C", "Civil & Structural")
    )
    print(f"\nDQ issues injected:")
    print(f"  Missing aveva_prepared_by (MISSING_MANDATORY_FIELD): {missing_prepared_by}")
    print(f"\nDQ issues structural (all relevant records):")
    print(f"  Discipline requiring normalisation (NORMALISATION_REQUIRED): {normalisation_needed}")
    print(f"\nTag references present: {tagged} / {len(records)}")
    print("-" * 53)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    script_dir  = Path(__file__).parent
    output_path = script_dir / "aveva_source.csv"

    _log("LOAD", f"Generating {DOC_COUNT} Aveva source records for {INSTANCE}")

    records = generate_records(DOC_COUNT)
    records = inject_dq_issues(records)

    write_csv(records, output_path)
    print_summary(records)
