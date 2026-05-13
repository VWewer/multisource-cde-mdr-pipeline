"""
generate_staged_layer.py
multisource-cde-mdr-pipeline | v2 pipeline — STAGED layer (harmonisation engine)

This script IS the pipeline. It reads three source-native CSVs, harmonises
them into a single canonical schema, detects data quality issues, and writes
four output files that everything downstream depends on.

WHY this exists as a separate layer:
  Each source system speaks a different language — different field names, date
  formats, revision conventions, status vocabularies, and confidentiality labels.
  A pipeline that silently absorbs those differences into a single schema is not
  a pipeline; it is a merge. A real CDE pipeline makes the transformation
  explicit, detectable, and auditable. That is what this script does.

Inputs (must exist before running):
  windchill_source.csv   — 30 rows, Windchill-native schema
  sharepoint_source.csv  — 20 rows, SharePoint-native schema
  aveva_source.csv       — 10 rows, Aveva-native schema

Outputs:
  raw_documents.csv          — 60 rows, harmonised canonical schema
                               (replaces the old generate_raw_layer.py output;
                               generate_mdr_layer.py reads this file)
  staged_events.csv          — ~471 rows, document lifecycle event log
  staged_cross_reference.csv — 60 rows, (source_system, source_native_id) -> mdr_id
  staged_dq_flags.csv        — ~15 rows, quality issues detected during harmonisation

DQ issues detected and flagged:
  MISSING_MANDATORY_FIELD  — required field is blank in source
  NORMALISATION_REQUIRED   — field value present but uses non-standard vocabulary
  REVISION_FORMAT_MISMATCH — source revision format differs from canonical (A/B/C)
  TIMESTAMP_PARSE_ERROR    — date field could not be parsed

Run from anywhere — paths are relative to this script's location.
"""

import csv
import random
import uuid
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from faker import Faker

fake = Faker()
random.seed(99)      # same seed as v1 staged layer — event generation stays consistent
Faker.seed(99)

# ---------------------------------------------------------------------------
# Canonical status vocabulary
# ---------------------------------------------------------------------------

# Windchill lifecycle state -> canonical status
WC_STATUS_MAP = {
    "In Work":            "IN_PROGRESS",
    "In Review":          "UNDER_REVIEW",
    "Released":           "APPROVED_FINAL",
    "Revised":            "REVISION_REQUIRED",
    "Baselined":          "APPROVED_DRAFT",
    "Cancelled":          "ON_HOLD",
    "Issued for Client":  "APPROVED_CUSTOMER",
    "Authority Approved": "APPROVED_AUTHORITY",
}

# SharePoint status -> canonical status
SP_STATUS_MAP = {
    "Draft":          "IN_PROGRESS",
    "Pending Review": "SUBMITTED",
    "Under Review":   "UNDER_REVIEW",
    "Approved":       "APPROVED_DRAFT",
    "Client Approved":"APPROVED_CUSTOMER",
    "Final Approved": "APPROVED_FINAL",
    "Rejected":       "REVISION_REQUIRED",
    "On Hold":        "ON_HOLD",
}

# Aveva document status -> canonical status
AVEVA_STATUS_MAP = {
    "Issued for Review":         "SUBMITTED",
    "In Progress":               "IN_PROGRESS",
    "Approved for Construction": "APPROVED_FINAL",
    "Returned for Comment":      "REVISION_REQUIRED",
    "Superseded":                "SUPERSEDED",
    "For Information":           "APPROVED_DRAFT",
    "Authority Issue":           "APPROVED_AUTHORITY",
    "Certified":                 "APPROVED_CERTIFICATION",
}

# Canonical status -> ISO 19650-2 suitability code
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

TERMINAL_STATUSES = {
    "APPROVED_FINAL", "APPROVED_CUSTOMER", "APPROVED_AUTHORITY",
    "APPROVED_CERTIFICATION", "SUPERSEDED"
}

# ---------------------------------------------------------------------------
# Discipline normalisation
# Each source uses a different vocabulary. All map to the same four canonical
# disciplines. Entries that require normalisation are flagged as DQ issues.
# ---------------------------------------------------------------------------

# Windchill discipline code -> canonical discipline name
WC_DISCIPLINE_MAP = {
    "MECH":        "Mechanical",
    "ELEC":        "Electrical",
    "INST":        "Instrumentation",
    "CIVIL":       "Civil",
    # Non-standard entries — should produce NORMALISATION_REQUIRED flag
    "MECHANICAL":  "Mechanical",
    "ELECTRICAL":  "Electrical",
    "CIVIL ENG":   "Civil",
}

# Windchill standard codes — anything NOT in this set is a normalisation issue
WC_STANDARD_CODES = {"MECH", "ELEC", "INST", "CIVIL"}

# SharePoint department -> canonical discipline name
SP_DEPARTMENT_MAP = {
    "Mechanical Engineering":    "Mechanical",
    "Electrical Engineering":    "Electrical",
    "Instrumentation & Control": "Instrumentation",  # requires normalisation
    "Civil & Structural":        "Civil",             # requires normalisation
}

# SharePoint "standard" departments (none of them match canonical exactly —
# all SP department values go through normalisation, but only the two below
# are flagged as non-obvious mappings worth surfacing to the DC)
SP_FLAG_DEPARTMENTS = {"Instrumentation & Control", "Civil & Structural"}

# Aveva discipline -> canonical discipline name
AVEVA_DISCIPLINE_MAP = {
    "Mechanical":        "Mechanical",
    "Electrical":        "Electrical",
    "I&C":               "Instrumentation",    # requires normalisation
    "Civil & Structural":"Civil",              # requires normalisation
}

AVEVA_FLAG_DISCIPLINES = {"I&C", "Civil & Structural"}

# Canonical discipline -> ISO 19650-2 role code
DISCIPLINE_ROLE_CODES = {
    "Mechanical":      "ME",
    "Electrical":      "EL",
    "Instrumentation": "IN",
    "Civil":           "CI",
}

# ---------------------------------------------------------------------------
# Confidentiality normalisation
# Three different vocabularies map to four canonical levels.
# ---------------------------------------------------------------------------

WC_CONFIDENTIALITY_MAP = {
    "Public":       "PUBLIC",
    "Internal":     "INTERNAL",
    "Restricted":   "RESTRICTED",
    "Confidential": "CONFIDENTIAL",
}

SP_CONFIDENTIALITY_MAP = {
    "Public":           "PUBLIC",
    "Internal Use Only":"INTERNAL",    # "Use Only" dropped — same concept
    "Confidential":     "CONFIDENTIAL",
}

AVEVA_CONFIDENTIALITY_MAP = {
    "Unrestricted":      "PUBLIC",
    "Restricted":        "RESTRICTED",
    "Confidential":      "CONFIDENTIAL",
    "Highly Confidential":"CONFIDENTIAL",   # mapped down; flagged for DC review
}

# Aveva entries that map to CONFIDENTIAL but signal a higher original class
AVEVA_DOWNGRADE_CONFIDENTIALITY = {"Highly Confidential"}

# ---------------------------------------------------------------------------
# Revision normalisation
# Windchill: A/B/C (canonical — no change needed)
# SharePoint: 1.0/1.1/2.0 (major version -> letter)
# Aveva: 0/1/2 (integer -> letter)
# ---------------------------------------------------------------------------

def normalise_sp_version(version: str) -> str:
    """
    Convert SharePoint decimal version to canonical alphabetic revision.

    SharePoint major version maps to a letter (1->A, 2->B, 3->C).
    Minor versions (1.1, 1.2) collapse to the same letter as the major.
    This is an intentional simplification — the source version is preserved
    in the cross-reference table for full traceability.

    Args:
        version: SharePoint version string, e.g. "1.0", "1.1", "2.0"

    Returns:
        Canonical revision letter, e.g. "A", "B"
    """
    try:
        major = int(float(version))
        # Map 1->A, 2->B, 3->C, 4->D (chr(64 + n) = A,B,C,D...)
        return chr(64 + max(1, major))
    except (ValueError, TypeError):
        return "A"   # default if unparseable


