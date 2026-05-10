"""
generate_mdr_layer.py
multisource-cde-mdr-pipeline | Day 2 — ANALYTICAL / MDR layer (v3 — final)

Changes in v3:
  - Priority renamed: "Critical" → "Very High" (avoids confusion with critical path)
  - is_critical field removed (redundant — float + priority covers this)
  - RAG driven by float × priority combined (not float alone)
  - is_on_critical_path reduced to realistic ~20% of active documents
  - Date trending: baseline_approval_date, previous_approval_date, total_slip_days,
    recent_slip_days, date_trend (SLIPPING / STALLED / RECOVERING / STABLE)
  - Progress tracking: derived_percent_complete (from STAGED), reported_percent_complete (manual)
  - responsible_person = discipline lead (separate pool from RAW authors)
  - schedule_float_days renamed from float_days (README notes simplified calculation)
  - All timestamps ISO 8601 UTC (trailing Z)

Run from anywhere — paths are relative to this script's location.
Reads:  <script_dir>/raw_documents.csv
        <script_dir>/staged_events.csv
Output: <script_dir>/mdr_requirements.csv
"""

import csv
import random
import uuid
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from collections import defaultdict
from faker import Faker

fake = Faker()
random.seed(7)
Faker.seed(7)

TODAY = date(2026, 5, 8)
LAST_MONTH = date(2026, 4, 8)   # previous snapshot date

# ── Priority (Very High replaces Critical to avoid confusion with critical path) ─
PRIORITY_WEIGHTS = {
    "AUTHORITY":     {"Very High": 0.50, "High": 0.35, "Medium": 0.12, "Low": 0.03},
    "CERTIFICATION": {"Very High": 0.35, "High": 0.40, "Medium": 0.20, "Low": 0.05},
    "CUSTOMER":      {"Very High": 0.20, "High": 0.35, "Medium": 0.35, "Low": 0.10},
    "INTERNAL":      {"Very High": 0.05, "High": 0.20, "Medium": 0.45, "Low": 0.30},
}

PRIORITY_ORDER = ["Very High", "High", "Medium", "Low"]

def assign_priority(approval_class: str) -> str:
    weights = PRIORITY_WEIGHTS[approval_class]
    return random.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]

# ── RAG status — driven by float × priority ────────────────────────────────────
RAG_THRESHOLDS = {
    #                AMBER  RED
    "Very High": (21,       7),
    "High":      (14,       3),
    "Medium":    ( 7,       0),
    "Low":       ( 7,    -180),   # Low: never RED unless very significantly overdue
}

def derive_rag(float_days: int, priority: str, canonical_status: str) -> str:
    if canonical_status == "OVERDUE":
        return "RED"
    amber_thresh, red_thresh = RAG_THRESHOLDS[priority]
    if float_days <= red_thresh:
        return "RED"
    if float_days <= amber_thresh:
        return "AMBER"
    return "GREEN"

# ── Date generation ────────────────────────────────────────────────────────────
def generate_planned_dates(priority: str, approval_class: str) -> tuple:
    """Returns (planned_submission_date, planned_approval_date)."""
    review_durations = {
        "INTERNAL":      random.randint(7,  21),
        "CUSTOMER":      random.randint(14, 42),
        "AUTHORITY":     random.randint(28, 90),
        "CERTIFICATION": random.randint(21, 60),
    }
    review_duration = review_durations[approval_class]

    float_target = random.choices(
        ["RED", "AMBER", "GREEN"], weights=[0.25, 0.35, 0.40], k=1
    )[0]

    amber_thresh, red_thresh = RAG_THRESHOLDS[priority]

    if float_target == "RED":
        lo = max(red_thresh - 60, -180)
        hi = max(lo, red_thresh)
        float_days = random.randint(lo, hi)
    elif float_target == "AMBER":
        lo = red_thresh + 1
        hi = max(lo, amber_thresh)
        float_days = random.randint(lo, hi)
    else:
        float_days = random.randint(amber_thresh + 1, amber_thresh + 60)

    planned_approval_date   = TODAY + timedelta(days=float_days)
    planned_submission_date = planned_approval_date - timedelta(days=review_duration)
    return planned_submission_date, planned_approval_date

