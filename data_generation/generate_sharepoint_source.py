"""
generate_sharepoint_source.py
multisource-cde-mdr-pipeline | v2 pipeline — SharePoint source generator

Generates source-native SharePoint data for one instance:
  - SharePoint_PMC  (Gamma EPC Ltd)  — 20 documents

WHY SharePoint is different from Windchill:
  SharePoint is a collaboration platform, not a PLM system. It has no formal
  lifecycle enforcement — users set status manually, version numbers are
  auto-incremented decimals (1.0, 1.1, 2.0), and metadata discipline is stored
  as a verbose department name, not a code. Date fields are in US format because
  the PMC's regional settings are en-US.

SharePoint source-native characteristics:
  - Field names carry the sp_ prefix
  - Status vocabulary is informal: "Draft", "Pending Review", "Approved", etc.
  - Versions are numeric: 1.0, 1.1, 2.0 (NOT alphabetic like Windchill)
  - Dates are MM/DD/YYYY — en-US SharePoint regional setting
  - Discipline stored as verbose department: "Mechanical Engineering",
    "Instrumentation & Control" (not "MECH" / "INST")
  - Confidentiality vocabulary differs: "Internal Use Only" (not "Internal")

Deliberately injected DQ issues (for staged-layer detection):
  - ~2 records missing sp_created_by     → DQ flag: MISSING_MANDATORY_FIELD
  - ~2 records missing sp_company        → DQ flag: MISSING_MANDATORY_FIELD
  - ~2 records: sp_department requires normalisation to canonical discipline code
    ("Instrumentation & Control" → "Instrumentation", "Civil & Structural" → "Civil")
    These are not errors per se but require a lookup table in the STAGED layer.

Output: <script_dir>/sharepoint_source.csv  (20 rows)

Run from anywhere — paths are relative to this script's location.
"""

import csv
import random
from datetime import date, timedelta, datetime
from pathlib import Path
from faker import Faker

fake = Faker()
random.seed(77)       # fixed seed — same output every run
Faker.seed(77)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_CODE = "PROJ1"

INSTANCE    = "SharePoint_PMC"
COMPANY     = "Gamma EPC Ltd"
DOC_COUNT   = 20

# SharePoint status vocabulary — informal, not a formal lifecycle
# These differ from Windchill lifecycle states and Aveva document statuses.
# The STAGED harmonisation maps these to the canonical status set.
SP_STATUSES = [
    "Draft",
    "Pending Review",
    "Under Review",
    "Approved",
    "Client Approved",
    "Final Approved",
    "Rejected",
    "On Hold",
]

# SharePoint version numbers — major.minor decimal format
# This is the key difference from Windchill (A/B/C) and Aveva (0/1/2).
SP_VERSION_SEQUENCES = [
    ["1.0"],
    ["1.0", "1.1"],
    ["1.0", "1.1", "2.0"],
    ["1.0", "2.0"],
    ["1.0", "1.1", "1.2", "2.0"],
]

# Department names — verbose, as stored in SharePoint metadata column.
# NOTE: "Instrumentation & Control" and "Civil & Structural" are the DQ
# normalisation challenge — they don't match the canonical discipline names
# ("Instrumentation", "Civil") and require a lookup in the STAGED layer.
SP_DEPARTMENTS = {
    "Mechanical":      "Mechanical Engineering",
    "Electrical":      "Electrical Engineering",
    "Instrumentation": "Instrumentation & Control",   # requires normalisation
    "Civil":           "Civil & Structural",           # requires normalisation
}

# Document types per discipline — same underlying types, different labelling
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

# As a PMC, SharePoint_PMC deals with all disciplines roughly equally
DISCIPLINE_WEIGHTS = [0.28, 0.25, 0.27, 0.20]

# SharePoint confidentiality vocabulary — different from Windchill
# "Internal Use Only" vs Windchill's "Internal"; same concept, different label.
SP_CLASSIFICATIONS = ["Public", "Internal Use Only", "Confidential"]

# SharePoint approval type vocabulary
SP_APPROVAL_TYPES = {
    "INTERNAL":      "Internal",
    "CUSTOMER":      "Client",
    "AUTHORITY":     "Authority",
    "CERTIFICATION": "Certification",
}

CERTIFYING_BODIES = [
    "TUV SUD", "TUV Rheinland", "DNV", "Lloyd's Register",
    "Bureau Veritas", "SGS", "Intertek",
]

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