def normalise_aveva_revision(revision_no) -> str:
    """
    Convert Aveva numeric revision to canonical alphabetic revision.

    Aveva uses 0-based integers (0=first issue, 1=first revision...).
    Canonical uses letters starting at A.

    Args:
        revision_no: Integer or string, e.g. 0, 1, 2

    Returns:
        Canonical revision letter, e.g. "A", "B", "C"
    """
    try:
        n = int(revision_no)
        return chr(65 + max(0, n))   # 0->A, 1->B, 2->C...
    except (ValueError, TypeError):
        return "A"

# ---------------------------------------------------------------------------
# Date parsing
# Each source uses a different date format. All normalise to ISO date string.
# ---------------------------------------------------------------------------

def parse_wc_date(ts: str) -> str:
    """
    Parse Windchill ISO 8601 UTC timestamp to ISO date string.

    Windchill format: 2025-01-15T09:23:11Z
    Output:          2025-01-15

    Args:
        ts: Windchill timestamp string.

    Returns:
        ISO date string (YYYY-MM-DD), or empty string on failure.
    """
    try:
        return ts[:10]   # first 10 chars of ISO 8601 are always YYYY-MM-DD
    except Exception:
        return ""


def parse_sp_date(ds: str) -> str:
    """
    Parse SharePoint MM/DD/YYYY date to ISO date string.

    SharePoint en-US regional format: 01/15/2025
    Output:                           2025-01-15

    Args:
        ds: SharePoint date string.

    Returns:
        ISO date string (YYYY-MM-DD), or empty string on failure.
    """
    try:
        d = datetime.strptime(ds.strip(), "%m/%d/%Y")
        return d.strftime("%Y-%m-%d")
    except Exception:
        return ""


def parse_aveva_date(ds: str) -> str:
    """
    Parse Aveva DD.MM.YYYY date to ISO date string.

    Aveva European format: 15.01.2025
    Output:                2025-01-15

    Note: DD.MM.YYYY and MM.DD.YYYY are ambiguous for days <= 12.
    We assume DD.MM.YYYY because the source system (Delta Technics BV)
    uses European locale — this assumption is recorded in the DQ flag.

    Args:
        ds: Aveva date string (may include time: "15.01.2025 09:23")

    Returns:
        ISO date string (YYYY-MM-DD), or empty string on failure.
    """
    try:
        # Strip time component if present (Aveva last_updated includes HH:MM)
        date_part = ds.strip().split(" ")[0]
        d = datetime.strptime(date_part, "%d.%m.%Y")
        return d.strftime("%Y-%m-%d")
    except Exception:
        return ""

# ---------------------------------------------------------------------------
# Document type -> ISO 19650-2 type code
# ---------------------------------------------------------------------------

TYPE_CODE_MAP = {
    "Drawing":     "DR", "Diagram": "DR", "Layout": "DR",
    "Isometric":   "DR", "P&ID":    "DR",
    "Specification": "SP",
    "Calculation": "CA",
    "Report":      "RP", "Assessment": "RP", "Narrative": "RP",
    "Datasheet":   "DS",
    "Schedule":    "SH", "List": "SH", "Index": "SH",
    "Take-Off":    "SH", "Matrix": "SH",
    "Certificate": "CT", "ITP":   "CT",
}

def get_type_code(doc_type: str) -> str:
    """Return the ISO 19650-2 type code for a document type string."""
    for keyword, code in TYPE_CODE_MAP.items():
        if keyword in doc_type:
            return code
    return "RP"   # default: report

# ---------------------------------------------------------------------------
# Canonical ID construction
# ---------------------------------------------------------------------------

PROJECT_CODE = "PROJ1"

# Originator codes — used in the ISO 19650 canonical ID
ORIGINATOR_CODES = {
    "Alpha Engineering GmbH": "ALPHAENG",
    "Beta Konstruktion AS":   "BETAKONS",
    "Gamma EPC Ltd":          "GAMMAEPC",
    "Delta Technics BV":      "DELTATECH",
}

# Volume code: ZZ = whole project (default; we don't have area data in all sources)
DEFAULT_VOLUME = "ZZ"
DEFAULT_LEVEL  = "00"

# Sequence counters per discipline — reset each pipeline run
_mdr_seq_counters = {"Mechanical": 0, "Electrical": 0, "Instrumentation": 0, "Civil": 0}

def next_mdr_sequence(discipline: str) -> int:
    """Increment and return the next sequence number for a discipline."""
    _mdr_seq_counters[discipline] += 1
    return _mdr_seq_counters[discipline]


def build_mdr_id(originator_code: str, type_code: str, discipline: str, seq: int) -> str:
    """
    Construct the canonical MDR_ID following ISO 19650 naming convention.

    Format: {PROJECT}-{ORIGINATOR}-{VOLUME}-{TYPE}-{DISC_CODE}-{SEQ:06d}
    Example: PROJ1-ALPHAENG-ZZ-DR-ME-000042

    WHY this ID never changes: it is assigned once at STAGED layer ingestion
    and stored in the cross-reference table. Revision, status, and other
    mutable attributes are event record fields, not part of the identifier.

    Args:
        originator_code: Company code from ORIGINATOR_CODES.
        type_code:       ISO 19650 document type code (DR/SP/CA/RP/DS/SH/CT).
        discipline:      Canonical discipline name.
        seq:             Zero-padded sequence number within this discipline.

    Returns:
        Canonical MDR_ID string.
    """
    role_code = DISCIPLINE_ROLE_CODES.get(discipline, "XX")
    return f"{PROJECT_CODE}-{originator_code}-{DEFAULT_VOLUME}-{type_code}-{role_code}-{seq:06d}"


def build_iso19650_filename(
    originator_code: str, type_code: str, role_code: str, seq: int, revision: str
) -> str:
    """
    Assemble the full ISO 19650-2 filename including revision.

    The filename (unlike the MDR_ID) does include the revision because it
    represents a specific version of the document, not the document entity.

    Format: {PROJECT}-{ORIGINATOR}-{VOLUME}-{LEVEL}-{TYPE}-{ROLE}-{SEQ:06d}-{REV}
    Example: PROJ1-ALPHAENG-ZZ-00-DR-ME-000042-B
    """
    return (
        f"{PROJECT_CODE}-{originator_code}-{DEFAULT_VOLUME}-{DEFAULT_LEVEL}"
        f"-{type_code}-{role_code}-{seq:06d}-{revision}"
    )

# ---------------------------------------------------------------------------
# Deterministic document UUID
# Uses uuid5 (namespace + name) so the same source record always gets the
# same UUID regardless of run order. This is the correct pattern for a
# harmonisation pipeline — UUIDs must be stable across re-runs.
# ---------------------------------------------------------------------------

_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # DNS namespace

def stable_uuid(source_system: str, source_native_id: str) -> str:
    """
    Generate a stable UUID from the source system identity.

    Uses uuid5 so the output is deterministic: same inputs always produce
    the same UUID. This ensures document_id is stable across pipeline re-runs,
    which is critical for the cross-reference table to remain consistent.
    """
    name = f"{source_system}:{source_native_id}"
    return str(uuid.uuid5(_UUID_NAMESPACE, name))

# ---------------------------------------------------------------------------
# DQ flag builder
# ---------------------------------------------------------------------------

_dq_flag_counter = 0