# ── Date trending ──────────────────────────────────────────────────────────────
def generate_date_trend(planned_approval_date: date) -> tuple:
    """
    Simulates baseline and previous month snapshots.
    Returns (baseline_approval_date, previous_approval_date,
             total_slip_days, recent_slip_days, date_trend)

    baseline = original plan at project kick-off (Jan 2025) — never changes
    previous = last month's planned date (April 2026 snapshot)
    """
    # Total slip since baseline: 0–90 days (some docs haven't slipped at all)
    total_slip = random.choices(
        [0, random.randint(1, 14), random.randint(15, 45), random.randint(46, 90)],
        weights=[0.20, 0.30, 0.35, 0.15],
        k=1
    )[0]
    baseline_approval_date = planned_approval_date - timedelta(days=total_slip)

    # Recent movement (last month): mostly small slips, some recovering, some stalled
    recent_movement = random.choices(
        [
            random.randint(-7, -1),    # recovering
            random.randint(0, 3),      # stable / stalled
            random.randint(4, 14),     # slipping
            random.randint(15, 30),    # significantly slipping
        ],
        weights=[0.10, 0.35, 0.35, 0.20],
        k=1
    )[0]
    previous_approval_date = planned_approval_date - timedelta(days=recent_movement)

    total_slip_days  = (planned_approval_date - baseline_approval_date).days
    recent_slip_days = (planned_approval_date - previous_approval_date).days

    # Date trend classification
    if recent_slip_days > 5:
        date_trend = "SLIPPING"
    elif recent_slip_days < -3:
        date_trend = "RECOVERING"
    elif total_slip_days > 14 and -3 <= recent_slip_days <= 5:
        date_trend = "STALLED"
    else:
        date_trend = "STABLE"

    return (
        baseline_approval_date,
        previous_approval_date,
        total_slip_days,
        recent_slip_days,
        date_trend,
    )

# ── Progress — derived from STAGED ────────────────────────────────────────────
def derive_percent_complete(doc_id: str, staged_by_doc: dict) -> int:
    """
    Fraction of planned transitions that have an actual timestamp.
    Returns 0–100 as integer.
    """
    events = staged_by_doc.get(doc_id, [])
    if not events:
        return 0
    total   = len(events)
    actuals = sum(1 for e in events if e["actual_timestamp"])
    return round(100 * actuals / total)

def simulate_reported_percent(derived: int, canonical_status: str) -> int:
    """
    Manually reported progress — close to derived but with human rounding/lag.
    People tend to report round numbers and slightly lag reality.
    """
    if canonical_status in ("APPROVED_FINAL", "SUPERSEDED"):
        return 100
    if canonical_status == "PLANNED":
        return 0
    # Round to nearest 5, with slight downward bias (people are conservative)
    lag = random.randint(-10, 5)
    reported = max(0, min(100, derived + lag))
    return round(reported / 5) * 5   # round to nearest 5%

# ── Next action — derived from STAGED ─────────────────────────────────────────
def derive_next_action(doc_id: str, staged_by_doc: dict) -> str:
    events  = staged_by_doc.get(doc_id, [])
    pending = [e for e in events if not e["actual_timestamp"]]
    if not pending:
        return "No further actions — document complete"
    to_status = pending[0]["to_status"]
    action_labels = {
        "IN_PROGRESS":            "Author to progress document",
        "SUBMITTED":              "Submit to CDE for review",
        "UNDER_REVIEW":           "Assign reviewer and open review cycle",
        "REVISION_REQUIRED":      "Return to originator with comments",
        "APPROVED_DRAFT":         "Issue draft approval",
        "APPROVED_CUSTOMER":      "Obtain client sign-off",
        "APPROVED_AUTHORITY":     "Submit to regulatory authority",
        "APPROVED_CERTIFICATION": "Submit to certifying body",
        "APPROVED_FINAL":         "Issue final approval and close out",
        "PLANNED":                "Plan document production",
        "ON_HOLD":                "Resolve hold — check comments",
    }
    return action_labels.get(to_status, f"Transition to {to_status}")

def derive_last_status_change(doc_id: str, staged_by_doc: dict) -> str:
    events  = staged_by_doc.get(doc_id, [])
    actuals = [e for e in events if e["actual_timestamp"]]
    return actuals[-1]["actual_timestamp"] if actuals else ""

# ── Discipline leads (small pool — separate from document authors) ─────────────
DISCIPLINE_LEADS = {
    "Mechanical":      [fake.name() for _ in range(2)],
    "Electrical":      [fake.name() for _ in range(2)],
    "Instrumentation": [fake.name() for _ in range(2)],
    "Civil":           [fake.name() for _ in range(2)],
}

RESPONSIBLE_COMPANIES = [
    "Gamma EPC Ltd",
    "Alpha Engineering GmbH",
    "Beta Konstruktion AS",
    "Delta Technics BV",
]

def assign_responsible(discipline: str, approval_class: str) -> tuple:
    lead = random.choice(DISCIPLINE_LEADS[discipline])
    company = (
        random.choice(["Gamma EPC Ltd", "Alpha Engineering GmbH"])
        if approval_class in ("AUTHORITY", "CERTIFICATION")
        else random.choice(RESPONSIBLE_COMPANIES)
    )
    return lead, company