def to_us_date(d: date) -> str:
    """
    Format a date as MM/DD/YYYY — SharePoint en-US regional setting.

    This is a key DQ difference from Windchill (ISO 8601) and Aveva (DD.MM.YYYY).
    The STAGED layer must detect and normalise this format.
    """
    return d.strftime("%m/%d/%Y")


def get_file_format(doc_type: str) -> str:
    """Choose a realistic file format based on document type."""
    drawing_keywords = ["Drawing", "Diagram", "Layout", "Isometric", "P&ID"]
    list_keywords    = ["Schedule", "List", "Index", "Take-Off", "Matrix"]
    if any(k in doc_type for k in drawing_keywords):
        return random.choice(["dwg", "pdf"])    # SharePoint uses lowercase extensions
    if any(k in doc_type for k in list_keywords):
        return random.choice(["xlsx", "pdf"])
    return "pdf"


def get_approval_type(doc_type: str) -> tuple:
    """
    Return (canonical_approval_class, sp_approval_type) for a document type.
    SharePoint stores the approval type in its own vocabulary.
    """
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
    if doc_type in authority_types:
        approval_class = "AUTHORITY"
    elif doc_type in cert_types:
        approval_class = "CERTIFICATION"
    elif doc_type in customer_types:
        approval_class = "CUSTOMER"
    else:
        approval_class = random.choices(
            ["INTERNAL", "CUSTOMER", "AUTHORITY", "CERTIFICATION"],
            weights=[0.45, 0.35, 0.12, 0.08], k=1
        )[0]
    return approval_class, SP_APPROVAL_TYPES[approval_class]


def get_sp_classification(approval_class: str, doc_type: str) -> str:
    """Map to SharePoint classification vocabulary."""
    confidential_types = {
        "Geotechnical Report", "HAZOP Report", "SIL Assessment",
        "Control Narrative", "Cause & Effect Matrix",
    }
    if doc_type in confidential_types or approval_class == "AUTHORITY":
        return "Confidential"
    if approval_class == "INTERNAL":
        return "Internal Use Only"
    return random.choices(["Internal Use Only", "Confidential"], weights=[0.75, 0.25], k=1)[0]


def make_sp_file_name(doc_type: str, company: str, version: str, file_format: str) -> str:
    """
    Build a realistic SharePoint file name.
    SharePoint stores documents by file name, not a formal document number.
    This is a harmonisation challenge — we cannot reliably parse a doc number from this.
    """
    # Shorten company to a tag
    company_tag = company.split()[0].upper()[:6]
    # Replace spaces and ampersands in doc type
    type_tag = doc_type.replace("&", "and").replace(" ", "_")
    return f"{PROJECT_CODE}_{company_tag}_{type_tag}_v{version}.{file_format}"


# ---------------------------------------------------------------------------
# Record generation
# ---------------------------------------------------------------------------

_sp_item_counter = 4500   # SharePoint item IDs start at a realistic offset

def next_sp_item_id() -> int:
    """Return the next SharePoint item ID (auto-incremented integer)."""
    global _sp_item_counter
    _sp_item_counter += random.randint(1, 12)   # gaps in SP item IDs are normal
    return _sp_item_counter


def generate_records(count: int) -> list:
    """
    Generate all SharePoint source records.

    Args:
        count: Number of records to generate.

    Returns:
        List of dicts, each representing one SharePoint document.
    """
    disciplines = list(SP_DEPARTMENTS.keys())
    records = []

    for _ in range(count):
        discipline = random.choices(disciplines, weights=DISCIPLINE_WEIGHTS, k=1)[0]
        doc_type   = random.choice(DISCIPLINE_DOC_TYPES[discipline])

        version_seq = random.choice(SP_VERSION_SEQUENCES)
        version     = version_seq[random.randint(0, len(version_seq) - 1)]

        status = random.choice(SP_STATUSES)

        approval_class, sp_approval_type = get_approval_type(doc_type)
        classification = get_sp_classification(approval_class, doc_type)

        cert_required = approval_class == "CERTIFICATION" or (
            approval_class == "CUSTOMER" and random.random() < 0.15
        )
        cert_body = random.choice(CERTIFYING_BODIES) if cert_required else ""

        created_date  = random_past_date(90, 400)
        modified_date = created_date + timedelta(days=random.randint(1, 60))
        if modified_date > TODAY:
            modified_date = TODAY

        sp_id    = next_sp_item_id()
        file_fmt = get_file_format(doc_type)
        file_name = make_sp_file_name(doc_type, COMPANY, version, file_fmt)

        records.append({
            # -- SharePoint identity (item ID is an integer, not a structured doc number)
            "sp_instance":        INSTANCE,
            "sp_item_id":         sp_id,
            "sp_file_name":       file_name,
            # -- Status (SharePoint informal vocabulary)
            "sp_status":          status,
            "sp_version":         version,        # decimal format: 1.0, 1.1, 2.0
            # -- Timestamps (MM/DD/YYYY — en-US SharePoint regional setting)
            "sp_created":         to_us_date(created_date),
            "sp_modified":        to_us_date(modified_date),
            # -- People
            "sp_created_by":      fake.name(),    # blanked for ~2 records (DQ)
            "sp_modified_by":     fake.name(),
            "sp_reviewer":        fake.name(),
            # -- Classification (SharePoint verbose field names and vocabulary)
            "sp_department":      SP_DEPARTMENTS[discipline],  # verbose — needs normalisation
            "sp_document_category": doc_type,
            "sp_company":         COMPANY,        # blanked for ~2 records (DQ)
            "sp_classification":  classification,
            "sp_approval_type":   sp_approval_type,
            "sp_certifying_body": cert_body,
            "sp_certification_required": cert_required,
            "sp_file_type":       file_fmt,
        })

    return records