def make_dq_flag(
    mdr_id: str,
    source_system: str,
    source_native_id: str,
    field_name: str,
    flag_type: str,
    flag_detail: str,
    original_value: str = "",
    suggested_value: str = "",
) -> dict:
    """
    Build a DQ flag record.

    Args:
        mdr_id:           Canonical MDR_ID of the affected document.
        source_system:    Source system name (Windchill/SharePoint/Aveva).
        source_native_id: Native ID in the source system.
        field_name:       Canonical field name that has the issue.
        flag_type:        One of MISSING_MANDATORY_FIELD / NORMALISATION_REQUIRED /
                          REVISION_FORMAT_MISMATCH / TIMESTAMP_PARSE_ERROR.
        flag_detail:      Human-readable description for the DC remediation queue.
        original_value:   What the source system provided.
        suggested_value:  What the pipeline recommends as the correct value.

    Returns:
        Dict representing one row in staged_dq_flags.csv.
    """
    global _dq_flag_counter
    _dq_flag_counter += 1
    return {
        "flag_id":          f"DQF-{_dq_flag_counter:04d}",
        "mdr_id":           mdr_id,
        "source_system":    source_system,
        "source_native_id": source_native_id,
        "field_name":       field_name,
        "flag_type":        flag_type,
        "flag_detail":      flag_detail,
        "original_value":   original_value,
        "suggested_value":  suggested_value,
        "resolved":         False,
        "resolved_by":      "",
        "resolved_at":      "",
    }


def make_transform_record(
    source_system: str,
    source_native_id: str,
    mdr_id: str,
    field_name: str,
    original_value: str,
    normalised_value: str,
    normalisation_rule: str,
    confidence: str = "HIGH",
) -> dict | None:
    """
    Build one transformation log record, or return None if no change occurred.

    The transformation log captures every silent, automated field mapping the
    STAGED layer applies.  Unlike DQ flags (which need human action), these
    transformations were applied with HIGH confidence and require no follow-up.
    The distinction between 'automated' and 'auditable' is the interview talking
    point: the pipeline makes its work visible even when no error was detected.

    Args:
        source_system:      Windchill / SharePoint / Aveva.
        source_native_id:   Native ID of the document in the source system.
        mdr_id:             ISO 19650 canonical ID assigned by this pipeline run.
        field_name:         Canonical field name that was transformed.
        original_value:     Raw value from the source system.
        normalised_value:   Value after transformation.
        normalisation_rule: Short code describing the rule applied (see constants below).
        confidence:         HIGH = deterministic lookup, MEDIUM = fallback/inference.

    Returns:
        Dict for one row in staged_transformation_log.csv, or None if the
        original and normalised values are identical (no transformation occurred).
    """
    # Only log when a real change happened — avoids noise from pass-through fields
    if str(original_value) == str(normalised_value):
        return None
    return {
        "source_system":      source_system,
        "source_native_id":   source_native_id,
        "mdr_id":             mdr_id,
        "field_name":         field_name,
        "original_value":     str(original_value),
        "normalised_value":   str(normalised_value),
        "normalisation_rule": normalisation_rule,
        "confidence":         confidence,
        # run_timestamp is set later in main() so all records share the same stamp
        "run_timestamp":      "",
    }


# Normalisation rule codes — used as the normalisation_rule value in the transform log.
# Keep these short and consistent; they become filter values in the dashboard.
RULE_DATE_DATETIME_TO_DATE  = "DATETIME_TO_DATE"       # ISO 8601 datetime -> date only (Windchill)
RULE_DATE_MMDDYYYY          = "DATE_FORMAT_MMDDYYYY"   # MM/DD/YYYY -> YYYY-MM-DD (SharePoint)
RULE_DATE_DDMMYYYY          = "DATE_FORMAT_DDMMYYYY"   # DD.MM.YYYY -> YYYY-MM-DD (Aveva)
RULE_DISCIPLINE_CODE        = "DISCIPLINE_CODE_TO_NAME" # MECH/ELEC/INST/CIVIL -> canonical name
RULE_DISCIPLINE_DEPT        = "DISCIPLINE_DEPT_TO_NAME" # SP department string -> canonical name
RULE_DISCIPLINE_AVEVA       = "DISCIPLINE_AVEVA_TO_NAME"# Aveva discipline -> canonical name
RULE_STATUS_VOCAB           = "STATUS_VOCABULARY"       # source status -> canonical status
RULE_CONFIDENTIALITY_VOCAB  = "CONFIDENTIALITY_VOCABULARY" # source class -> canonical class
RULE_REVISION_DECIMAL       = "REVISION_DECIMAL_TO_ALPHA"  # 1.0/2.0 -> A/B (SharePoint)
RULE_REVISION_INTEGER       = "REVISION_INTEGER_TO_ALPHA"  # 0/1/2 -> A/B/C (Aveva)
RULE_APPROVAL_TYPE          = "APPROVAL_TYPE_TO_CLASS"     # SP approval type -> class
RULE_FILE_FORMAT_CASE       = "FILE_FORMAT_UPPERCASE"       # pdf -> PDF

TRANSFORM_FIELDNAMES = [
    "source_system", "source_native_id", "mdr_id",
    "field_name", "original_value", "normalised_value",
    "normalisation_rule", "confidence", "run_timestamp",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TODAY = date(2026, 5, 10)

def _log(label: str, message: str) -> None:
    """Print a timestamped log line. ASCII only — no Unicode symbols."""
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] [{label}] {message}")


