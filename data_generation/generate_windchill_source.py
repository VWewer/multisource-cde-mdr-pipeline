"""
generate_windchill_source.py
multisource-cde-mdr-pipeline | v2 pipeline — Windchill source generator

Generates source-native Windchill data for two partner instances:
  - Windchill_PartnerA  (Alpha Engineering GmbH)   — 15 documents
  - Windchill_PartnerB  (Beta Konstruktion AS)      — 15 documents

WHY a separate script per source system:
  In a real CDE pipeline each source has its own connector, its own schema,
  and its own failure modes. Generating them separately demonstrates that
  understanding — one monolithic generator does not.

Windchill source-native characteristics:
  - Field names carry the wc_ prefix (Windchill API naming convention)
  - Lifecycle states use Windchill vocabulary (In Work / Released / Baselined...)
  - Revisions are alphabetic: A, B, C (Windchill standard)
  - Timestamps are ISO 8601 UTC — Windchill REST API returns this format
  - Discipline codes are short abbreviations: MECH, ELEC, INST, CIVIL

Deliberately injected DQ issues (for staged-layer detection):
  - ~3 records missing wc_author        → DQ flag: MISSING_MANDATORY_FIELD
  - ~2 records wc_discipline_code = "MECHANICAL" instead of "MECH"
                                         → DQ flag: NORMALISATION_REQUIRED

Output: <script_dir>/windchill_source.csv  (30 rows)

Run from anywhere — paths are relative to this script's location.
"""

import csv
import random
import uuid
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from faker import Faker

fake = Faker()
random.seed(42)       # fixed seed — same output every run
Faker.seed(42)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_CODE = "PROJ1"

# Windchill lifecycle states — Windchill native vocabulary
# These differ from the canonical statuses in the STAGED layer.
# The staged harmonisation script maps these to the canonical set.
WINDCHILL_LIFECYCLE_STATES = [
    "In Work",
    "In Review",
    "Released",
    "Revised",
    "Baselined",
    "Cancelled",
    "Issued for Client",
    "Authority Approved",
]

# Alphabetic revision sequence — Windchill standard
REVISION_SEQUENCES = [
    ["A"],
    ["A", "B"],
    ["A", "B", "C"],
    ["A", "B", "C", "D"],
]

# Windchill discipline codes — short abbreviations
# NOTE: two records will intentionally use "MECHANICAL" (a known DQ issue)
DISCIPLINE_CODES = {
    "Mechanical":      "MECH",
    "Electrical":      "ELEC",
    "Instrumentation": "INST",
    "Civil":           "CIVIL",
}

# Document types per discipline — realistic Windchill content
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

# Windchill confidentiality vocabulary (differs from SharePoint and Aveva)
WC_CONFIDENTIALITY = ["Public", "Internal", "Restricted", "Confidential"]

# Approval classes — these map to the canonical set used downstream
APPROVAL_CLASSES = ["INTERNAL", "CUSTOMER", "AUTHORITY", "CERTIFICATION"]

CERTIFYING_BODIES = [
    "TUV SUD", "TUV Rheinland", "DNV", "Lloyd's Register",
    "Bureau Veritas", "SGS", "Intertek",
]

# Windchill file format vocabulary
WC_FILE_FORMATS = {
    "drawing_types": ["DWG", "PDF"],
    "list_types":    ["XLSX", "PDF"],
    "default":       "PDF",
}