# ── Mock users (for dashboard pre-population) ──────────────────────────────────
# Named users: discipline leads + project director
# Generated here so the same names appear in bookmarks/saved views
MOCK_USERS = [
    "sarah.chen",
    "james.okafor",
    "maria.lindqvist",
    "hassan.al-rashid",
    "project.director",
]

# ── MDR ID counter ─────────────────────────────────────────────────────────────
DISCIPLINE_CODES = {
    "Mechanical":      "MEC",
    "Electrical":      "ELE",
    "Instrumentation": "INS",
    "Civil":           "CIV",
}
mdr_counters = {d: 0 for d in DISCIPLINE_CODES}

def next_mdr_id(discipline: str) -> str:
    mdr_counters[discipline] += 1
    return f"MDR-{DISCIPLINE_CODES[discipline]}-{mdr_counters[discipline]:03d}"

# ── Timestamp helper ───────────────────────────────────────────────────────────
def date_to_utc_iso(d: date) -> str:
    dt = datetime(
        d.year, d.month, d.day,
        random.randint(7, 17), random.randint(0, 59), random.randint(0, 59),
        tzinfo=timezone.utc
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Notes ──────────────────────────────────────────────────────────────────────
def generate_notes(rag: str, approval_class: str, canonical_status: str) -> str:
    if canonical_status == "ON_HOLD":
        return random.choice([
            "Hold placed pending vendor data submission.",
            "Awaiting client decision on scope change order.",
            "Hold — budget review in progress, release expected next sprint.",
            "",
        ])
    if rag == "RED" and approval_class == "AUTHORITY":
        return random.choice([
            "Authority submission overdue — escalated to project director.",
            "Regulatory timeline at risk — expedite review.",
            "",
        ])
    if rag == "RED":
        return random.choice([
            "Approval overdue — chasing responsible party.",
            "Schedule slippage identified — recovery plan requested.",
            "",
        ])
    if approval_class == "CERTIFICATION":
        return random.choice([
            "Physical inspection required prior to certification sign-off.",
            "",
        ])
    return ""

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    script_dir  = Path(__file__).parent
    raw_path    = script_dir / "raw_documents.csv"
    staged_path = script_dir / "staged_events.csv"
    output_path = script_dir / "mdr_requirements.csv"

    for p in [raw_path, staged_path]:
        if not p.exists():
            print(f"✗ Cannot find {p}. Run previous generation scripts first.")
            return

    with open(raw_path, encoding="utf-8") as f:
        raw_docs = list(csv.DictReader(f))
    with open(staged_path, encoding="utf-8") as f:
        staged_events = list(csv.DictReader(f))

    print(f"  Loaded {len(raw_docs)} RAW documents.")
    print(f"  Loaded {len(staged_events)} STAGED events.")

    staged_by_doc = defaultdict(list)
    for e in staged_events:
        staged_by_doc[e["document_id"]].append(e)

    mdr_records = []

    for doc in raw_docs:
        discipline       = doc["discipline"]
        approval_class   = doc["approval_class"]
        canonical_status = doc["canonical_status"]
        document_id      = doc["document_id"]

        # Use the canonical ISO 19650 ID already assigned by the STAGED layer
        # (e.g. PROJ1-ALPHAENG-ZZ-SH-IN-000001). Do NOT generate a new one here —
        # the ID must be stable and consistent across all pipeline outputs.
        mdr_id   = doc["mdr_id"]
        priority = assign_priority(approval_class)

        planned_submission_date, planned_approval_date = generate_planned_dates(
            priority, approval_class
        )

        schedule_float_days = (planned_approval_date - TODAY).days
        rag_status = derive_rag(schedule_float_days, priority, canonical_status)

        responsible_person, responsible_company = assign_responsible(discipline, approval_class)

        # Critical path: realistic ~20% of total register
        # AUTHORITY/CERTIFICATION and Very High/High priority skew higher
        cp_base = {
            "AUTHORITY":     0.45,
            "CERTIFICATION": 0.35,
            "CUSTOMER":      0.18,
            "INTERNAL":      0.08,
        }[approval_class]
        priority_boost = {"Very High": 0.10, "High": 0.05, "Medium": 0.0, "Low": -0.05}[priority]
        is_on_critical_path = random.random() < min(cp_base + priority_boost, 0.90)

        # Date trending
        (baseline_approval_date, previous_approval_date,
         total_slip_days, recent_slip_days, date_trend) = generate_date_trend(planned_approval_date)

        # Progress
        derived_pct  = derive_percent_complete(document_id, staged_by_doc)
        reported_pct = simulate_reported_percent(derived_pct, canonical_status)

        next_action        = derive_next_action(document_id, staged_by_doc)
        last_status_change = derive_last_status_change(document_id, staged_by_doc)
        notes              = generate_notes(rag_status, approval_class, canonical_status)

        mdr_records.append({
            # ── Identity
            "mdr_id":                      mdr_id,
            "fulfilled_by_document_id":    document_id,
            # ── Document metadata (denormalised from RAW)
            "document_title":              doc["title"],
            "description":                 doc["description"],
            "document_type":               doc["document_type"],
            "discipline":                  discipline,
            "current_revision":            doc["revision"],
            "file_format":                 doc["file_format"],
            # ── ISO 19650
            "iso19650_status_code":        doc["iso19650_status_code"],
            "iso19650_filename":           doc["iso19650_filename"],
            # ── Approval & certification (denormalised from RAW)
            "approval_class":              approval_class,
            "certification_required":      doc["certification_required"],
            "certifying_body":             doc["certifying_body"],
            "certifying_body_contact":     doc["certifying_body_contact"],
            # ── Confidentiality (denormalised from RAW)
            "confidentiality_class":       doc["confidentiality_class"],
            "sensitive_information":       doc["sensitive_information"],
            # ── Responsibility (discipline lead)
            "responsible_person":          responsible_person,
            "responsible_company":         responsible_company,
            # ── Priority & schedule
            "priority":                    priority,
            "is_on_critical_path":         is_on_critical_path,
            "planned_submission_date":     planned_submission_date.isoformat(),
            "planned_approval_date":       planned_approval_date.isoformat(),
            # ── Controlling fields
            "current_canonical_status":    canonical_status,
            "schedule_float_days":         schedule_float_days,
            "rag_status":                  rag_status,
            # ── Date trending (scheduler's baseline comparison)
            "baseline_approval_date":      baseline_approval_date.isoformat(),
            "previous_approval_date":      previous_approval_date.isoformat(),
            "total_slip_days":             total_slip_days,
            "recent_slip_days":            recent_slip_days,
            "date_trend":                  date_trend,
            # ── Progress
            "derived_percent_complete":    derived_pct,
            "reported_percent_complete":   reported_pct,
            # ── Derived / linked
            "source_system":               doc["source_system"],
            "source_system_instance":      doc["source_system_instance"],
            "source_system_link":          f"https://cde.example.com/docs/{document_id}",
            "last_status_change":          last_status_change,
            "next_action":                 next_action,
            "notes":                       notes,
        })

    fieldnames = list(mdr_records[0].keys())
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(mdr_records)

    print(f"OK: Written {len(mdr_records)} MDR rows -> {output_path}")
    print_summary(mdr_records)


def print_summary(records: list):
    from collections import Counter
    print("\n--- MDR (ANALYTICAL) Layer Summary ------------------------------")
    print(f"Total requirements : {len(records)}")

    for label, key in [
        ("RAG status",     "rag_status"),
        ("Priority",       "priority"),
        ("Approval class", "approval_class"),
        ("Date trend",     "date_trend"),
    ]:
        counts = Counter(r[key] for r in records)
        print(f"\n{label}:")
        for k, v in sorted(counts.items(), key=lambda x: -x[1]):
            bar = "#" * v
            print(f"  {k:<20} {v:>3}  {bar}")

    cp_count = sum(1 for r in records if str(r["is_on_critical_path"]) == "True")
    print(f"\nOn critical path : {cp_count} / {len(records)}")

    floats = sorted(int(r["schedule_float_days"]) for r in records)
    print(f"\nSchedule float days:")
    print(f"  Overdue (< 0) : {sum(1 for f in floats if f < 0)}")
    print(f"  Min / Median / Max : {min(floats)} / {floats[len(floats)//2]} / {max(floats)}")

    print("\nRAG x Priority cross-tab:")
    from collections import defaultdict
    cross = defaultdict(int)
    for r in records:
        cross[(r["priority"], r["rag_status"])] += 1
    print(f"  {'Priority':<12} {'RED':>5} {'AMBER':>6} {'GREEN':>6} {'TOTAL':>7}")
    grand = {"RED": 0, "AMBER": 0, "GREEN": 0}
    for p in PRIORITY_ORDER:
        red, amber, green = cross[(p,"RED")], cross[(p,"AMBER")], cross[(p,"GREEN")]
        grand["RED"] += red; grand["AMBER"] += amber; grand["GREEN"] += green
        print(f"  {p:<12} {red:>5} {amber:>6} {green:>6} {red+amber+green:>7}")
    print(f"  {'TOTAL':<12} {grand['RED']:>5} {grand['AMBER']:>6} {grand['GREEN']:>6} {sum(grand.values()):>7}")
    print("-" * 53)


PRIORITY_ORDER = ["Very High", "High", "Medium", "Low"]

if __name__ == "__main__":
    main()