def to_utc_iso(d: date) -> str:
    """Convert a date to a random ISO 8601 UTC timestamp during working hours."""
    dt = datetime(
        d.year, d.month, d.day,
        random.randint(7, 17), random.randint(0, 59), random.randint(0, 59),
        tzinfo=timezone.utc,
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def add_days(d: date, n: int) -> date:
    """Add n days to a date."""
    return d + timedelta(days=n)

# ---------------------------------------------------------------------------
# Windchill harmonisation
# ---------------------------------------------------------------------------

def harmonise_windchill(row: dict, dq_flags: list, transform_log: list) -> dict:
    """
    Map one Windchill source record to the canonical schema.

    Detects and flags:
      - Missing wc_author             -> MISSING_MANDATORY_FIELD
      - Non-standard wc_discipline_code (e.g. MECHANICAL) -> NORMALISATION_REQUIRED

    Silent transformations logged to transform_log:
      - Discipline code -> canonical name  (DISCIPLINE_CODE_TO_NAME)
      - Lifecycle state -> canonical status (STATUS_VOCABULARY)
      - ISO 8601 datetime -> date only      (DATETIME_TO_DATE)
      - Confidentiality label -> canonical  (CONFIDENTIALITY_VOCABULARY)

    Args:
        row:           One row from windchill_source.csv.
        dq_flags:      Mutable list to append DQ flag records to.
        transform_log: Mutable list to append transformation log records to.

    Returns:
        Canonical record dict (one row for raw_documents.csv).
    """
    source_native_id = row["wc_id"]
    source_system    = "Windchill"

    # -- Discipline normalisation
    raw_disc_code  = row["wc_discipline_code"]
    discipline     = WC_DISCIPLINE_MAP.get(raw_disc_code, "Mechanical")
    type_code      = get_type_code(row["wc_document_type"])
    role_code      = DISCIPLINE_ROLE_CODES[discipline]
    seq            = next_mdr_sequence(discipline)
    originator_code = row["wc_originator_code"]

    mdr_id          = build_mdr_id(originator_code, type_code, discipline, seq)
    iso19650_fn     = build_iso19650_filename(originator_code, type_code, role_code, seq, row["wc_revision"])
    doc_uuid        = stable_uuid(source_system, source_native_id)
    canonical_status = WC_STATUS_MAP.get(row["wc_lifecycle_state"], "IN_PROGRESS")

    # -- Transformation log: discipline code and status vocabulary
    # Confidence = MEDIUM for non-standard codes (e.g. "MECHANICAL" instead of "MECH").
    # The pipeline can still map them via the extended lookup table, but the mapping
    # is not a standard abbreviation — it needs human sign-off, so a DQ flag is raised.
    # HIGH = unambiguous standard code; MEDIUM = plausible guess requiring confirmation.
    disc_confidence = "HIGH" if raw_disc_code in WC_STANDARD_CODES else "MEDIUM"
    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "discipline", raw_disc_code, discipline, RULE_DISCIPLINE_CODE, disc_confidence)
    if rec: transform_log.append(rec)

    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "canonical_status", row["wc_lifecycle_state"], canonical_status, RULE_STATUS_VOCAB)
    if rec: transform_log.append(rec)

    # -- DQ: non-standard discipline code
    if raw_disc_code not in WC_STANDARD_CODES:
        dq_flags.append(make_dq_flag(
            mdr_id, source_system, source_native_id,
            field_name="discipline",
            flag_type="NORMALISATION_REQUIRED",
            flag_detail=(
                f"wc_discipline_code '{raw_disc_code}' is not a standard Windchill code. "
                f"Expected one of: MECH, ELEC, INST, CIVIL. "
                f"Mapped to '{discipline}' with MEDIUM confidence — please confirm."
            ),
            original_value=raw_disc_code,
            suggested_value=discipline,
        ))

    # -- DQ: missing author
    author = row.get("wc_author", "").strip()
    if not author:
        dq_flags.append(make_dq_flag(
            mdr_id, source_system, source_native_id,
            field_name="author",
            flag_type="MISSING_MANDATORY_FIELD",
            flag_detail=(
                "wc_author is blank. Author is mandatory for document traceability. "
                "Please fill this field in the DC remediation queue."
            ),
        ))

    # -- Revision: Windchill uses A/B/C — no normalisation needed
    revision = row["wc_revision"]

    # -- Date: Windchill ISO 8601 datetime stripped to date only
    issue_date = parse_wc_date(row["wc_created_at"])
    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "issue_date", row["wc_created_at"], issue_date, RULE_DATE_DATETIME_TO_DATE)
    if rec: transform_log.append(rec)

    # -- Confidentiality
    confidentiality = WC_CONFIDENTIALITY_MAP.get(row["wc_confidentiality"], "INTERNAL")
    sensitive = confidentiality in ("RESTRICTED", "CONFIDENTIAL")
    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "confidentiality_class", row["wc_confidentiality"], confidentiality, RULE_CONFIDENTIALITY_VOCAB)
    if rec: transform_log.append(rec)

    return {
        # Identity
        "document_id":            doc_uuid,
        "source_system_id":       source_native_id,
        "source_system":          source_system,
        "source_system_instance": row["wc_instance"],
        # Status
        "source_status":          row["wc_lifecycle_state"],
        "canonical_status":       canonical_status,
        "iso19650_status_code":   ISO19650_STATUS_MAP.get(canonical_status, "S0"),
        # ISO 19650-2 naming
        "project_code":           PROJECT_CODE,
        "originator_code":        originator_code,
        "volume_code":            DEFAULT_VOLUME,
        "level_code":             DEFAULT_LEVEL,
        "type_code":              type_code,
        "role_code":              role_code,
        "iso19650_filename":      iso19650_fn,
        "mdr_id":                 mdr_id,
        # Document metadata
        "document_type":          row["wc_document_type"],
        "discipline":             discipline,
        "revision":               revision,
        "title":                  row["wc_document_type"],
        "description":            f"{row['wc_document_type']} for {discipline} scope, revision {revision}.",
        "file_format":            row["wc_file_format"],
        # Approval & certification
        "approval_class":         row["wc_approval_class"],
        "certification_required": row["wc_certification_required"],
        "certifying_body":        row["wc_certifying_body"],
        "certifying_body_contact": "",
        # Confidentiality
        "confidentiality_class":  confidentiality,
        "sensitive_information":  sensitive,
        # People
        "author":                 author,
        "co_author":              row.get("wc_co_author", ""),
        "originator_company":     row["wc_originator_company"],
        "reviewer":               row.get("wc_reviewer", ""),
        "approver":               row.get("wc_approver", ""),
        # Dates
        "issue_date":             issue_date,
        "ingested_at":            to_utc_iso(date.fromisoformat(issue_date) + timedelta(days=random.randint(1, 5)) if issue_date else TODAY),
    }


# ---------------------------------------------------------------------------
# SharePoint harmonisation
# ---------------------------------------------------------------------------

