"""
generate_staged_layer.py
multisource-cde-mdr-pipeline | Day 2 — STAGED layer (v2 — final)

Reads raw_documents.csv and generates a CDE event log —
one row per status transition per document, planned and actual.

Key design:
  - Full planned sequence generated for every document from PLANNED → terminal
  - Actual timestamps populated up to current canonical_status
  - Future transitions remain planned-only (actual_timestamp = "")
  - approval_class drives terminal status, interval lengths, revision loop probability
  - REVISION_REQUIRED loops increment review_cycle
  - Variance is realistic: most transitions late, some significantly
  - All timestamps ISO 8601 UTC (trailing Z)

Run from anywhere — paths are relative to this script's location.
Reads:  <script_dir>/raw_documents.csv
Output: <script_dir>/staged_events.csv
"""

import csv
import random
import uuid
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

random.seed(99)

TODAY = date(2026, 5, 8)

# ── Transition path templates ──────────────────────────────────────────────────
BASE_SEQUENCE = [
    "PLANNED",
    "IN_PROGRESS",
    "SUBMITTED",
    "UNDER_REVIEW",
    "APPROVED_DRAFT",
]

TERMINAL_SEQUENCES = {
    "INTERNAL":      ["APPROVED_FINAL"],
    "CUSTOMER":      ["APPROVED_CUSTOMER", "APPROVED_FINAL"],
    "AUTHORITY":     ["APPROVED_AUTHORITY", "APPROVED_FINAL"],
    "CERTIFICATION": ["APPROVED_CERTIFICATION", "APPROVED_FINAL"],
}

# Planned interval (days) between transitions by approval class
PLANNED_INTERVALS = {
    "INTERNAL":      {"base": 7,  "variance": 3},
    "CUSTOMER":      {"base": 14, "variance": 7},
    "AUTHORITY":     {"base": 28, "variance": 14},
    "CERTIFICATION": {"base": 21, "variance": 10},
}

# Probability of a REVISION_REQUIRED loop by approval class
REVISION_PROBABILITY = {
    "INTERNAL":      0.10,
    "CUSTOMER":      0.35,
    "AUTHORITY":     0.50,
    "CERTIFICATION": 0.40,
}

def actual_variance(approval_class: str) -> int:
    """Days of variance: actual vs planned. Negative = early, positive = late."""
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

# ── Timestamp helpers (ISO 8601 UTC) ───────────────────────────────────────────
def date_to_utc_iso(d: date) -> str:
    dt = datetime(
        d.year, d.month, d.day,
        random.randint(7, 17), random.randint(0, 59), random.randint(0, 59),
        tzinfo=timezone.utc
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)

# ── Build full planned sequence ────────────────────────────────────────────────
def build_planned_sequence(approval_class: str, include_revision_loop: bool) -> list:
    seq = BASE_SEQUENCE.copy()
    if include_revision_loop:
        loop = ["REVISION_REQUIRED", "IN_PROGRESS", "SUBMITTED", "UNDER_REVIEW"]
        idx = seq.index("UNDER_REVIEW") + 1
        seq = seq[:idx] + loop + seq[idx:]
    seq += TERMINAL_SEQUENCES[approval_class]
    return seq

# ── Determine actual cutoff index ─────────────────────────────────────────────
def actuals_cutoff(sequence: list, current_status: str) -> int:
    if current_status in sequence:
        return sequence.index(current_status)
    if "IN_PROGRESS" in sequence:
        return sequence.index("IN_PROGRESS")
    return 0