# Two Windchill instances — separate engineering partners
INSTANCES = [
    {
        "instance":  "Windchill_PartnerA",
        "company":   "Alpha Engineering GmbH",
        "originator_code": "ALPHAENG",
        "count":     15,
        # PartnerA specialises in Mechanical + Instrumentation
        "discipline_weights": [0.40, 0.15, 0.35, 0.10],
    },
    {
        "instance":  "Windchill_PartnerB",
        "company":   "Beta Konstruktion AS",
        "originator_code": "BETAKONS",
        "count":     15,
        # PartnerB specialises in Electrical + Civil
        "discipline_weights": [0.15, 0.40, 0.20, 0.25],
    },
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


def to_utc_iso(d: date) -> str:
    """
    Convert a date to an ISO 8601 UTC timestamp string.
    Windchill REST API always returns this format: 2025-01-15T09:23:11Z
    """
    dt = datetime(
        d.year, d.month, d.day,
        random.randint(6, 18), random.randint(0, 59), random.randint(0, 59),
        tzinfo=timezone.utc,
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_file_format(doc_type: str) -> str:
    """Choose a realistic file format based on document type."""
    drawing_keywords = ["Drawing", "Diagram", "Layout", "Isometric", "P&ID"]
    list_keywords    = ["Schedule", "List", "Index", "Take-Off", "Matrix"]
    if any(k in doc_type for k in drawing_keywords):
        return random.choice(WC_FILE_FORMATS["drawing_types"])
    if any(k in doc_type for k in list_keywords):
        return random.choice(WC_FILE_FORMATS["list_types"])
    return WC_FILE_FORMATS["default"]


def get_approval_class(doc_type: str) -> str:
    """
    Assign approval class based on document type.
    Some types always require authority or certification sign-off.
    """
    authority_types    = {"HAZOP Report", "SIL Assessment", "Geotechnical Report", "Civil Specification"}
    cert_types         = {
        "Pump Datasheet", "Heat Exchanger Datasheet", "Structural Calculation",
        "Structural Steel Drawing", "Foundation Drawing",
        "Mechanical Completion Certificate", "Protection Relay Setting",
    }
    customer_types     = {
        "P&ID", "Cause & Effect Matrix", "Control Narrative", "ITP",
        "Equipment Layout", "Single Line Diagram",
    }
    if doc_type in authority_types:   return "AUTHORITY"
    if doc_type in cert_types:        return "CERTIFICATION"
    if doc_type in customer_types:    return "CUSTOMER"
    return random.choices(
        ["INTERNAL", "CUSTOMER", "AUTHORITY", "CERTIFICATION"],
        weights=[0.45, 0.35, 0.12, 0.08], k=1
    )[0]


def get_confidentiality(approval_class: str, doc_type: str) -> str:
    """Map approval class and doc type to a Windchill confidentiality label."""
    confidential_types = {
        "Geotechnical Report", "HAZOP Report", "SIL Assessment",
        "Control Narrative", "Cause & Effect Matrix",
    }
    restricted_types = {
        "Equipment Datasheet", "Pump Datasheet", "Heat Exchanger Datasheet",
        "Instrument Datasheet",
    }
    if doc_type in confidential_types or approval_class == "AUTHORITY":
        return "Confidential"
    if doc_type in restricted_types or approval_class == "CERTIFICATION":
        return random.choice(["Restricted", "Confidential"])
    if approval_class == "INTERNAL":
        return "Internal"
    return random.choices(["Internal", "Confidential"], weights=[0.75, 0.25], k=1)[0]


# ---------------------------------------------------------------------------
# Record generation
# ---------------------------------------------------------------------------

# Sequence counters — one per discipline, shared across both instances
# (discipline numbering is project-wide, not per-instance)
_discipline_counters = {d: 0 for d in DISCIPLINE_CODES}

def next_wc_doc_number(discipline: str) -> str:
    """Generate the next sequential Windchill document number for a discipline."""
    _discipline_counters[discipline] += 1
    return f"DOC-{DISCIPLINE_CODES[discipline]}-{_discipline_counters[discipline]:03d}"

# Instance-level ID counter
_instance_counters = {inst["instance"]: 0 for inst in INSTANCES}

def next_wc_id(instance: str) -> str:
    """Generate the next sequential Windchill internal object ID."""
    _instance_counters[instance] += 1
    prefix = "WCA" if "PartnerA" in instance else "WCB"
    return f"{prefix}-{_instance_counters[instance]:06d}"


def generate_instance_records(instance_cfg: dict) -> list:
    """
    Generate all records for one Windchill instance.

    Args:
        instance_cfg: dict with keys instance, company, originator_code,
                      count, discipline_weights.

    Returns:
        List of dicts, each representing one Windchill source document.
    """
    disciplines = list(DISCIPLINE_CODES.keys())
    records = []

    for i in range(instance_cfg["count"]):
        discipline = random.choices(disciplines, weights=instance_cfg["discipline_weights"], k=1)[0]
        doc_type   = random.choice(DISCIPLINE_DOC_TYPES[discipline])

        rev_seq  = random.choice(REVISION_SEQUENCES)
        revision = rev_seq[random.randint(0, len(rev_seq) - 1)]

        lifecycle_state = random.choice(WINDCHILL_LIFECYCLE_STATES)

        approval_class = get_approval_class(doc_type)
        confidentiality = get_confidentiality(approval_class, doc_type)

        # Certification fields — only populated for CERTIFICATION class
        cert_required = approval_class == "CERTIFICATION" or (
            approval_class == "CUSTOMER" and random.random() < 0.15
        )
        cert_body    = random.choice(CERTIFYING_BODIES) if cert_required else ""

        created_date  = random_past_date(90, 400)
        modified_date = created_date + timedelta(days=random.randint(1, 60))
        if modified_date > TODAY:
            modified_date = TODAY

        wc_id      = next_wc_id(instance_cfg["instance"])
        doc_number = next_wc_doc_number(discipline)

        # Discipline code: use standard abbreviation.
        # The DQ injection below will overwrite ~2 records to "MECHANICAL" later.
        disc_code = DISCIPLINE_CODES[discipline]

        # Author: populated for most records.
        # The DQ injection below will blank ~3 records later.
        author = fake.name()

        records.append({
            # -- Windchill internal identity
            "wc_instance":            instance_cfg["instance"],
            "wc_id":                  wc_id,
            "wc_doc_number":          doc_number,
            # -- Status (Windchill vocabulary)
            "wc_lifecycle_state":     lifecycle_state,
            "wc_revision":            revision,      # alphabetic: A, B, C
            # -- Timestamps (ISO 8601 UTC — Windchill API standard)
            "wc_created_at":          to_utc_iso(created_date),
            "wc_modified_at":         to_utc_iso(modified_date),
            # -- People
            "wc_author":              author,        # blanked for ~3 records (DQ)
            "wc_co_author":           fake.name() if random.random() < 0.4 else "",
            "wc_reviewer":            fake.name(),
            "wc_approver":            fake.name(),
            # -- Classification (Windchill field names and vocabulary)
            "wc_discipline_code":     disc_code,     # "MECHANICAL" injected for ~2 records (DQ)
            "wc_document_type":       doc_type,
            "wc_originator_company":  instance_cfg["company"],
            "wc_originator_code":     instance_cfg["originator_code"],
            "wc_confidentiality":     confidentiality,
            "wc_approval_class":      approval_class,
            "wc_certifying_body":     cert_body,
            "wc_certification_required": cert_required,
            "wc_file_format":         get_file_format(doc_type),
        })

    return records


def inject_dq_issues(records: list) -> list:
    """
    Inject deliberate data quality problems into a small subset of records.

    WHY: These issues are realistic. Windchill data entry is manual — authors
    get missed. Discipline codes are sometimes typed in full by mistake.
    The STAGED layer must detect and flag these before they reach the MDR.

    DQ issues injected:
      - 3 records: wc_author set to "" (missing mandatory field)
      - 2 records: wc_discipline_code set to "MECHANICAL" (non-standard — should be "MECH")

    Args:
        records: Full list of generated records.

    Returns:
        Same list with DQ issues injected.
    """
    # Pick 3 distinct random indices for missing author
    missing_author_indices = random.sample(range(len(records)), 3)
    for idx in missing_author_indices:
        records[idx]["wc_author"] = ""
        _log("DQ-INJECT", f"Record {records[idx]['wc_id']}: wc_author blanked (MISSING_MANDATORY_FIELD)")

    # Pick 2 distinct indices (not already used) for bad discipline code
    remaining = [i for i in range(len(records)) if i not in missing_author_indices]
    bad_disc_indices = random.sample(remaining, 2)
    for idx in bad_disc_indices:
        records[idx]["wc_discipline_code"] = "MECHANICAL"   # non-standard — should be "MECH"
        _log("DQ-INJECT", f"Record {records[idx]['wc_id']}: wc_discipline_code='MECHANICAL' (NORMALISATION_REQUIRED)")

    return records


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

FIELDNAMES = [
    "wc_instance", "wc_id", "wc_doc_number",
    "wc_lifecycle_state", "wc_revision",
    "wc_created_at", "wc_modified_at",
    "wc_author", "wc_co_author", "wc_reviewer", "wc_approver",
    "wc_discipline_code", "wc_document_type",
    "wc_originator_company", "wc_originator_code",
    "wc_confidentiality", "wc_approval_class",
    "wc_certifying_body", "wc_certification_required",
    "wc_file_format",
]


def write_csv(records: list, path: Path) -> None:
    """Write records to CSV. Always UTF-8 with BOM for Excel compatibility."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(records)
    _log("SAVE", f"Written {len(records)} records -> {path}")


def print_summary(records: list) -> None:
    """Print a plain-text summary of what was generated."""
    from collections import Counter
    print("\n--- Windchill Source Summary ------------------------------------")
    print(f"Total records : {len(records)}")

    for label, key in [
        ("Instance",          "wc_instance"),
        ("Discipline code",   "wc_discipline_code"),
        ("Lifecycle state",   "wc_lifecycle_state"),
        ("Revision",          "wc_revision"),
        ("Approval class",    "wc_approval_class"),
        ("Confidentiality",   "wc_confidentiality"),
    ]:
        counts = Counter(r[key] for r in records)
        print(f"\n{label}:")
        for k, v in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"  {k:<35} {v:>3}")

    missing_author = sum(1 for r in records if not r["wc_author"])
    bad_disc       = sum(1 for r in records if r["wc_discipline_code"] == "MECHANICAL")
    print(f"\nDQ issues injected:")
    print(f"  Missing wc_author (MISSING_MANDATORY_FIELD)   : {missing_author}")
    print(f"  wc_discipline_code='MECHANICAL' (NORMALISATION): {bad_disc}")
    print("-" * 53)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    script_dir = Path(__file__).parent
    output_path = script_dir / "windchill_source.csv"

    _log("LOAD", "Generating Windchill source data for PartnerA and PartnerB")

    all_records = []
    for inst in INSTANCES:
        _log("LOAD", f"Generating {inst['count']} records for {inst['instance']}")
        records = generate_instance_records(inst)
        all_records.extend(records)

    _log("LOAD", f"Total records before DQ injection: {len(all_records)}")

    all_records = inject_dq_issues(all_records)

    write_csv(all_records, output_path)
    print_summary(all_records)