def harmonise_sharepoint(row: dict, dq_flags: list, transform_log: list) -> dict:
    """
    Map one SharePoint source record to the canonical schema.

    Detects and flags:
      - Missing sp_created_by                    -> MISSING_MANDATORY_FIELD
      - Missing sp_company                       -> MISSING_MANDATORY_FIELD
      - sp_department in SP_FLAG_DEPARTMENTS     -> NORMALISATION_REQUIRED
      - sp_version decimal format                -> REVISION_FORMAT_MISMATCH

    Silent transformations logged to transform_log:
      - Department string -> canonical discipline (DISCIPLINE_DEPT_TO_NAME)
      - SP status -> canonical status             (STATUS_VOCABULARY)
      - MM/DD/YYYY date -> ISO date               (DATE_FORMAT_MMDDYYYY)
      - Classification -> canonical class         (CONFIDENTIALITY_VOCABULARY)
      - Decimal version -> alpha revision         (REVISION_DECIMAL_TO_ALPHA)
      - Approval type -> approval class           (APPROVAL_TYPE_TO_CLASS)
      - File type -> uppercase                    (FILE_FORMAT_UPPERCASE)

    Args:
        row:           One row from sharepoint_source.csv.
        dq_flags:      Mutable list to append DQ flag records to.
        transform_log: Mutable list to append transformation log records to.

    Returns:
        Canonical record dict.
    """
    source_native_id = str(row["sp_item_id"])
    source_system    = "SharePoint"

    # -- Discipline normalisation
    raw_dept   = row["sp_department"]
    discipline = SP_DEPARTMENT_MAP.get(raw_dept, "Mechanical")
    type_code  = get_type_code(row["sp_document_category"])
    role_code  = DISCIPLINE_ROLE_CODES[discipline]
    seq        = next_mdr_sequence(discipline)

    # SharePoint has no originator code field — derive from company name
    company         = row.get("sp_company", "").strip() or "Gamma EPC Ltd"
    originator_code = ORIGINATOR_CODES.get(company, "GAMMAEPC")

    mdr_id      = build_mdr_id(originator_code, type_code, discipline, seq)
    revision    = normalise_sp_version(row["sp_version"])
    iso19650_fn = build_iso19650_filename(originator_code, type_code, role_code, seq, revision)
    doc_uuid    = stable_uuid(source_system, source_native_id)

    canonical_status = SP_STATUS_MAP.get(row["sp_status"], "IN_PROGRESS")

    # -- Transformation log: discipline, status, revision
    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "discipline", raw_dept, discipline, RULE_DISCIPLINE_DEPT)
    if rec: transform_log.append(rec)

    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "canonical_status", row["sp_status"], canonical_status, RULE_STATUS_VOCAB)
    if rec: transform_log.append(rec)

    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "revision", row["sp_version"], revision, RULE_REVISION_DECIMAL)
    if rec: transform_log.append(rec)

    # -- DQ: department requires normalisation
    if raw_dept in SP_FLAG_DEPARTMENTS:
        dq_flags.append(make_dq_flag(
            mdr_id, source_system, source_native_id,
            field_name="discipline",
            flag_type="NORMALISATION_REQUIRED",
            flag_detail=(
                f"sp_department '{raw_dept}' does not match canonical discipline vocabulary. "
                f"Mapped to '{discipline}' via lookup table — please confirm."
            ),
            original_value=raw_dept,
            suggested_value=discipline,
        ))

    # -- DQ: revision format mismatch
    dq_flags.append(make_dq_flag(
        mdr_id, source_system, source_native_id,
        field_name="revision",
        flag_type="REVISION_FORMAT_MISMATCH",
        flag_detail=(
            f"SharePoint version '{row['sp_version']}' uses decimal format (major.minor). "
            f"Canonical revision format is alphabetic (A/B/C). "
            f"Normalised to '{revision}' — minor version detail is lost."
        ),
        original_value=row["sp_version"],
        suggested_value=revision,
    ))

    # -- DQ: missing created_by (author)
    author = row.get("sp_created_by", "").strip()
    if not author:
        dq_flags.append(make_dq_flag(
            mdr_id, source_system, source_native_id,
            field_name="author",
            flag_type="MISSING_MANDATORY_FIELD",
            flag_detail=(
                "sp_created_by is blank. Author is mandatory for document traceability. "
                "Please fill this field in the DC remediation queue."
            ),
        ))

    # -- DQ: missing company
    if not row.get("sp_company", "").strip():
        dq_flags.append(make_dq_flag(
            mdr_id, source_system, source_native_id,
            field_name="originator_company",
            flag_type="MISSING_MANDATORY_FIELD",
            flag_detail=(
                "sp_company is blank. Originator company is required for ISO 19650 compliance. "
                "Please fill this field in the DC remediation queue."
            ),
        ))

    # -- Date: parse MM/DD/YYYY -> ISO date
    issue_date = parse_sp_date(row["sp_created"])
    if issue_date:
        rec = make_transform_record(source_system, source_native_id, mdr_id,
            "issue_date", row["sp_created"], issue_date, RULE_DATE_MMDDYYYY)
        if rec: transform_log.append(rec)
    if not issue_date:
        issue_date = TODAY.isoformat()
        dq_flags.append(make_dq_flag(
            mdr_id, source_system, source_native_id,
            field_name="issue_date",
            flag_type="TIMESTAMP_PARSE_ERROR",
            flag_detail=(
                f"Could not parse sp_created '{row['sp_created']}' as MM/DD/YYYY. "
                f"Defaulted to pipeline run date. Please correct the source value."
            ),
            original_value=row["sp_created"],
            suggested_value=TODAY.isoformat(),
        ))

    # -- Confidentiality
    confidentiality = SP_CONFIDENTIALITY_MAP.get(row["sp_classification"], "INTERNAL")
    sensitive = confidentiality in ("RESTRICTED", "CONFIDENTIAL")
    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "confidentiality_class", row["sp_classification"], confidentiality, RULE_CONFIDENTIALITY_VOCAB)
    if rec: transform_log.append(rec)

    # Map SharePoint approval type back to canonical approval class
    sp_to_canonical_approval = {
        "Internal":      "INTERNAL",
        "Client":        "CUSTOMER",
        "Authority":     "AUTHORITY",
        "Certification": "CERTIFICATION",
    }
    raw_approval_type = row.get("sp_approval_type", "Internal")
    approval_class = sp_to_canonical_approval.get(raw_approval_type, "INTERNAL")
    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "approval_class", raw_approval_type, approval_class, RULE_APPROVAL_TYPE)
    if rec: transform_log.append(rec)

    # File format: normalise to uppercase
    raw_file_format = row.get("sp_file_type", "pdf")
    file_format = raw_file_format.upper()
    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "file_format", raw_file_format, file_format, RULE_FILE_FORMAT_CASE)
    if rec: transform_log.append(rec)

    return {
        "document_id":            doc_uuid,
        "source_system_id":       source_native_id,
        "source_system":          source_system,
        "source_system_instance": row["sp_instance"],
        "source_status":          row["sp_status"],
        "canonical_status":       canonical_status,
        "iso19650_status_code":   ISO19650_STATUS_MAP.get(canonical_status, "S0"),
        "project_code":           PROJECT_CODE,
        "originator_code":        originator_code,
        "volume_code":            DEFAULT_VOLUME,
        "level_code":             DEFAULT_LEVEL,
        "type_code":              type_code,
        "role_code":              role_code,
        "iso19650_filename":      iso19650_fn,
        "mdr_id":                 mdr_id,
        "document_type":          row["sp_document_category"],
        "discipline":             discipline,
        "revision":               revision,
        "title":                  row["sp_document_category"],
        "description":            f"{row['sp_document_category']} for {discipline} scope, version {row['sp_version']}.",
        "file_format":            file_format,
        "approval_class":         approval_class,
        "certification_required": row.get("sp_certification_required", False),
        "certifying_body":        row.get("sp_certifying_body", ""),
        "certifying_body_contact": "",
        "confidentiality_class":  confidentiality,
        "sensitive_information":  sensitive,
        "author":                 author,
        "co_author":              "",
        "originator_company":     company,
        "reviewer":               row.get("sp_reviewer", ""),
        "approver":               "",
        "issue_date":             issue_date,
        "ingested_at":            to_utc_iso(date.fromisoformat(issue_date) + timedelta(days=random.randint(1, 5))),
    }


# ---------------------------------------------------------------------------
# Aveva harmonisation
# ---------------------------------------------------------------------------