# ── Generate events for one document ──────────────────────────────────────────
def generate_document_events(doc: dict, project_start: date) -> list:
    approval_class   = doc["approval_class"]
    canonical_status = doc["canonical_status"]
    document_id      = doc["document_id"]
    issue_date       = date.fromisoformat(doc["issue_date"])

    plan_anchor = add_days(issue_date, -random.randint(30, 90))
    if plan_anchor < project_start:
        plan_anchor = project_start

    has_revision_loop = random.random() < REVISION_PROBABILITY[approval_class]
    sequence = build_planned_sequence(approval_class, has_revision_loop)

    terminal_set = {
        "APPROVED_FINAL", "APPROVED_CUSTOMER", "APPROVED_AUTHORITY",
        "APPROVED_CERTIFICATION", "SUPERSEDED"
    }
    actual_up_to = (
        len(sequence) - 1 if canonical_status in terminal_set
        else actuals_cutoff(sequence, canonical_status)
    )

    # Build planned timestamps
    interval_cfg = PLANNED_INTERVALS[approval_class]
    planned_dates = [plan_anchor]
    for i in range(1, len(sequence)):
        gap = interval_cfg["base"] + random.randint(
            -interval_cfg["variance"], interval_cfg["variance"]
        )
        gap = max(gap, 3)
        planned_dates.append(add_days(planned_dates[-1], gap))

    # Build actual timestamps
    actual_dates = []
    for i in range(len(sequence)):
        if i <= actual_up_to:
            actual_date = add_days(planned_dates[i], actual_variance(approval_class))
            if actual_date > TODAY:
                actual_date = TODAY
            actual_dates.append(actual_date)
        else:
            actual_dates.append(None)

    # Build event rows
    events = []
    review_cycle = 1
    in_loop      = False
    first_under_review_idx = sequence.index("UNDER_REVIEW") if "UNDER_REVIEW" in sequence else -1

    for i in range(len(sequence)):
        from_status = sequence[i - 1] if i > 0 else "REQUIREMENT_DRAFT"
        to_status   = sequence[i]

        if to_status == "REVISION_REQUIRED":
            in_loop = True
        if in_loop and to_status == "UNDER_REVIEW" and i > first_under_review_idx:
            review_cycle += 1
            in_loop = False

        planned_ts = date_to_utc_iso(planned_dates[i])
        actual_ts  = date_to_utc_iso(actual_dates[i]) if actual_dates[i] else ""

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
                "Client comments received — scope clarification needed.",
                "Authority raised query on safety justification.",
                "Certification body requested updated material certificates.",
                "Mark-up returned — title block correction required.",
            ])
        elif to_status == "ON_HOLD":
            comments = random.choice([
                "Blocked pending vendor data.",
                "On hold — awaiting client decision on scope change.",
                "Hold placed by project controls — budget review in progress.",
            ])
        elif to_status in ("APPROVED_AUTHORITY", "APPROVED_CERTIFICATION"):
            comments = random.choice([
                "Formal approval letter received and filed.",
                "Certificate issued — reference number logged.",
                "Regulatory acceptance confirmed in writing.",
            ])

        events.append({
            "event_id":                 str(uuid.uuid4()),
            "document_id":              document_id,
            "from_status":              from_status,
            "to_status":                to_status,
            "planned_timestamp":        planned_ts,
            "actual_timestamp":         actual_ts,
            "variance_days":            variance_days,
            "days_in_previous_status":  days_in_previous,
            "review_cycle":             review_cycle,
            "target_revision":          doc["revision"],
            "approval_class":           approval_class,
            "actioned_by_user_id":      str(uuid.uuid4())[:8],
            "actioned_by_name":         doc["author"] if to_status == "SUBMITTED" else "",
            "entered_by":               doc["reviewer"],
            "comments":                 comments,
        })

    return events


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    script_dir  = Path(__file__).parent
    raw_path    = script_dir / "raw_documents.csv"
    output_path = script_dir / "staged_events.csv"

    if not raw_path.exists():
        print(f"✗ Cannot find {raw_path}. Run generate_raw_layer.py first.")
        return

    with open(raw_path, encoding="utf-8") as f:
        raw_docs = list(csv.DictReader(f))

    print(f"  Loaded {len(raw_docs)} documents from RAW layer.")

    project_start = date(2025, 1, 1)
    all_events = []
    for doc in raw_docs:
        all_events.extend(generate_document_events(doc, project_start))

    fieldnames = [
        "event_id", "document_id", "from_status", "to_status",
        "planned_timestamp", "actual_timestamp", "variance_days",
        "days_in_previous_status", "review_cycle", "target_revision",
        "approval_class", "actioned_by_user_id", "actioned_by_name",
        "entered_by", "comments",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_events)

    print(f"✓ Written {len(all_events)} events → {output_path}")
    print_summary(all_events)


def print_summary(events: list):
    from collections import Counter
    print("\n── STAGED Layer Summary ──────────────────────────────")
    print(f"Total events : {len(events)}")
    actual_count = sum(1 for e in events if e["actual_timestamp"])
    print(f"  Actual (completed) : {actual_count}")
    print(f"  Planned (future)   : {len(events) - actual_count}")

    to_counts = Counter(e["to_status"] for e in events)
    print("\nTransition targets:")
    for k, v in sorted(to_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<30} {v:>4}")

    variances = [int(e["variance_days"]) for e in events if e["variance_days"] != ""]
    if variances:
        avg = sum(variances) / len(variances)
        late = sum(1 for v in variances if v > 0)
        print(f"\nVariance (actual − planned):")
        print(f"  Mean : {avg:+.1f}d  Min : {min(variances):+d}d  Max : {max(variances):+d}d")
        print(f"  Late : {late}/{len(variances)} transitions ({100*late//len(variances)}%)")

    print(f"\nRevision loops : {sum(1 for e in events if e['to_status'] == 'REVISION_REQUIRED')}")
    print("─────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