def inject_dq_issues(records: list) -> list:
    """
    Inject deliberate data quality problems into a small subset of records.

    WHY: In real SharePoint deployments, metadata columns are optional by
    default. Users skip them. Company and author fields are frequently
    blank because there is no enforcement mechanism in SharePoint.

    DQ issues injected:
      - 2 records: sp_created_by set to "" (missing mandatory field)
      - 2 records: sp_company set to ""    (missing mandatory field)

    Note: sp_department normalisation ("Instrumentation & Control",
    "Civil & Structural") is a structural issue present in ALL relevant
    records — not injected randomly. The STAGED layer handles it via a
    lookup table, not by flagging individual records.

    Args:
        records: Full list of generated records.

    Returns:
        Same list with DQ issues injected.
    """
    indices = random.sample(range(len(records)), 4)
    missing_created_by = indices[:2]
    missing_company    = indices[2:]

    for idx in missing_created_by:
        records[idx]["sp_created_by"] = ""
        _log("DQ-INJECT", f"Record sp_item_id={records[idx]['sp_item_id']}: sp_created_by blanked (MISSING_MANDATORY_FIELD)")

    for idx in missing_company:
        records[idx]["sp_company"] = ""
        _log("DQ-INJECT", f"Record sp_item_id={records[idx]['sp_item_id']}: sp_company blanked (MISSING_MANDATORY_FIELD)")

    return records


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

FIELDNAMES = [
    "sp_instance", "sp_item_id", "sp_file_name",
    "sp_status", "sp_version",
    "sp_created", "sp_modified",
    "sp_created_by", "sp_modified_by", "sp_reviewer",
    "sp_department", "sp_document_category",
    "sp_company", "sp_classification", "sp_approval_type",
    "sp_certifying_body", "sp_certification_required",
    "sp_file_type",
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
    print("\n--- SharePoint Source Summary -----------------------------------")
    print(f"Total records : {len(records)}")

    for label, key in [
        ("Status",          "sp_status"),
        ("Version",         "sp_version"),
        ("Department",      "sp_department"),
        ("Approval type",   "sp_approval_type"),
        ("Classification",  "sp_classification"),
    ]:
        counts = Counter(r[key] for r in records)
        print(f"\n{label}:")
        for k, v in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"  {k:<40} {v:>3}")

    missing_created_by = sum(1 for r in records if not r["sp_created_by"])
    missing_company    = sum(1 for r in records if not r["sp_company"])
    dept_normalisation = sum(
        1 for r in records if r["sp_department"] in ("Instrumentation & Control", "Civil & Structural")
    )
    print(f"\nDQ issues injected:")
    print(f"  Missing sp_created_by (MISSING_MANDATORY_FIELD)  : {missing_created_by}")
    print(f"  Missing sp_company (MISSING_MANDATORY_FIELD)     : {missing_company}")
    print(f"\nDQ issues structural (all relevant records):")
    print(f"  Dept requiring normalisation (NORMALISATION_REQUIRED): {dept_normalisation}")
    print("-" * 53)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    script_dir  = Path(__file__).parent
    output_path = script_dir / "sharepoint_source.csv"

    _log("LOAD", f"Generating {DOC_COUNT} SharePoint source records for {INSTANCE}")

    records = generate_records(DOC_COUNT)
    records = inject_dq_issues(records)

    write_csv(records, output_path)
    print_summary(records)