def harmonise_aveva(row: dict, dq_flags: list, transform_log: list) -> dict:
    """
    Map one Aveva source record to the canonical schema.

    Detects and flags:
      - Missing aveva_prepared_by              -> MISSING_MANDATORY_FIELD
      - aveva_discipline in AVEVA_FLAG_DISCIPLINES -> NORMALISATION_REQUIRED
      - Numeric revision format (0/1/2)        -> REVISION_FORMAT_MISMATCH
      - DD.MM.YYYY date format                 -> noted in cross-ref, not a flag
        (it parses correctly so no error flag, but the format difference is
        documented in CLAUDE.md as a design decision)

    Silent transformations logged to transform_log:
      - Aveva discipline -> canonical discipline (DISCIPLINE_AVEVA_TO_NAME)
      - Aveva status -> canonical status         (STATUS_VOCABULARY)
      - DD.MM.YYYY date -> ISO date              (DATE_FORMAT_DDMMYYYY)
      - Security class -> canonical class        (CONFIDENTIALITY_VOCABULARY)
      - Integer revision -> alpha revision       (REVISION_INTEGER_TO_ALPHA)

    Args:
        row:           One row from aveva_source.csv.
        dq_flags:      Mutable list to append DQ flag records to.
        transform_log: Mutable list to append transformation log records to.

    Returns:
        Canonical record dict.
    """
    source_native_id = row["aveva_id"]
    source_system    = "Aveva"

    # -- Discipline normalisation
    raw_disc   = row["aveva_discipline"]
    discipline = AVEVA_DISCIPLINE_MAP.get(raw_disc, "Mechanical")
    type_code  = get_type_code(row["aveva_doc_type"])
    role_code  = DISCIPLINE_ROLE_CODES[discipline]
    seq        = next_mdr_sequence(discipline)

    company         = row.get("aveva_originator_org", "Delta Technics BV")
    originator_code = ORIGINATOR_CODES.get(company, "DELTATECH")

    revision    = normalise_aveva_revision(row["aveva_revision_no"])
    mdr_id      = build_mdr_id(originator_code, type_code, discipline, seq)
    iso19650_fn = build_iso19650_filename(originator_code, type_code, role_code, seq, revision)
    doc_uuid    = stable_uuid(source_system, source_native_id)

    canonical_status = AVEVA_STATUS_MAP.get(row["aveva_document_status"], "IN_PROGRESS")

    # -- Transformation log: discipline, status, revision
    # Aveva uses vendor-specific notation ("I&C") that doesn't match canonical names.
    # These are MEDIUM confidence — the mapping is well-understood but not a 1:1 code.
    disc_confidence = "MEDIUM" if raw_disc in AVEVA_FLAG_DISCIPLINES else "HIGH"
    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "discipline", raw_disc, discipline, RULE_DISCIPLINE_AVEVA, disc_confidence)
    if rec: transform_log.append(rec)

    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "canonical_status", row["aveva_document_status"], canonical_status, RULE_STATUS_VOCAB)
    if rec: transform_log.append(rec)

    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "revision", str(row["aveva_revision_no"]), revision, RULE_REVISION_INTEGER)
    if rec: transform_log.append(rec)

    # -- DQ: discipline requires normalisation
    if raw_disc in AVEVA_FLAG_DISCIPLINES:
        dq_flags.append(make_dq_flag(
            mdr_id, source_system, source_native_id,
            field_name="discipline",
            flag_type="NORMALISATION_REQUIRED",
            flag_detail=(
                f"aveva_discipline '{raw_disc}' does not match canonical discipline vocabulary. "
                f"Mapped to '{discipline}' with MEDIUM confidence — please confirm."
            ),
            original_value=raw_disc,
            suggested_value=discipline,
        ))

    # -- DQ: revision format mismatch
    dq_flags.append(make_dq_flag(
        mdr_id, source_system, source_native_id,
        field_name="revision",
        flag_type="REVISION_FORMAT_MISMATCH",
        flag_detail=(
            f"Aveva revision_no '{row['aveva_revision_no']}' uses integer format (0=first issue). "
            f"Canonical revision format is alphabetic (A/B/C). "
            f"Normalised to '{revision}'."
        ),
        original_value=str(row["aveva_revision_no"]),
        suggested_value=revision,
    ))

    # -- DQ: missing prepared_by (author)
    author = row.get("aveva_prepared_by", "").strip()
    if not author:
        dq_flags.append(make_dq_flag(
            mdr_id, source_system, source_native_id,
            field_name="author",
            flag_type="MISSING_MANDATORY_FIELD",
            flag_detail=(
                "aveva_prepared_by is blank. Author is mandatory for document traceability. "
                "Please fill this field in the DC remediation queue."
            ),
        ))

    # -- Date: parse DD.MM.YYYY -> ISO date
    issue_date = parse_aveva_date(row["aveva_issue_date"])
    if issue_date:
        rec = make_transform_record(source_system, source_native_id, mdr_id,
            "issue_date", row["aveva_issue_date"], issue_date, RULE_DATE_DDMMYYYY)
        if rec: transform_log.append(rec)
    if not issue_date:
        issue_date = TODAY.isoformat()
        dq_flags.append(make_dq_flag(
            mdr_id, source_system, source_native_id,
            field_name="issue_date",
            flag_type="TIMESTAMP_PARSE_ERROR",
            flag_detail=(
                f"Could not parse aveva_issue_date '{row['aveva_issue_date']}' as DD.MM.YYYY. "
                f"Defaulted to pipeline run date."
            ),
            original_value=row["aveva_issue_date"],
            suggested_value=TODAY.isoformat(),
        ))

    # -- Confidentiality
    raw_security    = row.get("aveva_security_class", "Unrestricted")
    confidentiality = AVEVA_CONFIDENTIALITY_MAP.get(raw_security, "PUBLIC")
    sensitive       = confidentiality in ("RESTRICTED", "CONFIDENTIAL")
    rec = make_transform_record(source_system, source_native_id, mdr_id,
        "confidentiality_class", raw_security, confidentiality, RULE_CONFIDENTIALITY_VOCAB)
    if rec: transform_log.append(rec)

    # Flag downgrade: "Highly Confidential" has no canonical equivalent
    if raw_security in AVEVA_DOWNGRADE_CONFIDENTIALITY:
        dq_flags.append(make_dq_flag(
            mdr_id, source_system, source_native_id,
            field_name="confidentiality_class",
            flag_type="NORMALISATION_REQUIRED",
            flag_detail=(
                f"Aveva security class '{raw_security}' has no direct canonical equivalent. "
                f"Mapped to 'CONFIDENTIAL' — DC should confirm if a higher classification applies."
            ),
            original_value=raw_security,
            suggested_value="CONFIDENTIAL",
        ))

    approval_class = row.get("aveva_approval_category", "INTERNAL")

    return {
        "document_id":            doc_uuid,
        "source_system_id":       source_native_id,
        "source_system":          source_system,
        "source_system_instance": row["aveva_instance"],
        "source_status":          row["aveva_document_status"],
        "canonical_status":       canonical_status,
        "iso19650_status_code":   ISO19650_STATUS_MAP.get(canonical_status, "S0"),
        "project_code":           PROJECT_CODE,
        "originator_code":        originator_code,
        "volume_code":            DEFAULT_VOLUME,
        "level_code":             DEFAULT_LEVEL,
        "type_code":              type_code,
        "role_code":              role_code,
        "iso19650_filename":      iso19650_fn,
        "mdr_id":                 mdr_id,
        "document_type":          row["aveva_doc_type"],
        "discipline":             discipline,
        "revision":               revision,
        "title":                  row["aveva_doc_type"],
        "description":            f"{row['aveva_doc_type']} for {discipline} scope, revision {revision}.",
        "file_format":            row.get("aveva_file_format", "PDF"),
        "approval_class":         approval_class,
        "certification_required": row.get("aveva_certification_required", False),
        "certifying_body":        row.get("aveva_certifying_body", ""),
        "certifying_body_contact": "",
        "confidentiality_class":  confidentiality,
        "sensitive_information":  sensitive,
        "author":                 author,
        "co_author":              "",
        "originator_company":     company,
        "reviewer":               row.get("aveva_checked_by", ""),
        "approver":               row.get("aveva_approved_by", ""),
        "issue_date":             issue_date,
        "ingested_at":            to_utc_iso(date.fromisoformat(issue_date) + timedelta(days=random.randint(1, 5))),
    }


# ---------------------------------------------------------------------------
# Cross-reference builder
# ---------------------------------------------------------------------------

def build_cross_reference(source_system: str, source_instance: str,
                           source_native_id: str, source_doc_number: str,
                           mdr_id: str, document_id: str) -> dict:
    """
    Build one row for staged_cross_reference.csv.

    The cross-reference table is the golden record bridge — it maps every
    source-native identifier to the canonical MDR_ID. It is never deleted.
    If a source system renumbers its documents, the cross-reference is updated;
    the canonical MDR_ID does not change.

    Args:
        source_system:     Windchill / SharePoint / Aveva
        source_instance:   e.g. Windchill_PartnerA
        source_native_id:  The ID as it appears in the source system
        source_doc_number: The human-readable doc number from the source
        mdr_id:            Canonical MDR_ID
        document_id:       Stable UUID

    Returns:
        Dict representing one cross-reference row.
    """
    return {
        "xref_id":           f"XREF-{source_system[:2].upper()}-{source_native_id}",
        "mdr_id":            mdr_id,
        "document_id":       document_id,
        "source_system":     source_system,
        "source_instance":   source_instance,
        "source_native_id":  source_native_id,
        "source_doc_number": source_doc_number,
        "created_at":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# Event generation (same logic as v1 staged layer, now runs on canonical records)
# ---------------------------------------------------------------------------

BASE_SEQUENCE = [
    "PLANNED", "IN_PROGRESS", "SUBMITTED", "UNDER_REVIEW", "APPROVED_DRAFT",
]

TERMINAL_SEQUENCES = {
    "INTERNAL":      ["APPROVED_FINAL"],
    "CUSTOMER":      ["APPROVED_CUSTOMER", "APPROVED_FINAL"],
    "AUTHORITY":     ["APPROVED_AUTHORITY", "APPROVED_FINAL"],
    "CERTIFICATION": ["APPROVED_CERTIFICATION", "APPROVED_FINAL"],
}

PLANNED_INTERVALS = {
    "INTERNAL":      {"base": 7,  "variance": 3},
    "CUSTOMER":      {"base": 14, "variance": 7},
    "AUTHORITY":     {"base": 28, "variance": 14},
    "CERTIFICATION": {"base": 21, "variance": 10},
}

REVISION_PROBABILITY = {
    "INTERNAL": 0.10, "CUSTOMER": 0.35, "AUTHORITY": 0.50, "CERTIFICATION": 0.40,
}


def actual_variance(approval_class: str) -> int:
    """Return days of variance (actual vs planned). Negative = early, positive = late."""
    profiles = {
        "INTERNAL":      (-2,  5,  0.05),
        "CUSTOMER":      (-3, 21,  0.08),
        "AUTHORITY":     ( 0, 60,  0.02),
        "CERTIFICATION": ( 0, 45,  0.03),
    }
    min_late, max_late, p_early = profiles[approval_class]
    if random.random() < p_early:
        return random.randint(-5, -1)
    return random.randint(min_late, max_late)


def build_planned_sequence(approval_class: str, include_revision_loop: bool) -> list:
    """Build the full planned status sequence for a document."""
    seq = BASE_SEQUENCE.copy()
    if include_revision_loop:
        loop = ["REVISION_REQUIRED", "IN_PROGRESS", "SUBMITTED", "UNDER_REVIEW"]
        idx  = seq.index("UNDER_REVIEW") + 1
        seq  = seq[:idx] + loop + seq[idx:]
    seq += TERMINAL_SEQUENCES.get(approval_class, ["APPROVED_FINAL"])
    return seq


def actuals_cutoff(sequence: list, current_status: str) -> int:
    """Return the index in sequence up to which we have actual timestamps."""
    if current_status in sequence:
        return sequence.index(current_status)
    if "IN_PROGRESS" in sequence:
        return sequence.index("IN_PROGRESS")
    return 0


def generate_document_events(doc: dict, project_start: date) -> list:
    """
    Generate the lifecycle event log for one canonically harmonised document.

    Planned dates are anchored to TODAY (the pipeline run date), NOT to the
    document's issue_date.  This makes STAGED timeline dates consistent with
    the MDR planned dates, which are now derived directly from these events.

    Algorithm:
      1. Build the full event sequence and cumulative day-gap offsets.
      2. Choose target_completion (planned final approval) relative to TODAY:
           - Terminal docs: randomly 7-365 days in the past (already done).
           - Active docs: probabilistic RED/AMBER/GREEN zone draw,
             constrained to days_to_completion <= remaining_duration so that
             the current-status planned date is <= TODAY (enabling realistic
             actual timestamps without impossible future-to-past capping).
      3. plan_anchor = target_completion - total_duration  (work backwards).
      4. planned_dates = plan_anchor + each cumulative gap.
      5. actual_dates  = planned_date + variance, capped at TODAY,
             for events up to actual_up_to; None beyond.

    Args:
        doc:           One canonical record from raw_documents.csv.
        project_start: Earliest possible event date.

    Returns:
        List of event dicts for staged_events.csv.
    """
    approval_class   = doc["approval_class"]
    canonical_status = doc["canonical_status"]
    document_id      = doc["document_id"]

    has_revision_loop = random.random() < REVISION_PROBABILITY.get(approval_class, 0.20)
    sequence = build_planned_sequence(approval_class, has_revision_loop)

    actual_up_to = (
        len(sequence) - 1 if canonical_status in TERMINAL_STATUSES
        else actuals_cutoff(sequence, canonical_status)
    )

    interval_cfg = PLANNED_INTERVALS.get(approval_class, {"base": 14, "variance": 7})

    # Build cumulative gap offsets (days from plan_anchor to each event index).
    # We compute them all upfront so we can derive plan_anchor from target_completion
    # rather than plan_anchor from issue_date (the old approach).
    cum_gaps = [0]
    for _ in range(1, len(sequence)):
        step = interval_cfg["base"] + random.randint(
            -interval_cfg["variance"], interval_cfg["variance"]
        )
        cum_gaps.append(cum_gaps[-1] + max(step, 3))
    total_duration = cum_gaps[-1]

    # remaining_duration = days from the current status event to planned completion.
    # This is the maximum valid positive float: if target_completion is set further
    # than remaining_duration days from TODAY, the current-status planned date
    # would be in the future, making the actual timestamp impossible.
    remaining_duration = total_duration - cum_gaps[actual_up_to]
    max_positive = max(remaining_duration, 1)

    if canonical_status in TERMINAL_STATUSES:
        # Document already approved — completion was some time in the past.
        days_to_completion = -random.randint(7, 365)
    else:
        # Active document — distribute across RED / AMBER / GREEN.
        # If not enough remaining steps to reach comfortable GREEN headroom,
        # fall back to AMBER (late-stage docs are naturally tighter).
        zone = random.choices(["RED", "AMBER", "GREEN"], weights=[0.25, 0.35, 0.40])[0]
        if zone == "GREEN" and max_positive < 22:
            zone = "AMBER"
        if zone == "RED":
            days_to_completion = random.randint(-90, -1)
        elif zone == "AMBER":
            days_to_completion = random.randint(0, max(1, min(21, max_positive)))
        else:
            days_to_completion = random.randint(22, max_positive)

    target_completion = TODAY + timedelta(days=days_to_completion)

    # Derive plan_anchor by working backwards from target_completion.
    # plan_anchor is the date the PLANNED phase begins (before any authoring).
    plan_anchor = target_completion - timedelta(days=total_duration)
    if plan_anchor < project_start:
        plan_anchor = project_start

    # planned_dates: each event = plan_anchor + its cumulative gap
    planned_dates = [plan_anchor + timedelta(days=g) for g in cum_gaps]

    # actual_dates: add variance to planned date for events that have happened
    # (up to actual_up_to); cap at TODAY so no actual is in the future.
    actual_dates = []
    for i in range(len(sequence)):
        if i <= actual_up_to:
            actual_date = planned_dates[i] + timedelta(days=actual_variance(approval_class))
            if actual_date > TODAY:
                actual_date = TODAY
            actual_dates.append(actual_date)
        else:
            actual_dates.append(None)

    events = []
    review_cycle = 1
    in_loop      = False
    first_ur_idx = sequence.index("UNDER_REVIEW") if "UNDER_REVIEW" in sequence else -1

    for i, to_status in enumerate(sequence):
        from_status = sequence[i - 1] if i > 0 else "REQUIREMENT_DRAFT"

        if to_status == "REVISION_REQUIRED":
            in_loop = True
        if in_loop and to_status == "UNDER_REVIEW" and i > first_ur_idx:
            review_cycle += 1
            in_loop = False

        planned_ts = to_utc_iso(planned_dates[i])
        actual_ts  = to_utc_iso(actual_dates[i]) if actual_dates[i] else ""

        variance_days = ""
        if actual_dates[i] and i > 0:
            variance_days = (actual_dates[i] - planned_dates[i]).days

        days_in_previous = ""
        if actual_dates[i] and i > 0 and actual_dates[i - 1]:
            days_in_previous = (actual_dates[i] - actual_dates[i - 1]).days

        comments = ""
        if to_status == "REVISION_REQUIRED":
            comments = random.choice([
                "Reviewer requested additional detail on calculations.",
                "Client comments received -- scope clarification needed.",
                "Authority raised query on safety justification.",
                "Certification body requested updated material certificates.",
                "Mark-up returned -- title block correction required.",
            ])
        elif to_status == "ON_HOLD":
            comments = random.choice([
                "Blocked pending vendor data.",
                "On hold -- awaiting client decision on scope change.",
                "Hold placed by project controls -- budget review in progress.",
            ])
        elif to_status in ("APPROVED_AUTHORITY", "APPROVED_CERTIFICATION"):
            comments = random.choice([
                "Formal approval letter received and filed.",
                "Certificate issued -- reference number logged.",
                "Regulatory acceptance confirmed in writing.",
            ])

        events.append({
            "event_id":                str(uuid.uuid4()),
            "document_id":             document_id,
            "from_status":             from_status,
            "to_status":               to_status,
            "planned_timestamp":       planned_ts,
            "actual_timestamp":        actual_ts,
            "variance_days":           variance_days,
            "days_in_previous_status": days_in_previous,
            "review_cycle":            review_cycle,
            "target_revision":         doc["revision"],
            "approval_class":          approval_class,
            "actioned_by_user_id":     str(uuid.uuid4())[:8],
            "actioned_by_name":        doc["author"] if to_status == "SUBMITTED" else "",
            "entered_by":              doc["reviewer"],
            "comments":                comments,
        })

    return events


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

RAW_FIELDNAMES = [
    "document_id", "source_system_id", "source_system", "source_system_instance",
    "source_status", "canonical_status", "iso19650_status_code",
    "project_code", "originator_code", "volume_code", "level_code",
    "type_code", "role_code", "iso19650_filename", "mdr_id",
    "document_type", "discipline", "revision", "title", "description", "file_format",
    "approval_class", "certification_required", "certifying_body", "certifying_body_contact",
    "confidentiality_class", "sensitive_information",
    "author", "co_author", "originator_company", "reviewer", "approver",
    "issue_date", "ingested_at",
]

EVENT_FIELDNAMES = [
    "event_id", "document_id", "from_status", "to_status",
    "planned_timestamp", "actual_timestamp", "variance_days",
    "days_in_previous_status", "review_cycle", "target_revision",
    "approval_class", "actioned_by_user_id", "actioned_by_name",
    "entered_by", "comments",
]

XREF_FIELDNAMES = [
    "xref_id", "mdr_id", "document_id",
    "source_system", "source_instance", "source_native_id",
    "source_doc_number", "created_at",
]

DQ_FIELDNAMES = [
    "flag_id", "mdr_id", "source_system", "source_native_id",
    "field_name", "flag_type", "flag_detail",
    "original_value", "suggested_value",
    "resolved", "resolved_by", "resolved_at",
]


def write_csv(records: list, path: Path, fieldnames: list, label: str) -> None:
    """Write records to CSV and log the result."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    _log("SAVE", f"Written {len(records)} {label} -> {path.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """
    Run the full STAGED layer harmonisation pipeline.

    Reads windchill_source.csv, sharepoint_source.csv, aveva_source.csv.
    Writes raw_documents.csv, staged_events.csv,
           staged_cross_reference.csv, staged_dq_flags.csv,
           staged_transformation_log.csv.
    """
    script_dir = Path(__file__).parent

    # -- Verify all source files exist before starting
    sources = {
        "Windchill":   script_dir / "windchill_source.csv",
        "SharePoint":  script_dir / "sharepoint_source.csv",
        "Aveva":       script_dir / "aveva_source.csv",
    }
    for name, path in sources.items():
        if not path.exists():
            _log("ERROR", f"{path.name} not found. Run generate_{name.lower()}_source.py first.")
            return

    # -- Load source CSVs
    def load(path):
        with open(path, encoding="utf-8") as f:
            return list(csv.DictReader(f))

    wc_rows = load(sources["Windchill"])
    sp_rows = load(sources["SharePoint"])
    av_rows = load(sources["Aveva"])

    _log("LOAD", f"Windchill source  : {len(wc_rows)} rows")
    _log("LOAD", f"SharePoint source : {len(sp_rows)} rows")
    _log("LOAD", f"Aveva source      : {len(av_rows)} rows")

    # -- Harmonise all three sources
    canonical_records = []
    xref_records      = []
    dq_flags          = []
    transform_log     = []   # one record per field silently transformed by the pipeline

    _log("LOAD", "Harmonising Windchill records...")
    for row in wc_rows:
        canon = harmonise_windchill(row, dq_flags, transform_log)
        canonical_records.append(canon)
        xref_records.append(build_cross_reference(
            source_system    ="Windchill",
            source_instance  = row["wc_instance"],
            source_native_id = row["wc_id"],
            source_doc_number= row["wc_doc_number"],
            mdr_id           = canon["mdr_id"],
            document_id      = canon["document_id"],
        ))

    _log("LOAD", "Harmonising SharePoint records...")
    for row in sp_rows:
        canon = harmonise_sharepoint(row, dq_flags, transform_log)
        canonical_records.append(canon)
        xref_records.append(build_cross_reference(
            source_system    ="SharePoint",
            source_instance  = row["sp_instance"],
            source_native_id = str(row["sp_item_id"]),
            source_doc_number= row["sp_file_name"],
            mdr_id           = canon["mdr_id"],
            document_id      = canon["document_id"],
        ))

    _log("LOAD", "Harmonising Aveva records...")
    for row in av_rows:
        canon = harmonise_aveva(row, dq_flags, transform_log)
        canonical_records.append(canon)
        xref_records.append(build_cross_reference(
            source_system    ="Aveva",
            source_instance  = row["aveva_instance"],
            source_native_id = row["aveva_id"],
            source_doc_number= row["aveva_doc_number"],
            mdr_id           = canon["mdr_id"],
            document_id      = canon["document_id"],
        ))

    _log("LOAD", (
        f"Harmonisation complete: {len(canonical_records)} canonical records, "
        f"{len(dq_flags)} DQ flags, {len(transform_log)} transformations logged"
    ))

    # -- Generate events from harmonised records
    _log("LOAD", "Generating lifecycle events...")
    project_start = date(2025, 1, 1)
    all_events    = []
    for doc in canonical_records:
        all_events.extend(generate_document_events(doc, project_start))

    _log("LOAD", f"Event generation complete: {len(all_events)} events")

    # Stamp every transformation record with the pipeline run time before writing.
    # All records get the same timestamp so you can filter to "this run" in the dashboard.
    _run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    for _t in transform_log:
        _t["run_timestamp"] = _run_ts

    # -- Write all outputs
    write_csv(canonical_records, script_dir / "raw_documents.csv",                RAW_FIELDNAMES,       "harmonised records")
    write_csv(all_events,        script_dir / "staged_events.csv",                EVENT_FIELDNAMES,     "events")
    write_csv(xref_records,      script_dir / "staged_cross_reference.csv",       XREF_FIELDNAMES,      "cross-reference rows")
    write_csv(dq_flags,          script_dir / "staged_dq_flags.csv",              DQ_FIELDNAMES,        "DQ flags")
    write_csv(transform_log,     script_dir / "staged_transformation_log.csv",    TRANSFORM_FIELDNAMES, "transformation records")

    print_summary(canonical_records, all_events, dq_flags, transform_log)


def print_summary(records: list, events: list, dq_flags: list, transform_log: list) -> None:
    """Print a plain-text summary of all pipeline outputs."""
    from collections import Counter

    print("\n--- STAGED Layer Summary ----------------------------------------")
    print(f"Harmonised records : {len(records)}")
    print(f"Events generated   : {len(events)}")
    print(f"DQ flags raised    : {len(dq_flags)}")
    print(f"Transformations    : {len(transform_log)}")

    print("\nRecords by source system:")
    src_counts = Counter(r["source_system"] for r in records)
    for k, v in sorted(src_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<20} {v:>3}")

    print("\nRecords by discipline:")
    disc_counts = Counter(r["discipline"] for r in records)
    for k, v in sorted(disc_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<20} {v:>3}")

    print("\nDQ flags by type:")
    flag_counts = Counter(f["flag_type"] for f in dq_flags)
    for k, v in sorted(flag_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<35} {v:>3}")

    print("\nDQ flags by source system:")
    src_flag_counts = Counter(f["source_system"] for f in dq_flags)
    for k, v in sorted(src_flag_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<20} {v:>3}")

    actual_count = sum(1 for e in events if e["actual_timestamp"])
    print(f"\nEvents breakdown:")
    print(f"  Completed (actual timestamp) : {actual_count}")
    print(f"  Planned (future)             : {len(events) - actual_count}")

    print("\nTransformations by rule:")
    rule_counts = Counter(t["normalisation_rule"] for t in transform_log)
    for k, v in sorted(rule_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<35} {v:>3}")

    print("\nTransformations by source system:")
    src_tx_counts = Counter(t["source_system"] for t in transform_log)
    for k, v in sorted(src_tx_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<20} {v:>3}")

    print("-" * 53)


if __name__ == "__main__":
    main()
