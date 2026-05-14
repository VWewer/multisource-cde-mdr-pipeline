"""
app.py
multisource-cde-mdr-pipeline | Day 3 — Streamlit dashboard

Entry point. Run from project root:
    streamlit run dashboard/app.py

Or from inside dashboard/:
    streamlit run app.py

Reads from:
  - data_generation/mdr_requirements.csv   ← source of truth (editable)
  - data_generation/edit_log.csv           ← audit trail (append-only)
  - data_generation/bookmarks.csv          ← per-user watchlist
  - data_generation/saved_views.json       ← named saved views

Snowflake connection used for RAW and STAGED data (read-only).
MDR edits are written back to the local CSV, not Snowflake directly.
"""

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# ── Path resolution ────────────────────────────────────────────────────────────
DASHBOARD_DIR  = Path(__file__).parent
PROJECT_ROOT   = DASHBOARD_DIR.parent
DATA_DIR       = PROJECT_ROOT / "data_generation"

MDR_CSV        = DATA_DIR / "mdr_requirements.csv"
EDIT_LOG_CSV   = DASHBOARD_DIR / "edit_log.csv"
BOOKMARKS_CSV  = DASHBOARD_DIR / "bookmarks.csv"
SAVED_VIEWS_JSON = DASHBOARD_DIR / "saved_views.json"
DQ_FLAGS_CSV              = DATA_DIR / "staged_dq_flags.csv"
STAGED_EVENTS_CSV         = DATA_DIR / "staged_events.csv"
TRANSFORMATION_LOG_CSV    = DATA_DIR / "staged_transformation_log.csv"

load_dotenv(PROJECT_ROOT / ".env")

TODAY = date(2026, 5, 8)  # fixed for demo dataset

# ── Debug / logging ────────────────────────────────────────────────────────────
# Set DEBUG=true in your .env file to unlock extra verbose terminal output.
# Key lifecycle events (load, save, edit, filter, etc.) always print regardless
# of this flag — DEBUG just adds extra detail like column lists and row samples.
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"


def _log(label: str, msg: str):
    """Print a timestamped lifecycle event to the terminal.

    This is your window into what the app is doing step by step.
    The terminal where you ran 'streamlit run' is where these appear.

    Args:
        label: A short category tag. Conventions (from CLAUDE.md):
               [LOAD]      reading data from disk
               [SAVE]      writing data to disk
               [EDIT]      a user changed a field in the dashboard
               [SNOWFLAKE] a query was sent to Snowflake
               [FILTER]    a filter was applied to the MDR data
               [BOOKMARK]  a watchlist entry was added or removed
               [VIEW]      a saved view was loaded or written
               [ERROR]     something went wrong
        msg:   A plain-English description of what happened.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{label}] {msg}")


def fmt_date(val) -> str:
    """Format a date value as YYYY-MM-DD, returning '—' for nulls or empties.

    Handles pandas Timestamp objects (produced by parse_dates), plain date/datetime
    objects, and raw strings. Strips the time component that pandas adds when a CSV
    column is parsed as datetime but contains date-only values (e.g. 2026-04-12 00:00:00).

    Args:
        val: A pandas Timestamp, datetime, date, string, or NaN/None.
    Returns:
        str: 'YYYY-MM-DD' or '—' if the value is null/empty.
    """
    # Handle None explicitly before pd.isna (which throws on some types)
    if val is None:
        return "—"
    try:
        if pd.isna(val):
            return "—"
    except (TypeError, ValueError):
        pass
    # Timestamp / datetime objects have strftime
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    # Fallback: treat as string and take the first 10 chars (the date portion)
    s = str(val).strip()
    return s[:10] if len(s) >= 10 else (s if s else "—")


# ── Streamlit page config ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="MDR Control | PROJ1",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Base palette ── */
:root {
    --bg-main:      #0f1117;
    --bg-panel:     #181c27;
    --bg-card:      #1e2333;
    --border:       #2a3040;
    --text-primary: #e8ecf4;
    --text-muted:   #7a8499;
    --text-dim:     #4a5568;
    --accent:       #3b82f6;
    --green:        #22c55e;
    --amber:        #f59e0b;
    --red:          #ef4444;
    --tag-bg:       #252d40;
}

/* ── Typography — IBM Plex Mono + IBM Plex Sans ── */
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
    color: var(--text-primary);
}

/* ── Hide default Streamlit chrome ── */
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding: 1.5rem 2rem 2rem 2rem !important; }

/* ── Remove sidebar collapse button (CSS attempt — JS below is the reliable path) ── */
/* data-testid may vary by Streamlit version; the JS MutationObserver in main()
   is the primary mechanism — this is belt-and-suspenders only. */
button[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapseButton"] {
    display: none !important;
    visibility: hidden !important;
    pointer-events: none !important;
}

/* ── App header bar ── */
.app-header {
    display: flex;
    align-items: baseline;
    gap: 1rem;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.75rem;
    margin-bottom: 1.5rem;
}
.app-header h1 {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.05rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--text-primary);
    margin: 0;
}
.app-header .sub {
    font-size: 0.78rem;
    color: var(--text-muted);
    letter-spacing: 0.05em;
}

/* ── Page title ── */
.page-title {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem;
    font-weight: 500;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 1.25rem;
}

/* ── RAG summary tiles ── */
.rag-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.75rem;
    margin-bottom: 1.5rem;
}
.rag-tile {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-top-width: 3px;
    border-radius: 4px;
    padding: 1rem 1.25rem;
}
.rag-tile.green  { border-top-color: var(--green); }
.rag-tile.amber  { border-top-color: var(--amber); }
.rag-tile.red    { border-top-color: var(--red); }
.rag-tile.total  { border-top-color: var(--accent); }

.rag-tile .count {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 2.4rem;
    font-weight: 600;
    line-height: 1;
    margin-bottom: 0.35rem;
}
.rag-tile.green .count  { color: var(--green); }
.rag-tile.amber .count  { color: var(--amber); }
.rag-tile.red   .count  { color: var(--red); }
.rag-tile.total .count  { color: var(--accent); }

.rag-tile .label {
    font-size: 0.75rem;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-muted);
}
.rag-tile .sub-stats {
    margin-top: 0.6rem;
    font-size: 0.72rem;
    color: var(--text-dim);
    line-height: 1.6;
    font-family: 'IBM Plex Mono', monospace;
}

/* ── Section headers ── */
.section-header {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem;
    font-weight: 500;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.4rem;
    margin: 1.5rem 0 0.9rem 0;
}

/* ── Trend summary row ── */
.trend-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.75rem;
    margin-bottom: 1.25rem;
}
.trend-tile {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.85rem 1rem;
    display: flex;
    align-items: center;
    gap: 0.85rem;
}
.trend-icon {
    font-size: 1.5rem;
    line-height: 1;
}
.trend-tile .t-count {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.6rem;
    font-weight: 600;
}
.trend-tile.slipping .t-count  { color: var(--red); }
.trend-tile.stalled  .t-count  { color: var(--amber); }
.trend-tile.recovering .t-count { color: var(--green); }
.trend-tile.stable   .t-count  { color: var(--text-muted); }
.trend-tile .t-label {
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
}
.trend-tile .t-sub {
    font-size: 0.68rem;
    color: var(--text-dim);
    margin-top: 0.1rem;
    font-family: 'IBM Plex Mono', monospace;
}

/* ── Data table base ── */
.stDataFrame { border: 1px solid var(--border) !important; }

/* ── Critical path table ── */
.crit-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
    font-family: 'IBM Plex Sans', sans-serif;
}
.crit-table th {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.68rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-muted);
    padding: 0.4rem 0.6rem;
    border-bottom: 1px solid var(--border);
    text-align: left;
    font-weight: 500;
}
.crit-table td {
    padding: 0.5rem 0.6rem;
    border-bottom: 1px solid var(--border);
    color: var(--text-primary);
    vertical-align: middle;
}
.crit-table tr:last-child td { border-bottom: none; }
.crit-table tr:hover td { background: #232840; }

.badge {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 2px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.68rem;
    font-weight: 500;
    letter-spacing: 0.05em;
}
.badge.red   { background: #3d1515; color: var(--red); }
.badge.amber { background: #3d2c0a; color: var(--amber); }
.badge.green { background: #0d2e1a; color: var(--green); }
.badge.blue  { background: #0d1e3d; color: var(--accent); }

.float-neg  { color: var(--red);   font-family: 'IBM Plex Mono', monospace; }
.float-low  { color: var(--amber); font-family: 'IBM Plex Mono', monospace; }
.float-ok   { color: var(--green); font-family: 'IBM Plex Mono', monospace; }

/* ── Gatekeeper heatmap ── */
.gk-grid {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
}
.gk-row {
    display: grid;
    grid-template-columns: 160px 1fr 80px;
    align-items: center;
    gap: 0.75rem;
    font-size: 0.78rem;
}
.gk-name {
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 0.76rem;
}
.gk-bar-bg {
    background: var(--bg-panel);
    border-radius: 2px;
    height: 8px;
    position: relative;
}
.gk-bar-fill {
    border-radius: 2px;
    height: 100%;
    transition: width 0.6s ease;
}
.gk-count {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
    color: var(--text-muted);
    text-align: right;
}

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background: var(--bg-panel);
    border-right: 1px solid var(--border);
}
section[data-testid="stSidebar"] .block-container {
    padding: 1.25rem 1rem !important;
}
.sidebar-logo {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.65rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--text-dim);
    margin-bottom: 1.25rem;
    padding-bottom: 0.75rem;
    border-bottom: 1px solid var(--border);
}
.sidebar-section {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.62rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text-dim);
    margin: 1.1rem 0 0.4rem 0;
}
.user-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    background: var(--tag-bg);
    border: 1px solid var(--border);
    border-radius: 2px;
    padding: 0.25rem 0.6rem;
    font-size: 0.75rem;
    color: var(--text-primary);
    margin-bottom: 0.5rem;
}
.user-role {
    font-size: 0.68rem;
    color: var(--text-dim);
    margin-top: 0.1rem;
}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# MOCK USERS
# ══════════════════════════════════════════════════════════════════════════════

USERS = {
    # Each user has a job-title role (display only) and a default_role that pre-selects
    # the permission level in the Active Role radio when the user changes.
    # discipline=None for project.director means "show all disciplines" on filtered views.
    "sarah.chen":       {"display": "Sarah Chen",       "role": "Instrumentation Lead", "discipline": "Instrumentation", "default_role": "Document Controller"},
    "james.okafor":     {"display": "James Okafor",     "role": "Mechanical Lead",      "discipline": "Mechanical",      "default_role": "Document Controller"},
    "maria.lindqvist":  {"display": "Maria Lindqvist",  "role": "Electrical Lead",      "discipline": "Electrical",      "default_role": "Document Controller"},
    "hassan.al-rashid": {"display": "Hassan Al-Rashid", "role": "Civil Lead",           "discipline": "Civil",           "default_role": "Document Controller"},
    "project.director": {"display": "Project Director", "role": "All Disciplines",      "discipline": None,              "default_role": "Project Manager"},
}

# ── Role constants ─────────────────────────────────────────────────────────────
# Single source of truth for role names — avoids scattered string literals
# that break silently on a typo.
ROLE_READ_ONLY           = "Read Only"
ROLE_PROJECT_MANAGER     = "Project Manager"
ROLE_DOCUMENT_CONTROLLER = "Document Controller"
ROLES                    = [ROLE_READ_ONLY, ROLE_PROJECT_MANAGER, ROLE_DOCUMENT_CONTROLLER]

# ── RAG colour map ─────────────────────────────────────────────────────────────
# Canonical hex values used in tiles and inline styles across all pages.
RAG_COLOR_MAP = {"RED": "#ef4444", "AMBER": "#f59e0b", "GREEN": "#22c55e"}

# RAG threshold tables — must stay in sync with generate_mdr_layer.py.
# These are the source of truth for live in-session recomputation.
RAG_THRESHOLDS = {
    #                AMBER  RED
    "Very High": (21,       7),
    "High":      (14,       3),
    "Medium":    ( 7,       0),
    "Low":       ( 7,    -180),
}
CRITICAL_PATH_THRESHOLDS = (14, 3)   # Option B: tight tier applied when is_on_critical_path=True
TERMINAL_STATUSES = {
    "APPROVED_FINAL", "APPROVED_CUSTOMER", "APPROVED_AUTHORITY",
    "APPROVED_CERTIFICATION", "SUPERSEDED",
}


def derive_rag(float_days: int, priority: str, canonical_status: str,
               is_on_critical_path: bool = False) -> str:
    """Compute RAG status — mirrors generate_mdr_layer.derive_rag() exactly.

    Used for live in-session recomputation when the user changes priority or
    is_on_critical_path in the MDR Register. The result is never written to the
    CSV — rag_status is always derived, never stored permanently by the dashboard.

    Args:
        float_days:          (planned_approval_date - TODAY).days.
        priority:            Very High / High / Medium / Low.
        canonical_status:    Current lifecycle status string.
        is_on_critical_path: Applies the Critical Path tier when True.

    Returns:
        "RED", "AMBER", or "GREEN".
    """
    if canonical_status in TERMINAL_STATUSES:
        return "GREEN"
    if canonical_status == "OVERDUE":
        return "RED"
    if is_on_critical_path:
        amber_thresh, red_thresh = CRITICAL_PATH_THRESHOLDS
    else:
        amber_thresh, red_thresh = RAG_THRESHOLDS.get(priority, (7, 0))
    if float_days <= red_thresh:
        return "RED"
    if float_days <= amber_thresh:
        return "AMBER"
    return "GREEN"


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def load_mdr() -> pd.DataFrame:
    """Load the MDR CSV into a DataFrame — source of truth for all dashboard data.

    The CSV lives at data_generation/mdr_requirements.csv. Every inline edit
    the user makes in the dashboard is written back to this same file via save_mdr().

    Returns:
        pd.DataFrame: 60-row MDR with date columns parsed as Python datetime objects.

    Side effects:
        Calls st.stop() and shows a browser error if the CSV file is missing.
    """
    _log("LOAD", f"Reading MDR from {MDR_CSV}")

    if not MDR_CSV.exists():
        _log("ERROR", f"MDR CSV not found at {MDR_CSV} — run generate_mdr_layer.py first")
        st.error(f"MDR CSV not found at {MDR_CSV}. Run generate_mdr_layer.py first.")
        st.stop()

    df = pd.read_csv(MDR_CSV, parse_dates=["planned_submission_date", "planned_approval_date",
                                            "baseline_approval_date", "previous_approval_date"])
    _log("LOAD", f"MDR loaded — {len(df)} rows x {len(df.columns)} columns")

    # Always recompute rag_status from current field values — it is a derived field,
    # not a stored one.  This ensures the Option B critical-path tier is applied
    # without needing a pipeline re-run, and that any priority changes saved by the PM
    # are reflected immediately on the next load.
    def _cp(val):
        """Safely coerce is_on_critical_path to bool regardless of CSV dtype."""
        if isinstance(val, bool):
            return val
        return str(val).lower() == "true"

    # pandas 3.0 reads string columns as StringDtype (PyArrow-backed) when pyarrow is
    # installed.  Streamlit's data_editor cannot render PyArrow-backed strings and shows
    # 0 rows silently.  Convert ALL string columns to plain object dtype immediately after
    # reading so every downstream operation — filtering, display, edits — works as expected.
    str_cols = df.select_dtypes(include="string").columns.tolist()
    if str_cols:
        df[str_cols] = df[str_cols].astype(object)

    df["rag_status"] = df.apply(
        lambda row: derive_rag(
            int(row["schedule_float_days"]) if pd.notna(row.get("schedule_float_days")) else 0,
            str(row.get("priority", "Medium")),
            str(row.get("current_canonical_status", "")),
            _cp(row.get("is_on_critical_path", False)),
        ),
        axis=1,
    )

    # A 100%-complete or terminal-status document cannot logically be "SLIPPING" —
    # its schedule is irrelevant once the work is done.  Force date_trend to STABLE.
    completed_mask = (
        (pd.to_numeric(df["reported_percent_complete"], errors="coerce").fillna(0) >= 100) |
        (df["current_canonical_status"].isin(TERMINAL_STATUSES))
    )
    df.loc[completed_mask, "date_trend"] = "STABLE"

    if DEBUG:
        # Extra detail: RAG breakdown lets you verify the data looks right at a glance
        print(f"         RAG distribution: {df['rag_status'].value_counts().to_dict()}")
        print(f"         Columns: {list(df.columns)}")

    return df


def load_saved_views() -> dict:
    """Load saved views from JSON, creating default seed if missing."""
    if not SAVED_VIEWS_JSON.exists():
        seed = {
            "Critical Path — All": {
                "owner": "project.director",
                "shared": True,
                "created_at": str(datetime.now(timezone.utc)),
                "filters": {"is_on_critical_path": True},
                "sort": {"field": "schedule_float_days", "ascending": True},
                "columns": ["mdr_id", "document_title", "discipline", "priority",
                            "rag_status", "schedule_float_days", "current_canonical_status"],
            },
            "My Red Items": {
                "owner": "project.director",
                "shared": False,
                "created_at": str(datetime.now(timezone.utc)),
                "filters": {"rag_status": "RED"},
                "sort": {"field": "schedule_float_days", "ascending": True},
                "columns": ["mdr_id", "document_title", "discipline", "priority",
                            "schedule_float_days", "date_trend", "responsible_person"],
            },
            "Mechanical Register": {
                "owner": "james.okafor",
                "shared": True,
                "created_at": str(datetime.now(timezone.utc)),
                "filters": {"discipline": "Mechanical"},
                "sort": {"field": "planned_approval_date", "ascending": True},
                "columns": ["mdr_id", "document_title", "approval_class", "priority",
                            "rag_status", "planned_approval_date", "reported_percent_complete"],
            },
        }
        SAVED_VIEWS_JSON.parent.mkdir(parents=True, exist_ok=True)
        with open(SAVED_VIEWS_JSON, "w") as f:
            json.dump(seed, f, indent=2, default=str)
        return seed

    with open(SAVED_VIEWS_JSON) as f:
        return json.load(f)


def save_view(name: str, view: dict):
    views = load_saved_views()
    views[name] = view
    with open(SAVED_VIEWS_JSON, "w") as f:
        json.dump(views, f, indent=2, default=str)


def load_bookmarks() -> pd.DataFrame:
    """Load bookmarks CSV, creating with headers if missing."""
    cols = ["username", "mdr_id", "personal_note", "created_at"]
    if not BOOKMARKS_CSV.exists():
        # Seed a few demo bookmarks
        seed = pd.DataFrame([
            # IDs are ISO 19650 canonical identifiers generated by the STAGED layer.
            # Format: {PROJECT}-{ORIGINATOR}-{VOLUME}-{TYPE}-{DISCIPLINE}-{SEQUENCE}
            {"username": "sarah.chen",       "mdr_id": "PROJ1-ALPHAENG-ZZ-SH-IN-000001", "personal_note": "Authority approval — watch closely",       "created_at": str(TODAY)},
            {"username": "james.okafor",     "mdr_id": "PROJ1-ALPHAENG-ZZ-DS-ME-000001", "personal_note": "Critical path — vendor drawings pending",   "created_at": str(TODAY)},
            {"username": "project.director", "mdr_id": "PROJ1-ALPHAENG-ZZ-DS-ME-000001", "personal_note": "Flagged by client last week",               "created_at": str(TODAY)},
            {"username": "project.director", "mdr_id": "PROJ1-ALPHAENG-ZZ-DR-EL-000001", "personal_note": "",                                          "created_at": str(TODAY)},
        ], columns=cols)
        BOOKMARKS_CSV.parent.mkdir(parents=True, exist_ok=True)
        seed.to_csv(BOOKMARKS_CSV, index=False)
        return seed
    return pd.read_csv(BOOKMARKS_CSV)


def log_edit(username: str, mdr_id: str, field: str, old_val, new_val):
    """Append a single field change to the audit log (edit_log.csv).

    This is an append-only file — entries are never deleted or modified.
    It gives a full history of every change made to the MDR through the dashboard.

    Each row records:
        timestamp  — when the change happened (UTC, so it's timezone-safe)
        username   — which mock user made the change (e.g. "sarah.chen")
        mdr_id     — which document was changed (e.g. "MDR-INS-001")
        field      — which column was edited (e.g. "priority")
        old_value  — the value before the edit (stored as a plain string)
        new_value  — the value after the edit (stored as a plain string)

    Args:
        username: The currently active mock user.
        mdr_id:   The document identifier.
        field:    The column name that was changed.
        old_val:  The previous value (any type — converted to str for storage).
        new_val:  The new value (any type — converted to str for storage).
    """
    _log("EDIT", f"{username} | {mdr_id} | {field}: '{old_val}' -> '{new_val}'")

    row = {
        "timestamp":  str(datetime.now(timezone.utc)),
        "username":   username,
        "mdr_id":     mdr_id,
        "field":      field,
        "old_value":  str(old_val),
        "new_value":  str(new_val),
    }
    cols = list(row.keys())
    exists = EDIT_LOG_CSV.exists()

    try:
        with open(EDIT_LOG_CSV, "a", newline="", encoding="utf-8") as f:
            import csv as _csv
            w = _csv.DictWriter(f, fieldnames=cols)
            if not exists:
                # First ever entry — write the column headers before the first row
                w.writeheader()
            w.writerow(row)
    except Exception as e:
        _log("ERROR", f"Failed to write edit log: {e}")


def save_csv(df: pd.DataFrame, path: Path, label: str, date_cols: list[str] | None = None):
    """Write a DataFrame to CSV — shared by save_mdr() and save_dq_flags().

    Args:
        df:        DataFrame to write (a copy is made internally).
        path:      Destination CSV path.
        label:     Short name for log/error messages, e.g. "MDR", "DQ flags".
        date_cols: Columns holding datetime objects that must be str()-converted
                   before writing so the CSV stays human-readable.
    """
    _log("SAVE", f"Writing {len(df)} rows to {path}")
    out = df.copy()
    if date_cols:
        for col in date_cols:
            if col in out.columns:
                out[col] = out[col].astype(str)
    try:
        out.to_csv(path, index=False)
        _log("SAVE", f"{label} CSV updated successfully")
    except Exception as e:
        _log("ERROR", f"Failed to write {label} CSV: {e}")
        st.error(f"Could not save {label}: {e}")


def save_mdr(df: pd.DataFrame):
    """Persist MDR edits to CSV. Date columns are serialised to ISO strings.

    Args:
        df: Full MDR DataFrame including any user edits.
    """
    save_csv(df, MDR_CSV, "MDR", date_cols=[
        "planned_submission_date", "planned_approval_date",
        "baseline_approval_date", "previous_approval_date",
    ])


def save_dq_flags(dq: pd.DataFrame):
    """Persist DC-resolved DQ flags to CSV.

    Args:
        dq: Full DQ flags DataFrame with resolved/resolved_by/resolved_at updated.
    """
    save_csv(dq, DQ_FLAGS_CSV, "DQ flags")


# ══════════════════════════════════════════════════════════════════════════════
# SNOWFLAKE HELPER (read-only — RAW + STAGED)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource(ttl=300)
def get_snowflake_connection():
    """Open a connection to Snowflake and cache it for 5 minutes.

    @st.cache_resource means Streamlit creates this connection once and reuses
    it across all reruns until the TTL (time-to-live) of 300 seconds expires.
    Without caching, every button click would open a new database connection,
    which is slow (1-3 seconds) and wasteful.

    Reads these keys from .env (loaded at startup via load_dotenv):
        SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, SNOWFLAKE_ACCOUNT,
        SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE, SNOWFLAKE_ROLE (optional)

    Returns:
        A live Snowflake connection object, or None if anything went wrong.
        Callers must check for None before using the connection.
    """
    _log("SNOWFLAKE", "Opening connection to Snowflake...")
    try:
        import snowflake.connector
        conn = snowflake.connector.connect(
            user      = os.environ["SNOWFLAKE_USER"],
            password  = os.environ["SNOWFLAKE_PASSWORD"],
            account   = os.environ["SNOWFLAKE_ACCOUNT"],
            warehouse = os.environ["SNOWFLAKE_WAREHOUSE"],
            database  = os.environ["SNOWFLAKE_DATABASE"],
            role      = os.environ.get("SNOWFLAKE_ROLE", ""),
        )
        _log("SNOWFLAKE", f"Connected — database: {os.environ['SNOWFLAKE_DATABASE']}")
        return conn
    except KeyError as e:
        # KeyError means the variable name doesn't exist in .env at all
        _log("ERROR", f"Missing Snowflake credential in .env: {e} — check .env.example for required keys")
        return None
    except Exception as e:
        # Catches wrong password, suspended warehouse, bad account name, network issues, etc.
        _log("ERROR", f"Snowflake connection failed: {e}")
        return None


@st.cache_data(ttl=300)
def query_snowflake(sql: str) -> pd.DataFrame:
    """Run a read-only SQL query against Snowflake and return the results as a DataFrame.

    @st.cache_data means identical queries within 5 minutes return a cached copy
    instead of hitting the database again. This keeps the dashboard fast — Snowflake
    queries can take 1-5 seconds each, and Streamlit reruns on every interaction.

    This function is used for RAW and STAGED layer data (read-only display).
    The ANALYTICAL layer (MDR) is read from the local CSV instead, because
    dashboard edits write to the CSV, not back to Snowflake.

    Args:
        sql: A SELECT query string. Do not pass INSERT/UPDATE/DELETE here.

    Returns:
        pd.DataFrame with query results, or an empty DataFrame if the connection
        is unavailable or the query fails. Always check if the result is empty
        before trying to use it.
    """
    # Truncate the SQL in the log so long queries don't flood the terminal
    preview = sql.strip().replace("\n", " ")
    _log("SNOWFLAKE", f"Query: {preview[:80]}{'...' if len(preview) > 80 else ''}")

    conn = get_snowflake_connection()
    if conn is None:
        _log("ERROR", "Snowflake query skipped — no active connection")
        return pd.DataFrame()

    try:
        result = pd.read_sql(sql, conn)
        _log("SNOWFLAKE", f"Query returned {len(result)} rows")
        return result
    except Exception as e:
        _log("ERROR", f"Snowflake query failed: {e}")
        st.warning(f"Snowflake query failed: {e}")
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# DERIVED METRICS
# ══════════════════════════════════════════════════════════════════════════════

def compute_rag_counts(df: pd.DataFrame) -> dict:
    counts = df["rag_status"].value_counts().to_dict()
    return {
        "GREEN": counts.get("GREEN", 0),
        "AMBER": counts.get("AMBER", 0),
        "RED":   counts.get("RED",   0),
        "TOTAL": len(df),
    }


def compute_trend_counts(df: pd.DataFrame) -> dict:
    counts = df["date_trend"].value_counts().to_dict()
    return {
        "SLIPPING":   counts.get("SLIPPING",   0),
        "STALLED":    counts.get("STALLED",    0),
        "RECOVERING": counts.get("RECOVERING", 0),
        "STABLE":     counts.get("STABLE",     0),
    }


def format_float(days) -> str:
    try:
        d = int(days)
        if d < 0:
            return f'<span class="float-neg">{d}d</span>'
        if d <= 14:
            return f'<span class="float-low">+{d}d</span>'
        return f'<span class="float-ok">+{d}d</span>'
    except Exception:
        return str(days)


@st.dialog("Source Document Preview")
def _source_doc_dialog(mdr_id: str, title: str):
    """Modal shown when the user checks the 'Src' column in the MDR Register.

    In a live CDE this button would open the source document from the document
    management system. Here it explains the demo constraint clearly.

    Args:
        mdr_id: ISO 19650 canonical ID of the selected document.
        title:  Human-readable document title.
    """
    st.markdown(f"### {title}")
    st.markdown(
        f"<span style='font-family:\"IBM Plex Mono\",monospace; font-size:0.8rem;"
        f" color:#7a8499;'>{mdr_id}</span>",
        unsafe_allow_html=True,
    )
    st.divider()
    st.info(
        "In a live system, clicking here would open the source document "
        "directly from the CDE (Content Management System / Document Management System). "
        "This is a portfolio demo — no real document file exists behind this link.",
        icon="📋",
    )
    st.markdown(
        '<div style="font-size:0.78rem; color:#7a8499; margin-top:0.5rem;">'
        'In production the DMS link would be:<br>'
        f'<code>https://dms.proj1.example.com/docs/{mdr_id}</code>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown("")
    # Returning from a @st.dialog function closes the dialog immediately.
    # st.rerun() inside a dialog reruns the dialog itself, not the outer app,
    # which is why it appeared unresponsive.
    if st.button("Close", type="primary", key="src_doc_close"):
        return


def rag_badge(rag: str) -> str:
    cls = rag.lower() if rag in ("RED", "AMBER", "GREEN") else "blue"
    return f'<span class="badge {cls}">{rag}</span>'


def render_rag_threshold_expander(key_suffix: str = ""):
    """Render a collapsible RAG threshold reference table.

    Shows the exact day-count thresholds used to assign RED / AMBER / GREEN
    per priority tier, including the critical-path override rule.  Calling this
    on both Overview and MDR gives users the decision logic wherever they are.

    Args:
        key_suffix: Unique string appended to the expander key to avoid
                    Streamlit key collisions when the same expander appears
                    on multiple pages in the same run.
    """
    with st.expander("RAG thresholds — how status is calculated", expanded=False):
        st.markdown("""
**RAG status** is derived live from schedule float and document priority.
Float = `planned_approval_date − today (demo date: 2026-05-08)`. Negative float = already past the planned date.

**Step 1 — pick the threshold tier:**
Documents on the critical path use a single flat **Critical Path tier** regardless of priority.
Off-critical-path documents use their **Priority tier**.

| Tier | Applies to | AMBER when float ≤ | RED when float ≤ |
|---|---|---:|---:|
| **Critical Path** | `is_on_critical_path = True` (any priority) | **14 days** | **3 days** |
| Very High | Critical path = No | 21 days | 7 days |
| High | Critical path = No | 14 days | 3 days |
| Medium | Critical path = No | 7 days | 0 days |
| Low | Critical path = No | 7 days | −180 days |

**Note:** "Very High + Critical Path" is actually *looser* than "Very High alone" because the flat CP tier (14d/3d) is tighter than Very High off-CP (21d/7d). That is intentional — the CP override exists to surface scheduling risk regardless of how a document was originally prioritised.

**Step 2 — terminal status override:**
Documents with a terminal status (APPROVED_FINAL, APPROVED_CUSTOMER, APPROVED_AUTHORITY, APPROVED_CERTIFICATION, SUPERSEDED) are forced to **GREEN** regardless of dates.

**Example:** Priority=Very High, not on critical path, float=+10d → AMBER (≤21d). Add it to critical path → RED (≤14d CP threshold, but float=10d > 3d, so AMBER by CP tier).
        """)


def gk_bar_color(count: int, max_count: int) -> str:
    ratio = count / max_count if max_count else 0
    if ratio > 0.6:
        return "#ef4444"
    if ratio > 0.3:
        return "#f59e0b"
    return "#3b82f6"


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

def render_sidebar(df: pd.DataFrame) -> tuple[str, str | None, str]:
    """Renders sidebar. Returns (current_user, active_saved_view_name | None, active_role)."""

    # ── Pending navigation request ─────────────────────────────────────────────
    # Other pages signal a navigation jump by writing to _nav_request / _detail_request
    # BEFORE this function renders the nav radio widget.  We apply them here so that
    # Streamlit sees the new value before the widget is instantiated (setting a widget
    # key after the widget has rendered raises StreamlitAPIException).
    # _back_request: written by the Back button — applies without pushing to history
    # (if we used _nav_request it would push the destination back onto the stack).
    if "_back_request" in st.session_state:
        st.session_state["nav_page"] = st.session_state.pop("_back_request")
    # _nav_request: written by link tiles / action buttons — pushes current page to
    # history so the Back button can return here.
    elif "_nav_request" in st.session_state:
        nav_history = st.session_state.setdefault("_nav_history", [])
        nav_history.append({
            "page":   st.session_state.get("nav_page", "Overview"),
            "detail": st.session_state.get("detail_doc_select"),
        })
        st.session_state["nav_page"] = st.session_state.pop("_nav_request")
    if "_detail_request" in st.session_state:
        st.session_state["detail_doc_select"] = st.session_state.pop("_detail_request")

    with st.sidebar:
        st.markdown('<div class="sidebar-logo">📋 MDR Control · PROJ1</div>', unsafe_allow_html=True)

        # ── User selector ──────────────────────────────────────────────────────
        # Changing the user affects: My Watchlist (shows that user's bookmarks)
        # and the audit log (edits are attributed to this user).
        st.markdown('<div class="sidebar-section">Active User</div>', unsafe_allow_html=True)
        user_options = list(USERS.keys())
        user_labels  = [f"{USERS[u]['display']} — {USERS[u]['role']}" for u in user_options]

        prev_user = st.session_state.get("_prev_user")
        user_idx = st.selectbox(
            "User",
            options=range(len(user_options)),
            format_func=lambda i: user_labels[i],
            label_visibility="collapsed",
            key="user_selector",
            help=(
                "Simulates switching between project team members. "
                "Affects: My Watchlist (each user has their own bookmarks) "
                "and the audit trail (edits are logged under this name)."
            ),
        )
        current_user = user_options[user_idx]
        u = USERS[current_user]

        # When the user changes, auto-suggest that user's default role by updating
        # the session_state key that the Active Role radio reads from.
        if prev_user != current_user:
            st.session_state["active_role"] = u["default_role"]
            st.session_state["_prev_user"]  = current_user
            # Pre-filter the MDR Register to this user's discipline so switching
            # users produces a visible, relevant change. project.director has
            # discipline=None which maps to "All" (no filter applied).
            disc = u.get("discipline")
            st.session_state["reg_disc"] = disc if disc else "All"

        st.markdown(
            f'<div class="user-chip">👤 {u["display"]}</div>'
            f'<div class="user-role">{u["role"]}</div>',
            unsafe_allow_html=True
        )
        st.markdown(
            '<div style="font-size:0.65rem; color:#4a5568; margin-top:0.2rem;">'
            'Controls: My Watchlist · audit log</div>',
            unsafe_allow_html=True
        )

        # ── Role selector ──────────────────────────────────────────────────────
        # The role controls what the user can do — independent of who they are.
        # Read Only = view only. PM = can edit schedule/priority fields.
        # Document Controller = can resolve DQ flags in Source System Health.
        st.markdown('<div class="sidebar-section">Active Role</div>', unsafe_allow_html=True)
        # Do not pass index= here — Streamlit ignores index when the key already
        # exists in session_state, and combining both causes a StreamlitAPIException.
        # Session state is managed above (auto-set when user changes, otherwise
        # Streamlit defaults to the first option on the very first load).
        active_role = st.radio(
            "Role",
            ROLES,
            label_visibility="collapsed",
            key="active_role",
            help=(
                "Controls edit permissions for this session. "
                "Read Only: view everything, change nothing. "
                "Project Manager: edit priority, critical path, % complete, and notes. "
                "Document Controller: resolve DQ flags in Source System Health."
            ),
        )
        st.markdown(
            '<div style="font-size:0.65rem; color:#4a5568; margin-top:0.2rem;">'
            'Controls: edit permissions</div>',
            unsafe_allow_html=True
        )

        st.divider()

        # ── Navigation ────────────────────────────────────────────────────────
        st.markdown('<div class="sidebar-section">Navigation</div>', unsafe_allow_html=True)
        page = st.radio(
            "Page",
            ["Overview", "MDR", "My Watchlist", "Document Detail", "Source System Health", "Audit Trail"],
            label_visibility="collapsed",
            key="nav_page",
            help="Switch between dashboard pages.",
        )

        # Back button — always visible; disabled when no history.
        # History is populated whenever a link, tile, or action button navigates
        # programmatically (via _nav_request). Direct radio clicks are not tracked
        # because the user is explicitly choosing a page, not following a link.
        nav_history = st.session_state.get("_nav_history", [])
        if st.button(
            "<- Back",
            key="btn_back",
            width="stretch",
            disabled=not nav_history,
            help="Return to the previous page (only available after following a link or tile)",
        ):
            prev = nav_history.pop()
            st.session_state["_nav_history"] = nav_history
            # Use _back_request so render_sidebar applies it at the TOP of the next
            # render pass, before the nav radio widget is instantiated.  Setting
            # nav_page directly here raises StreamlitAPIException because the radio
            # with key="nav_page" has already been rendered this pass.
            st.session_state["_back_request"] = prev["page"]
            if prev.get("detail"):
                st.session_state["_detail_request"] = prev["detail"]
            st.rerun()

        st.divider()

        # ── Saved Views ───────────────────────────────────────────────────────
        st.markdown('<div class="sidebar-section">Saved Views</div>', unsafe_allow_html=True)
        views      = load_saved_views()
        view_names = ["— none —"] + list(views.keys())
        selected_view_name = st.selectbox(
            "Load saved view",
            options=view_names,
            label_visibility="collapsed",
            key="saved_view_selector",
        )
        active_view = None if selected_view_name == "— none —" else selected_view_name

        # Save current view (stub — wired up in MDR Register page)
        with st.expander("Save current view", expanded=False):
            new_view_name = st.text_input("View name", key="new_view_name")
            shared_flag   = st.checkbox("Shared (visible to all users)", key="view_shared")
            if st.button("Save", key="btn_save_view"):
                if new_view_name.strip():
                    # Saved view content is set by the MDR Register page via session_state
                    view_payload = st.session_state.get("current_view_payload", {})
                    view_payload.update({
                        "owner":      current_user,
                        "shared":     shared_flag,
                        "created_at": str(datetime.now(timezone.utc)),
                    })
                    save_view(new_view_name.strip(), view_payload)
                    st.success(f"Saved: {new_view_name}")
                else:
                    st.warning("Enter a view name first.")

        st.divider()

        # ── Quick stats ───────────────────────────────────────────────────────
        st.markdown('<div class="sidebar-section">Quick Stats</div>', unsafe_allow_html=True)
        rag = compute_rag_counts(df)
        col1, col2, col3 = st.columns(3)
        col1.metric("🔴 RED",   rag["RED"])
        col2.metric("🟡 AMBER", rag["AMBER"])
        col3.metric("🟢 GREEN", rag["GREEN"])

        crit_red = len(df[(df["is_on_critical_path"] == True) & (df["rag_status"] == "RED")])
        st.markdown(
            f'<div style="font-size:0.72rem; color:#7a8499; margin-top:0.5rem;">'
            f'Critical path RED: <span style="color:#ef4444; font-family:\'IBM Plex Mono\',monospace;">{crit_red}</span>'
            f'</div>',
            unsafe_allow_html=True
        )

        st.divider()

        # ── Demo / pipeline controls ──────────────────────────────────────────
        st.markdown('<div class="sidebar-section">Demo Controls</div>', unsafe_allow_html=True)

        # 1. Reload from disk — clears Streamlit cache so next render reads fresh CSVs.
        #    Use this after running the pipeline scripts manually from the terminal.
        #    Does NOT change any data on disk.
        if st.button(
            "Reload from disk",
            width="stretch",
            key="btn_reload_disk",
            help=(
                "Clears the Streamlit data cache so all CSVs are re-read from disk on "
                "the next render. Use this after running generate_staged_layer.py or "
                "generate_mdr_layer.py from the terminal. No data is changed."
            ),
        ):
            st.cache_data.clear()
            st.session_state.pop("mdr_working", None)
            _log("LOAD", "Cache cleared — reloading all CSVs from disk on next render")
            st.rerun()

        # 2. Reset to pipeline state — clears in-memory edits, reloads from disk.
        #    MDR CSV edits (priority, critical path, notes) made through the dashboard
        #    are written to disk — this button reloads from disk without changing them.
        if st.button(
            "Reset to pipeline state",
            width="stretch",
            key="btn_reset_pipeline",
            help=(
                "Discards any unsaved in-session edits and reloads from the pipeline "
                "CSV files on disk. Dashboard edits already saved (priority, % complete, "
                "notes, is_on_critical_path) are preserved on disk — they are NOT reverted."
            ),
        ):
            st.session_state.pop("mdr_working", None)
            st.cache_data.clear()
            st.rerun()

        # 3. Full demo reset — re-runs the entire pipeline from staged → MDR layer,
        #    clears the complete edit log, and reloads. Use before each demo to get a
        #    perfectly clean state: all DQ flags unresolved, no PM edits, fresh RAG.
        if st.button(
            "Full demo reset",
            width="stretch",
            key="btn_full_reset",
            type="secondary",
            help=(
                "Re-runs generate_staged_layer.py and generate_mdr_layer.py, then "
                "clears the entire edit log (DQ resolutions AND PM edits). "
                "Use this before a demo to restore every flag and undo all edits. "
                "DISCARDS all DC resolutions, priority changes, and is_on_critical_path "
                "changes made through the dashboard."
            ),
        ):
            import subprocess
            import sys as _sys
            _staged_script = DATA_DIR / "generate_staged_layer.py"
            _mdr_script    = DATA_DIR / "generate_mdr_layer.py"
            _log("EDIT", "Full demo reset: re-running staged and MDR pipeline scripts")
            with st.spinner("Running full pipeline reset..."):
                _r1 = subprocess.run(
                    [_sys.executable, str(_staged_script)],
                    cwd=str(DATA_DIR), capture_output=True, text=True, timeout=120,
                )
                _r2 = subprocess.run(
                    [_sys.executable, str(_mdr_script)],
                    cwd=str(DATA_DIR), capture_output=True, text=True, timeout=120,
                ) if _r1.returncode == 0 else None
            if _r1.returncode != 0:
                st.error(f"generate_staged_layer.py failed:\n{_r1.stderr[:300]}")
            elif _r2 and _r2.returncode != 0:
                st.error(f"generate_mdr_layer.py failed:\n{_r2.stderr[:300]}")
            else:
                # Wipe the entire edit log so the dashboard starts completely clean.
                # Both DQ_REMEDIATION and PM_UPDATE events are cleared because the
                # regenerated MDR CSV has fresh baseline values — old edits no longer
                # correspond to the new data.
                if EDIT_LOG_CSV.exists():
                    pd.DataFrame(columns=["timestamp","username","mdr_id","field","old_value","new_value"]
                                 ).to_csv(EDIT_LOG_CSV, index=False)
                    _log("SAVE", "Full demo reset: edit_log.csv cleared")
                st.session_state.pop("mdr_working", None)
                st.cache_data.clear()
                st.rerun()

        st.markdown(
            f'<div style="font-size:0.65rem; color:#4a5568; margin-top:0.5rem; font-family:\'IBM Plex Mono\',monospace;">'
            f'Snapshot: {TODAY}</div>',
            unsafe_allow_html=True
        )

    return current_user, active_view, active_role


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════

def page_overview(df: pd.DataFrame, current_user: str, active_role: str = ROLE_READ_ONLY):
    st.markdown('<div class="page-title">Overview — Project PROJ1 · Snapshot 8 May 2026</div>', unsafe_allow_html=True)

    # ── How-to guide ──────────────────────────────────────────────────────────
    # Collapsible so it doesn't clutter the view for returning users.
    with st.expander("How to use this dashboard", expanded=False):
        st.markdown("""
**What is this?**
A Common Data Environment / Master Document Register (CDE/MDR) dashboard for Project PROJ1.
It tracks 60 engineering documents across three source systems (Windchill, SharePoint, Aveva)
and surfaces delivery risk, data quality issues, and document lifecycle status in one place.

---

**Sidebar — Active User**
Simulates switching between team members. Each user has their own **My Watchlist** (bookmarks)
and all edits are attributed to the selected user in the audit log.
Switching user automatically suggests an appropriate role.

**Sidebar — Active Role**
Controls what you can edit in this session:
- **Read Only** — view any page, no changes allowed.
- **Project Manager** — edit Priority, Critical Path, % Complete, and Notes in the MDR Register.
- **Document Controller** — resolve DQ flags (missing data, normalisation issues) in Source System Health.

---

**Pages:**
| Page | Purpose |
|---|---|
| Overview | RAG summary, date trend, critical path at a glance |
| MDR | Full document list — filter, sort, export, inline edits (PM role) |
| My Watchlist | Bookmarked documents with personal notes — quick status check |
| Document Detail | Single document deep-dive: metadata + full STAGED lifecycle timeline |
| Source System Health | Per-source RAG, DQ flag queue, mark flags resolved (DC role) |

---

**RAG status** is computed from days of schedule float vs. the document's priority:
- RED = overdue or critically close to deadline
- AMBER = within the warning window
- GREEN = on track

**Date Trend** shows whether delivery dates are moving: Slipping / Stalled / Recovering / Stable.
        """)

    # ── RAG Summary Tiles ──────────────────────────────────────────────────────
    rag = compute_rag_counts(df)

    # Sub-stats per tile
    red_crit  = len(df[(df["rag_status"] == "RED")   & (df["is_on_critical_path"] == True)])
    amb_crit  = len(df[(df["rag_status"] == "AMBER") & (df["is_on_critical_path"] == True)])
    red_vh    = len(df[(df["rag_status"] == "RED")   & (df["priority"] == "Very High")])
    total_cp  = len(df[df["is_on_critical_path"] == True])

    st.markdown(f"""
    <div class="rag-grid">
        <div class="rag-tile red">
            <div class="count">{rag['RED']}</div>
            <div class="label">Red</div>
            <div class="sub-stats">
                Critical path: {red_crit}<br>
                Very High priority: {red_vh}
            </div>
        </div>
        <div class="rag-tile amber">
            <div class="count">{rag['AMBER']}</div>
            <div class="label">Amber</div>
            <div class="sub-stats">
                Critical path: {amb_crit}<br>
                &nbsp;
            </div>
        </div>
        <div class="rag-tile green">
            <div class="count">{rag['GREEN']}</div>
            <div class="label">Green</div>
            <div class="sub-stats">
                On track<br>
                &nbsp;
            </div>
        </div>
        <div class="rag-tile total">
            <div class="count">{rag['TOTAL']}</div>
            <div class="label">Total Documents</div>
            <div class="sub-stats">
                Critical path: {total_cp}<br>
                &nbsp;
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── RAG filter buttons — navigate to MDR Register pre-filtered ────────────
    # Buttons sit in four columns to align under each tile.
    # reg_rag is a filter widget key in page_mdr_register — setting it here
    # works because that widget hasn't been rendered yet (we're on Overview).
    def _nav_to_register(rag_val=None, trend_val=None, disc_val=None, person_val=None):
        """Navigate to MDR Register with specific filters pre-set, all others reset to All.

        Resetting the other filter keys prevents cross-contamination from discipline
        pre-filters (e.g. sarah.chen has reg_disc=Instrumentation) which would otherwise
        silently reduce '12 RED' to '2 RED' by intersecting with the user discipline filter.
        """
        for k in ("reg_disc", "reg_rag", "reg_prio", "reg_cp", "reg_appr", "reg_trend", "reg_person", "reg_search"):
            st.session_state[k] = "All"
        if rag_val:
            st.session_state["reg_rag"]    = rag_val
        if trend_val:
            st.session_state["reg_trend"]  = trend_val
        if disc_val:
            st.session_state["reg_disc"]   = disc_val
        if person_val:
            st.session_state["reg_person"] = person_val
        # Signal to page_mdr_register that explicit filter values were just set here.
        # The _reg_view reset block will skip its saved-view override so these values
        # are not overwritten when active_view happens to differ from _reg_view.
        st.session_state["_nav_filters_set"] = True
        st.session_state["_nav_request"] = "MDR"
        st.rerun()

    btn_r, btn_a, btn_g, btn_t = st.columns(4)
    with btn_r:
        if st.button(f"View {rag['RED']} RED ->", key="ov_nav_red"):
            _nav_to_register(rag_val="RED")
    with btn_a:
        if st.button(f"View {rag['AMBER']} AMBER ->", key="ov_nav_amber"):
            _nav_to_register(rag_val="AMBER")
    with btn_g:
        if st.button(f"View {rag['GREEN']} GREEN ->", key="ov_nav_green"):
            _nav_to_register(rag_val="GREEN")
    with btn_t:
        if st.button(f"View all {rag['TOTAL']} ->", key="ov_nav_all"):
            _nav_to_register()

    # ── RAG legend ────────────────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.68rem; color:#4a5568; margin-top:0.2rem; margin-bottom:0.25rem;">'
        '<b style="color:#ef4444;">RED</b> = at or past the deadline warning threshold for this priority &nbsp;|&nbsp; '
        '<b style="color:#f59e0b;">AMBER</b> = within warning window (Very High: &le;21d, High: &le;14d, Medium: &le;7d) &nbsp;|&nbsp; '
        '<b style="color:#22c55e;">GREEN</b> = on track'
        '</div>',
        unsafe_allow_html=True
    )
    render_rag_threshold_expander("overview")

    # ── Date Trend Summary ─────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Date Trend</div>', unsafe_allow_html=True)
    trend = compute_trend_counts(df)

    st.markdown(f"""
    <div class="trend-grid">
        <div class="trend-tile slipping">
            <div class="trend-icon">📉</div>
            <div>
                <div class="t-count">{trend['SLIPPING']}</div>
                <div class="t-label">Slipping</div>
                <div class="t-sub">&nbsp;</div>
            </div>
        </div>
        <div class="trend-tile stalled">
            <div class="trend-icon">⏸</div>
            <div>
                <div class="t-count">{trend['STALLED']}</div>
                <div class="t-label">Stalled</div>
                <div class="t-sub">&nbsp;</div>
            </div>
        </div>
        <div class="trend-tile recovering">
            <div class="trend-icon">📈</div>
            <div>
                <div class="t-count">{trend['RECOVERING']}</div>
                <div class="t-label">Recovering</div>
                <div class="t-sub">&nbsp;</div>
            </div>
        </div>
        <div class="trend-tile stable">
            <div class="trend-icon">→</div>
            <div>
                <div class="t-count">{trend['STABLE']}</div>
                <div class="t-label">Stable</div>
                <div class="t-sub">&nbsp;</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Date trend filter buttons ─────────────────────────────────────────────
    tb_sl, tb_st, tb_re, tb_sta = st.columns(4)
    with tb_sl:
        if st.button(f"View {trend['SLIPPING']} Slipping ->", key="ov_nav_slip"):
            _nav_to_register(trend_val="SLIPPING")
    with tb_st:
        if st.button(f"View {trend['STALLED']} Stalled ->", key="ov_nav_stall"):
            _nav_to_register(trend_val="STALLED")
    with tb_re:
        if st.button(f"View {trend['RECOVERING']} Recovering ->", key="ov_nav_rec"):
            _nav_to_register(trend_val="RECOVERING")
    with tb_sta:
        if st.button(f"View {trend['STABLE']} Stable ->", key="ov_nav_stable"):
            _nav_to_register(trend_val="STABLE")

    # ── Date trend legend ─────────────────────────────────────────────────────
    st.markdown(
        '<div style="font-size:0.68rem; color:#4a5568; margin-top:0.2rem; margin-bottom:0.5rem;">'
        '<b>Slipping</b> = approval date moved right &gt;5d since last month &nbsp;|&nbsp; '
        '<b>Stalled</b> = already &gt;14d behind baseline but not moving recently (resource bottleneck) &nbsp;|&nbsp; '
        '<b>Recovering</b> = date improved &gt;3d since last month &nbsp;|&nbsp; '
        '<b>Stable</b> = date movement within ±5d'
        '</div>',
        unsafe_allow_html=True
    )

    # ── Two-column layout: Critical Path items | Gatekeeper heatmap ───────────
    col_left, col_right = st.columns([3, 2], gap="large")

    with col_left:
        st.markdown('<div class="section-header">Critical Path — RED &amp; AMBER</div>', unsafe_allow_html=True)

        crit = df[
            (df["is_on_critical_path"] == True) &
            (df["rag_status"].isin(["RED", "AMBER"]))
        ].sort_values(["rag_status", "schedule_float_days"])

        if crit.empty:
            st.markdown('<p style="color:#4a5568; font-size:0.8rem;">No critical path items at RED or AMBER.</p>', unsafe_allow_html=True)
        else:
            # Render as per-row columns with an explicit Detail button in each row.
            # We avoid st.dataframe(on_select) because Streamlit adds an unavoidable
            # selection-checkbox column which confused users.
            TREND_LABEL = {"SLIPPING": "Slipping", "STALLED": "Stalled",
                           "RECOVERING": "Recovering", "STABLE": "Stable"}

            # Column header row
            h1, h2, h3, h4, h5, h6 = st.columns([1.6, 3.2, 1.1, 1.0, 1.8, 1.3])
            h1.markdown('<span style="font-size:0.72rem;color:#7a8499;font-family:\'IBM Plex Mono\',monospace;">ID</span>', unsafe_allow_html=True)
            h2.markdown('<span style="font-size:0.72rem;color:#7a8499;font-family:\'IBM Plex Mono\',monospace;">DOCUMENT</span>', unsafe_allow_html=True)
            h3.markdown('<span style="font-size:0.72rem;color:#7a8499;font-family:\'IBM Plex Mono\',monospace;">DISC.</span>', unsafe_allow_html=True)
            h4.markdown('<span style="font-size:0.72rem;color:#7a8499;font-family:\'IBM Plex Mono\',monospace;">RAG</span>', unsafe_allow_html=True)
            h5.markdown('<span style="font-size:0.72rem;color:#7a8499;font-family:\'IBM Plex Mono\',monospace;">FLOAT / TREND</span>', unsafe_allow_html=True)
            h6.markdown("")
            st.markdown('<hr style="margin:0.2rem 0 0.4rem 0;border-color:#2a3040;">', unsafe_allow_html=True)

            for _, row in crit.iterrows():
                c1, c2, c3, c4, c5, c6 = st.columns([1.6, 3.2, 1.1, 1.0, 1.8, 1.3])
                rag_color = RAG_COLOR_MAP.get(str(row["rag_status"]), "#7a8499")
                trend_lbl = TREND_LABEL.get(str(row["date_trend"]), str(row["date_trend"]))
                float_d   = int(row["schedule_float_days"]) if pd.notna(row.get("schedule_float_days")) else 0
                c1.markdown(f'<span style="font-size:0.72rem;font-family:\'IBM Plex Mono\',monospace;color:#7a8499;">{row["mdr_id"]}</span>', unsafe_allow_html=True)
                c2.markdown(f'<span style="font-size:0.82rem;">{row["document_title"]}</span>', unsafe_allow_html=True)
                c3.markdown(f'<span style="font-size:0.78rem;color:#7a8499;">{row["discipline"]}</span>', unsafe_allow_html=True)
                c4.markdown(f'<span style="color:{rag_color};font-weight:600;font-size:0.82rem;">● {row["rag_status"]}</span>', unsafe_allow_html=True)
                c5.markdown(f'<span style="font-size:0.78rem;color:#7a8499;">{float_d}d &nbsp;·&nbsp; {trend_lbl}</span>', unsafe_allow_html=True)
                with c6:
                    if st.button("Detail ->", key=f"cp_det_{row['mdr_id']}", use_container_width=True):
                        st.session_state["_nav_request"]    = "Document Detail"
                        st.session_state["_detail_request"] = row["mdr_id"]
                        st.rerun()

    with col_right:
        # Gatekeeper heatmap is restricted to PM and DC — it names individuals who
        # are sitting on stalled documents, which is management-level information.
        if active_role in (ROLE_PROJECT_MANAGER, ROLE_DOCUMENT_CONTROLLER):
            st.markdown('<div class="section-header">Gatekeeper Heatmap — Stalled Documents</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="section-header">Gatekeeper Heatmap</div>', unsafe_allow_html=True)
            st.markdown(
                '<div style="background:#1e2333; border:1px solid #2a3040; border-radius:4px; padding:1rem;">'
                '<span style="color:#4a5568; font-size:0.82rem;">Restricted — select Project Manager or '
                'Document Controller role to view responsible-person breakdown.</span></div>',
                unsafe_allow_html=True
            )

        stalled = df[df["date_trend"] == "STALLED"]
        if active_role not in (ROLE_PROJECT_MANAGER, ROLE_DOCUMENT_CONTROLLER):
            pass  # role gate shown above; skip the heatmap body entirely
        elif stalled.empty:
            st.markdown('<p style="color:#4a5568; font-size:0.8rem;">No stalled documents.</p>', unsafe_allow_html=True)
        else:
            gk_counts = (
                stalled.groupby("responsible_person")
                .size()
                .sort_values(ascending=False)
                .head(10)
            )
            max_count = gk_counts.max()

            for person, count in gk_counts.items():
                bar_width = int(100 * count / max_count)
                bar_color = gk_bar_color(count, max_count)
                # Render bar as HTML, then place a native button in the same row
                bar_col, btn_col = st.columns([5, 1])
                with bar_col:
                    st.markdown(
                        f'<div class="gk-row">'
                        f'<div class="gk-name">{person}</div>'
                        f'<div class="gk-bar-bg">'
                        f'<div class="gk-bar-fill" style="width:{bar_width}%;background:{bar_color};"></div>'
                        f'</div>'
                        f'<div class="gk-count">{count} doc{"s" if count != 1 else ""}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                with btn_col:
                    if st.button(
                        "View ->",
                        key=f"gk_nav_{person}",
                        use_container_width=True,
                        help=f"Show stalled documents for {person} in MDR Register",
                    ):
                        # Filter by person + STALLED — exact match on responsible_person,
                        # not discipline, to avoid picking up other people's stalled docs.
                        _nav_to_register(trend_val="STALLED", person_val=person)

            st.markdown(
                f'<div style="font-size:0.68rem; color:#4a5568; margin-top:0.9rem; font-family:\'IBM Plex Mono\',monospace;">'
                f'STALLED = total slip &gt;14d AND recent slip within +-5d. Indicates resource bottleneck.</div>',
                unsafe_allow_html=True
            )

    # ── Bottom row: Discipline breakdown ──────────────────────────────────────
    st.markdown('<div class="section-header">Discipline Summary</div>', unsafe_allow_html=True)

    disc_df = (
        df.groupby("discipline")
          .agg(
              total=("mdr_id", "count"),
              red=("rag_status", lambda x: (x == "RED").sum()),
              amber=("rag_status", lambda x: (x == "AMBER").sum()),
              green=("rag_status", lambda x: (x == "GREEN").sum()),
              on_cp=("is_on_critical_path", lambda x: x.eq(True).sum()),
              avg_float=("schedule_float_days", "mean"),
          )
          .reset_index()
          .sort_values("red", ascending=False)
    )

    cols = st.columns(len(disc_df))
    for col, (_, row) in zip(cols, disc_df.iterrows()):
        with col:
            st.markdown(f"""
            <div style="background:#1e2333; border:1px solid #2a3040; border-radius:4px; padding:0.85rem 1rem;">
                <div style="font-family:'IBM Plex Mono',monospace; font-size:0.65rem; letter-spacing:0.1em; color:#7a8499; text-transform:uppercase; margin-bottom:0.5rem;">{row['discipline']}</div>
                <div style="display:flex; gap:0.75rem; align-items:baseline; margin-bottom:0.4rem;">
                    <span style="color:{RAG_COLOR_MAP['RED']}; font-family:'IBM Plex Mono',monospace; font-weight:600;">{int(row['red'])}</span>
                    <span style="color:{RAG_COLOR_MAP['AMBER']}; font-family:'IBM Plex Mono',monospace; font-weight:600;">{int(row['amber'])}</span>
                    <span style="color:{RAG_COLOR_MAP['GREEN']}; font-family:'IBM Plex Mono',monospace; font-weight:600;">{int(row['green'])}</span>
                    <span style="color:#4a5568; font-size:0.7rem;">/ {int(row['total'])}</span>
                </div>
                <div style="font-size:0.68rem; color:#4a5568; font-family:'IBM Plex Mono',monospace;">
                    CP: {int(row['on_cp'])} · avg float {row['avg_float']:.0f}d
                </div>
            </div>
            """, unsafe_allow_html=True)
            # Navigation button — filters MDR Register to this discipline
            if st.button(
                f"View {row['discipline']} ->",
                key=f"disc_nav_{row['discipline']}",
                use_container_width=True,
                help=f"Open MDR Register filtered to {row['discipline']} documents",
            ):
                _nav_to_register(disc_val=row["discipline"])

    # ── Pipeline Flags summary ────────────────────────────────────────────────
    # Show a quick count of unresolved DQ flags by type so users know at a
    # glance whether the pipeline flagged anything needing DC attention.
    # Full detail lives in Source System Health -> DC Remediation Queue.
    st.markdown('<div class="section-header">Pipeline Flags — Unresolved</div>', unsafe_allow_html=True)
    if DQ_FLAGS_CSV.exists():
        _log("LOAD", "Reading DQ flags for Overview summary")
        dq = pd.read_csv(DQ_FLAGS_CSV)
        unresolved = dq[dq["resolved"].astype(str).str.lower() != "true"]
        if unresolved.empty:
            st.markdown(
                '<p style="color:#22c55e; font-size:0.82rem;">All pipeline flags resolved. No action required.</p>',
                unsafe_allow_html=True
            )
        else:
            # Group by flag_type for a compact summary row
            flag_counts = unresolved["flag_type"].value_counts()
            total_unresolved = len(unresolved)
            flag_cols = st.columns(len(flag_counts) + 1)
            with flag_cols[0]:
                st.markdown(f"""
                <div style="background:#1e2333; border:1px solid #ef4444; border-radius:4px; padding:0.7rem 1rem;">
                    <div style="font-family:'IBM Plex Mono',monospace; font-size:0.65rem; color:#7a8499; text-transform:uppercase; margin-bottom:0.3rem;">Total</div>
                    <div style="font-size:1.4rem; font-weight:700; color:#ef4444; font-family:'IBM Plex Mono',monospace;">{total_unresolved}</div>
                    <div style="font-size:0.68rem; color:#7a8499;">unresolved flags</div>
                </div>
                """, unsafe_allow_html=True)
            for col, (flag_type, count) in zip(flag_cols[1:], flag_counts.items()):
                # Human-readable label: MISSING_MANDATORY_FIELD -> Missing Field
                label = flag_type.replace("_", " ").title()
                with col:
                    st.markdown(f"""
                    <div style="background:#1e2333; border:1px solid #2a3040; border-radius:4px; padding:0.7rem 1rem;">
                        <div style="font-family:'IBM Plex Mono',monospace; font-size:0.65rem; color:#7a8499; text-transform:uppercase; margin-bottom:0.3rem;">{label}</div>
                        <div style="font-size:1.4rem; font-weight:700; color:#f59e0b; font-family:'IBM Plex Mono',monospace;">{count}</div>
                        <div style="font-size:0.68rem; color:#7a8499;">flags</div>
                    </div>
                    """, unsafe_allow_html=True)
            if st.button(
                f"Resolve {total_unresolved} flag(s) in Source System Health ->",
                key="flags_nav_health",
                use_container_width=False,
                help="Open the DC Remediation Queue in Source System Health",
            ):
                st.session_state["_nav_request"] = "Source System Health"
                st.rerun()
    else:
        st.caption("staged_dq_flags.csv not found — run generate_staged_layer.py to populate.")

    # ── My Watchlist quick access ─────────────────────────────────────────────
    # Show the current user's bookmarked documents as a compact strip so they
    # can jump to anything flagged without going to My Watchlist first.
    st.markdown('<div class="section-header">My Watchlist — Quick Access</div>', unsafe_allow_html=True)
    bookmarks = load_bookmarks()
    user_bm = bookmarks[bookmarks["username"] == current_user]
    if user_bm.empty:
        st.markdown(
            '<p style="color:#4a5568; font-size:0.8rem;">No bookmarks yet — star documents in the MDR Register to add them here.</p>',
            unsafe_allow_html=True
        )
    else:
        # Enrich bookmark rows with MDR data for the quick tiles
        quick_cols = ["mdr_id", "document_title", "rag_status", "discipline", "reported_percent_complete"]
        quick = user_bm.merge(df[quick_cols], on="mdr_id", how="inner")
        tile_cols = st.columns(min(len(quick), 4))  # up to 4 across, then wrap
        for col, (_, brow) in zip(tile_cols, quick.iterrows()):
            rag_hex = RAG_COLOR_MAP.get(brow["rag_status"], "#4a5568")
            short_title = str(brow["document_title"])[:32] + ("..." if len(str(brow["document_title"])) > 32 else "")
            with col:
                st.markdown(f"""
                <div style="background:#1e2333; border-left:3px solid {rag_hex}; border-radius:4px;
                            padding:0.7rem 0.9rem; margin-bottom:0.4rem;">
                    <div style="font-family:'IBM Plex Mono',monospace; font-size:0.65rem; color:#7a8499;">{brow['mdr_id']}</div>
                    <div style="font-size:0.78rem; color:#e8ecf4; margin:0.2rem 0; font-weight:500;">{short_title}</div>
                    <div style="font-size:0.68rem; color:#7a8499;">{brow['discipline']} · {int(brow['reported_percent_complete'])}%</div>
                </div>
                """, unsafe_allow_html=True)
                # Button navigates to Document Detail pre-loaded with this document
                if st.button("Open ->", key=f"ov_detail_{brow['mdr_id']}", width="stretch"):
                    st.session_state["_nav_request"]    = "Document Detail"
                    st.session_state["_detail_request"] = brow["mdr_id"]
                    _log("LOAD", f"Quick access: navigating to Document Detail for {brow['mdr_id']}")
                    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE STUBS (Day 4+)
# ══════════════════════════════════════════════════════════════════════════════

def page_mdr_register(df: pd.DataFrame, current_user: str, active_view: str | None, active_role: str = "Read Only"):  # noqa: C901
    st.markdown('<div class="page-title">Master Document Register</div>', unsafe_allow_html=True)

    # Load saved view defaults
    views     = load_saved_views()
    view_data = views.get(active_view, {}) if active_view else {}
    vf        = view_data.get("filters", {})
    v_cols    = view_data.get("columns", [])
    v_sort    = view_data.get("sort", {})

    # Reset filter widget state when the active view changes so new defaults apply.
    # Exception: if _nav_to_register just set explicit filter values, skip the reset so
    # navigation-driven filters (e.g. "View 12 RED ->") are not overwritten by the saved
    # view's defaults.  Always update _reg_view so the check stays accurate next render.
    nav_just_set_filters = st.session_state.pop("_nav_filters_set", False)
    if st.session_state.get("_reg_view") != active_view and not nav_just_set_filters:
        for k in ("reg_disc", "reg_rag", "reg_prio", "reg_cp", "reg_appr", "reg_trend", "reg_person", "reg_search", "reg_cols"):
            st.session_state.pop(k, None)
    st.session_state["_reg_view"] = active_view

    # ── Filters ───────────────────────────────────────────────────────────────
    with st.expander("Filters", expanded=True):
        fc1, fc2, fc3, fc4, fc5, fc6 = st.columns(6)

        disc_opts   = ["All"] + sorted(df["discipline"].dropna().unique().tolist())
        rag_opts    = ["All", "RED", "AMBER", "GREEN"]
        prio_opts   = ["All", "Very High", "High", "Medium", "Low"]
        cp_opts     = ["All", "Critical Path only", "Non-critical only"]
        appr_opts   = ["All"] + sorted(df["approval_class"].dropna().unique().tolist())
        trend_opts  = ["All", "SLIPPING", "STALLED", "RECOVERING", "STABLE"]
        person_opts = ["All"] + sorted(df["responsible_person"].dropna().unique().tolist())

        def _idx(lst, val): return lst.index(val) if val in lst else 0

        sel_disc   = fc1.selectbox("Discipline",    disc_opts,   index=_idx(disc_opts,   vf.get("discipline")),     key="reg_disc")
        sel_rag    = fc2.selectbox("RAG",           rag_opts,    index=_idx(rag_opts,    vf.get("rag_status")),     key="reg_rag")
        sel_prio   = fc3.selectbox("Priority",      prio_opts,   index=_idx(prio_opts,   vf.get("priority")),       key="reg_prio")
        vf_cp_val  = "Critical Path only" if vf.get("is_on_critical_path") is True else ("Non-critical only" if vf.get("is_on_critical_path") is False else "All")
        sel_cp     = fc4.selectbox("Critical Path", cp_opts,     index=_idx(cp_opts,     vf_cp_val),                key="reg_cp")
        sel_appr   = fc5.selectbox("Approval",      appr_opts,   index=_idx(appr_opts,   vf.get("approval_class")), key="reg_appr")
        sel_trend  = fc6.selectbox("Date Trend",    trend_opts,  index=_idx(trend_opts,  vf.get("date_trend")),     key="reg_trend")

        # Responsible person filter — shown in a second row; primarily set via Gatekeeper navigation
        fp1, fp2, _fp3 = st.columns([2, 4, 1])
        sel_person = fp1.selectbox("Responsible Person", person_opts, index=_idx(person_opts, None), key="reg_person")

        # Free-text search — partial, case-insensitive match across key fields.
        # Scales to any number of documents; complementary to the dropdowns above.
        search_query = fp2.text_input(
            "Search",
            placeholder="Filter by document ID, title, responsible person, or discipline...",
            key="reg_search",
            label_visibility="visible",
            help=(
                "Case-insensitive partial match across MDR ID, document title, "
                "responsible person, and discipline. Works alongside the dropdown filters."
            ),
        )

    # Apply filters — each line narrows the DataFrame by one criterion
    filt = df.copy()
    if sel_disc   != "All": filt = filt[filt["discipline"]          == sel_disc]
    if sel_rag    != "All": filt = filt[filt["rag_status"]          == sel_rag]
    if sel_prio   != "All": filt = filt[filt["priority"]            == sel_prio]
    if sel_cp    == "Critical Path only": filt = filt[filt["is_on_critical_path"] == True]
    elif sel_cp  == "Non-critical only":  filt = filt[filt["is_on_critical_path"] != True]
    if sel_appr   != "All": filt = filt[filt["approval_class"]      == sel_appr]
    if sel_trend  != "All": filt = filt[filt["date_trend"]          == sel_trend]
    if sel_person != "All": filt = filt[filt["responsible_person"]  == sel_person]

    # Free-text search — applied last, on top of all dropdown filters
    if search_query.strip():
        q = search_query.strip().lower()
        filt = filt[
            filt["mdr_id"].str.lower().str.contains(q, na=False)
            | filt["document_title"].str.lower().str.contains(q, na=False)
            | filt["responsible_person"].str.lower().str.contains(q, na=False)
            | filt["discipline"].str.lower().str.contains(q, na=False)
        ]

    _log("FILTER", (
        f"disc={sel_disc} | rag={sel_rag} | prio={sel_prio} | "
        f"cp={sel_cp} | appr={sel_appr} | trend={sel_trend} | person={sel_person} "
        f"| search='{search_query}' -> {len(filt)} of {len(df)} rows"
    ))

    render_rag_threshold_expander("mdr")

    # ── Column selector ───────────────────────────────────────────────────────
    # Only Project Managers can write PM_UPDATE events (priority, critical path,
    # percent complete, notes). All other roles get disabled columns.
    can_edit_mdr  = (active_role == ROLE_PROJECT_MANAGER)
    EDITABLE_COLS = ["is_on_critical_path", "priority", "reported_percent_complete", "notes"]
    ALL_OPTIONAL  = [
        "document_title", "discipline", "priority", "rag_status", "is_on_critical_path",
        "schedule_float_days", "date_trend", "current_canonical_status", "approval_class",
        "planned_approval_date", "total_slip_days", "responsible_person",
        "reported_percent_complete", "derived_percent_complete", "source_system", "notes",
    ]
    DEFAULT_COLS = [
        "document_title", "discipline", "priority", "rag_status", "is_on_critical_path",
        "schedule_float_days", "date_trend", "current_canonical_status",
        "reported_percent_complete", "responsible_person", "notes",
    ]
    col_default   = [c for c in (v_cols or DEFAULT_COLS) if c in ALL_OPTIONAL]
    selected_cols = st.multiselect("Columns", options=ALL_OPTIONAL, default=col_default, key="reg_cols")

    display_cols = ["mdr_id"] + [c for c in selected_cols if c != "mdr_id"]

    # ── Sort ──────────────────────────────────────────────────────────────────
    s1, s2, _ = st.columns([3, 1, 4])
    sort_field = s1.selectbox(
        "Sort by", display_cols,
        index=_idx(display_cols, v_sort.get("field", "schedule_float_days")),
        key="reg_sort_field",
    )
    sort_asc = s2.checkbox("Ascending", value=v_sort.get("ascending", True), key="reg_sort_asc")

    filt_sorted = filt.sort_values(sort_field, ascending=sort_asc, na_position="last") \
                  if sort_field in filt.columns else filt

    # ── Bookmarks ─────────────────────────────────────────────────────────────
    bookmarks   = load_bookmarks()
    user_bm_ids = set(bookmarks[bookmarks["username"] == current_user]["mdr_id"].tolist())

    # ── Build display df ──────────────────────────────────────────────────────
    display_df = filt_sorted[display_cols].copy().reset_index(drop=True)
    display_df.insert(1, "bookmarked", display_df["mdr_id"].isin(user_bm_ids))

    # Trigger columns — always False on display; the value is never written to CSV.
    # Checking a cell fires the action (navigate / open dialog) on the next rerun.
    # Streamlit's data_editor has no "link" cell type for internal navigation, so
    # a CheckboxColumn is the only per-row trigger mechanism available.
    display_df.insert(1, "open_detail", False)
    # Place view_source next to the title column if it is visible
    if "document_title" in display_df.columns:
        title_pos = list(display_df.columns).index("document_title")
        display_df.insert(title_pos + 1, "view_source", False)
    else:
        display_df["view_source"] = False

    # ── Active-filter summary + clear button ─────────────────────────────────
    active_filters = {
        k: v for k, v in {
            "Discipline": sel_disc, "RAG": sel_rag, "Priority": sel_prio,
            "Critical Path": sel_cp, "Approval": sel_appr,
            "Trend": sel_trend, "Person": sel_person,
            "Search": search_query.strip() or None,
        }.items() if v and v not in ("All", None, "")
    }
    filter_row_left, filter_row_right = st.columns([7, 1])
    with filter_row_left:
        if active_filters:
            tags = "  ".join(
                f'<span style="background:#252d40;border:1px solid #3b82f6;border-radius:3px;'
                f'padding:0.1rem 0.45rem;font-size:0.72rem;color:#93c5fd;">'
                f'{k}: {v}</span>'
                for k, v in active_filters.items()
            )
            st.markdown(
                f'<div style="margin:0.1rem 0 0.4rem 0;">Filters active: {tags}</div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            f'<div style="font-size:0.75rem;color:#7a8499;margin:0.1rem 0 0.4rem 0;">'
            f'Showing <b>{len(display_df)}</b> of {len(df)} documents</div>',
            unsafe_allow_html=True,
        )
    with filter_row_right:
        if active_filters and st.button("Clear filters", key="mdr_clear_filters"):
            for k in ("reg_disc", "reg_rag", "reg_prio", "reg_cp", "reg_appr",
                      "reg_trend", "reg_person", "reg_search"):
                st.session_state[k] = "All"
            st.session_state["reg_search"] = ""
            st.rerun()

    # ── Column config ─────────────────────────────────────────────────────────
    col_cfg = {
        "mdr_id":       st.column_config.TextColumn("ID", width="small", disabled=True),
        # Navigation trigger columns — check to fire action; value is never saved.
        # Arrow label makes clear these are links, not data fields.
        "open_detail":  st.column_config.CheckboxColumn(
            "->",
            width="small",
            help="Check to open Document Detail for this row (same tab)",
        ),
        "view_source":  st.column_config.CheckboxColumn(
            "Src",
            width="small",
            help="Check to preview the source document link concept (demo popup)",
        ),
        "bookmarked":   st.column_config.CheckboxColumn("⭐", width="small", help="Add to My Watchlist"),
        "document_title": st.column_config.TextColumn("Title", width="large", disabled=True),
        "discipline":                st.column_config.TextColumn("Discipline", disabled=True),
        "priority":                  st.column_config.SelectboxColumn("Priority", options=["Very High", "High", "Medium", "Low"], disabled=not can_edit_mdr),
        "rag_status":                st.column_config.TextColumn("RAG", width="small", disabled=True),
        "is_on_critical_path":       st.column_config.CheckboxColumn("Crit. Path", disabled=not can_edit_mdr),
        "schedule_float_days":       st.column_config.NumberColumn("Float (d)", format="%d", disabled=True),
        "date_trend":                st.column_config.TextColumn("Trend", width="small", disabled=True),
        "current_canonical_status":  st.column_config.TextColumn("Status", disabled=True),
        "approval_class":            st.column_config.TextColumn("Approval Class", disabled=True),
        "planned_approval_date":     st.column_config.DateColumn("Planned Approval", disabled=True),
        "total_slip_days":           st.column_config.NumberColumn("Slip (d)", format="%d", disabled=True),
        "responsible_person":        st.column_config.TextColumn("Responsible", disabled=True),
        "reported_percent_complete": st.column_config.NumberColumn("% Complete", min_value=0, max_value=100, step=5, format="%d%%", disabled=not can_edit_mdr),
        "derived_percent_complete":  st.column_config.NumberColumn("% Complete (auto)", format="%.0f%%", disabled=True),
        "source_system":             st.column_config.TextColumn("Source System", disabled=True),
        "notes":                     st.column_config.TextColumn("Notes", width="large", disabled=not can_edit_mdr),
    }

    # ── Editable table ────────────────────────────────────────────────────────
    # Show a banner when the user cannot edit — explains why the fields appear greyed out
    if not can_edit_mdr:
        st.info(f"Read Only — switch to {ROLE_PROJECT_MANAGER} in the sidebar to edit priority, critical path, percent complete, and notes.")

    edited_df = st.data_editor(
        display_df,
        column_config=col_cfg,
        width="stretch",
        hide_index=True,
        key="mdr_register_editor",
        num_rows="fixed",
    )

    # ── Trigger column actions — checked first, before any persistence ──────────
    # open_detail: check a row -> navigate to Document Detail.
    # view_source: check a row -> open the source document demo dialog.
    # Both columns default to False and are never written to the MDR CSV.
    nav_rows = edited_df[edited_df["open_detail"].astype(bool)]
    if not nav_rows.empty:
        target_id = nav_rows.iloc[0]["mdr_id"]
        st.session_state["_nav_request"]    = "Document Detail"
        st.session_state["_detail_request"] = target_id
        _log("LOAD", f"MDR: navigating to Document Detail for {target_id}")
        st.rerun()

    src_rows = edited_df[edited_df["view_source"].astype(bool)]
    if not src_rows.empty:
        src_row   = src_rows.iloc[0]
        src_title = str(src_row.get("document_title", src_row["mdr_id"]))
        _source_doc_dialog(src_row["mdr_id"], src_title)

    # ── Document Actions — alternative navigation for users who prefer not to ──
    # use the trigger columns.  Selectbox lists all currently visible documents.
    # Selecting one + clicking a button is equivalent to checking a trigger cell.
    st.markdown('<hr style="border-color:#2a3040;margin:0.75rem 0 0.5rem 0;">', unsafe_allow_html=True)
    action_opts = ["— select a document —"] + [
        f'{r["mdr_id"]}  ·  {r.get("document_title", "")}' for _, r in display_df.iterrows()
    ]
    sel_col, det_col, src_col = st.columns([5, 1, 1])
    with sel_col:
        action_sel = st.selectbox(
            "Document actions",
            action_opts,
            key="reg_action_sel",
            label_visibility="collapsed",
            help="Pick a document from the filtered list, then click Detail or Src Doc.",
        )
    action_disabled = action_sel.startswith("—")
    with det_col:
        if st.button(
            "Detail ->",
            key="reg_action_detail",
            use_container_width=True,
            disabled=action_disabled,
            help="Open Document Detail for the selected document (same tab)",
        ):
            target_id = action_sel.split("  ·  ")[0].strip()
            _log("LOAD", f"MDR Register: navigating to Document Detail for {target_id}")
            st.session_state["_nav_request"]    = "Document Detail"
            st.session_state["_detail_request"] = target_id
            st.rerun()
    with src_col:
        if st.button(
            "Src Doc ->",
            key="reg_action_src",
            use_container_width=True,
            disabled=action_disabled,
            help="Preview the CDE source document link concept (demo popup)",
        ):
            target_id    = action_sel.split("  ·  ")[0].strip()
            target_title = "  ·  ".join(action_sel.split("  ·  ")[1:]).strip()
            _source_doc_dialog(target_id, target_title)

    # ── Persist MDR edits ─────────────────────────────────────────────────────
    # RAG_TRIGGERS: editing either of these fields may change rag_status, so we
    # recompute it for the affected rows after writing the new field value to df.
    # df is st.session_state["mdr_working"] (same object, passed by reference), so
    # updating df here updates the working copy without an extra assignment.
    RAG_TRIGGERS = {"priority", "is_on_critical_path"}

    has_changes     = False
    rag_recompute_ids = set()   # mdr_ids whose RAG needs recomputing this cycle

    for edit_col in EDITABLE_COLS:
        if edit_col not in display_df.columns:
            continue
        orig    = display_df[edit_col]
        new     = edited_df[edit_col]
        changed = ~(orig.eq(new) | (orig.isna() & new.isna()))
        if changed.any():
            for i, row in edited_df[changed].iterrows():
                mdr_id  = row["mdr_id"]
                old_val = orig.iloc[i]
                new_val = row[edit_col]
                df.loc[df["mdr_id"] == mdr_id, edit_col] = new_val
                log_edit(current_user, mdr_id, edit_col, old_val, new_val)
                if edit_col in RAG_TRIGGERS:
                    rag_recompute_ids.add(mdr_id)
            has_changes = True

    # Recompute rag_status for any document where a RAG-triggering field changed.
    # Reading from df (already updated above) so the new priority / critical_path
    # value is used, not the old display_df value.
    for mid in rag_recompute_ids:
        mask = df["mdr_id"] == mid
        row  = df[mask].iloc[0]
        cp   = row["is_on_critical_path"]
        cp   = cp if isinstance(cp, bool) else str(cp).lower() == "true"
        new_rag = derive_rag(
            int(row["schedule_float_days"]) if pd.notna(row.get("schedule_float_days")) else 0,
            str(row["priority"]),
            str(row["current_canonical_status"]),
            cp,
        )
        df.loc[mask, "rag_status"] = new_rag
        _log("EDIT", f"RAG recomputed for {mid}: {new_rag} (priority={row['priority']}, cp={cp})")

    if has_changes:
        save_mdr(df)
        st.toast("Changes saved.", icon="✅")
        st.rerun()

    # ── Persist bookmark changes ───────────────────────────────────────────────
    bm_changed = ~(display_df["bookmarked"].eq(edited_df["bookmarked"]))
    if bm_changed.any():
        all_bm = load_bookmarks()
        for i, row in edited_df[bm_changed].iterrows():
            mdr_id = row["mdr_id"]
            if bool(row["bookmarked"]):
                # User ticked the star — add a new bookmark entry
                _log("BOOKMARK", f"{current_user} added {mdr_id} to watchlist")
                new_entry = pd.DataFrame([{"username": current_user, "mdr_id": mdr_id,
                                           "personal_note": "", "created_at": str(TODAY)}])
                all_bm = pd.concat([all_bm, new_entry], ignore_index=True)
            else:
                # User unticked the star — remove their bookmark for this document
                _log("BOOKMARK", f"{current_user} removed {mdr_id} from watchlist")
                all_bm = all_bm[~((all_bm["username"] == current_user) & (all_bm["mdr_id"] == mdr_id))]
        try:
            all_bm.to_csv(BOOKMARKS_CSV, index=False)
        except Exception as e:
            _log("ERROR", f"Failed to write bookmarks CSV: {e}")
        st.toast("Watchlist updated.", icon="⭐")
        st.rerun()

    # ── Export ────────────────────────────────────────────────────────────────
    import io
    try:
        buf = io.BytesIO()
        filt_sorted[display_cols].to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)
        st.download_button(
            "📥 Export to Excel", data=buf,
            file_name=f"MDR_Register_{TODAY}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="btn_export",
        )
    except ImportError:
        csv_data = filt_sorted[display_cols].to_csv(index=False)
        st.download_button(
            "📥 Export to CSV", data=csv_data,
            file_name=f"MDR_Register_{TODAY}.csv",
            mime="text/csv",
            key="btn_export",
        )

    # ── Update saved view payload (consumed by sidebar Save) ─────────────────
    st.session_state["current_view_payload"] = {
        "filters": {k: v for k, v in {
            "discipline":          sel_disc  if sel_disc  != "All" else None,
            "rag_status":          sel_rag   if sel_rag   != "All" else None,
            "priority":            sel_prio  if sel_prio  != "All" else None,
            "is_on_critical_path": (True if sel_cp == "Critical Path only" else (False if sel_cp == "Non-critical only" else None)),
            "approval_class":      sel_appr  if sel_appr  != "All" else None,
            "date_trend":          sel_trend if sel_trend != "All" else None,
        }.items() if v is not None},
        "columns": selected_cols,
        "sort":    {"field": sort_field, "ascending": sort_asc},
    }


def page_watchlist(df: pd.DataFrame, current_user: str):
    """My Watchlist page.

    Shows documents the current user has bookmarked, enriched with live MDR
    data (title, discipline, RAG status, priority, canonical status, % complete).
    Users can edit their personal note in-place and remove bookmarks without
    going back to the MDR Register.

    Args:
        df:           Full MDR DataFrame — joined with bookmarks for document details.
        current_user: Currently active mock user (filters bookmarks and saves changes).
    """
    st.markdown('<div class="page-title">My Watchlist</div>', unsafe_allow_html=True)

    # ── Load this user's bookmarks ────────────────────────────────────────────
    _log("LOAD", f"Loading bookmarks for {current_user}")
    bookmarks = load_bookmarks()
    user_bm   = bookmarks[bookmarks["username"] == current_user].copy()

    if user_bm.empty:
        st.info(
            "No bookmarks yet. Open the MDR Register and tick the star column "
            "on any document to add it here."
        )
        return

    # ── Join with MDR to enrich each bookmark row ─────────────────────────────
    # Only pull in the fields needed for the card — we don't want to duplicate
    # the full register. Inner join: if an mdr_id no longer exists in the MDR
    # the bookmark is silently skipped (orphan guard).
    mdr_cols = [
        "mdr_id", "document_title", "discipline", "rag_status",
        "priority", "current_canonical_status", "reported_percent_complete",
    ]
    merged = user_bm.merge(df[mdr_cols], on="mdr_id", how="inner")

    n = len(merged)
    st.markdown(f"**{n} bookmarked document{'s' if n != 1 else ''}**")
    st.markdown("---")

    # ── One card per bookmark ──────────────────────────────────────────────────
    for _, row in merged.iterrows():
        with st.container():

            # Header: document title on the left, RAG badge on the right
            col_title, col_rag = st.columns([6, 1])
            with col_title:
                # mdr_id shown in small monospace beside the title
                st.markdown(
                    f"**{row['document_title']}**"
                    f"&nbsp;&nbsp;"
                    f"<span style='font-family:\"IBM Plex Mono\",monospace;"
                    f" font-size:0.72rem; color:#7a8499;'>{row['mdr_id']}</span>",
                    unsafe_allow_html=True,
                )
            with col_rag:
                st.markdown(rag_badge(row["rag_status"]), unsafe_allow_html=True)

            # Detail row: four label-value pairs
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.caption("Discipline")
                st.markdown(f"**{row['discipline']}**")
            with c2:
                st.caption("Priority")
                st.markdown(f"**{row['priority']}**")
            with c3:
                st.caption("Status")
                # Convert SNAKE_CASE status to "Title Case" for readability
                display_status = str(row["current_canonical_status"]).replace("_", " ").title()
                st.markdown(f"**{display_status}**")
            with c4:
                st.caption("% Complete")
                st.markdown(f"**{int(row['reported_percent_complete'])}%**")

            st.markdown("")  # small vertical gap before the note field

            # Personal note — editable text area
            current_note = str(row["personal_note"]) if pd.notna(row["personal_note"]) else ""
            new_note = st.text_area(
                "Personal note",
                value=current_note,
                key=f"note_{row['mdr_id']}",
                height=68,
                placeholder="Add a personal note...",
                label_visibility="collapsed",
            )

            # Action buttons: Save note | View Detail | Remove
            btn_save_col, btn_remove_col, btn_detail_col, _ = st.columns([1.2, 1.2, 1.5, 4])

            with btn_save_col:
                if st.button("Save note", key=f"save_{row['mdr_id']}", type="primary"):
                    # Only write to disk if the note actually changed
                    if new_note != current_note:
                        all_bm = load_bookmarks()
                        mask = (
                            (all_bm["username"] == current_user) &
                            (all_bm["mdr_id"]   == row["mdr_id"])
                        )
                        all_bm.loc[mask, "personal_note"] = new_note
                        try:
                            all_bm.to_csv(BOOKMARKS_CSV, index=False)
                            _log("BOOKMARK", f"{current_user} updated note on {row['mdr_id']}")
                            st.toast("Note saved.")
                        except Exception as e:
                            _log("ERROR", f"Failed to save note: {e}")
                            st.error(f"Could not save note: {e}")
                    else:
                        st.toast("No change to save.")

            with btn_remove_col:
                if st.button("Remove", key=f"remove_{row['mdr_id']}", type="secondary"):
                    all_bm = load_bookmarks()
                    # Drop this user's bookmark for this document
                    all_bm = all_bm[
                        ~((all_bm["username"] == current_user) & (all_bm["mdr_id"] == row["mdr_id"]))
                    ]
                    try:
                        all_bm.to_csv(BOOKMARKS_CSV, index=False)
                        _log("BOOKMARK", f"{current_user} removed {row['mdr_id']} from watchlist")
                    except Exception as e:
                        _log("ERROR", f"Failed to remove bookmark: {e}")
                        st.error(f"Could not remove bookmark: {e}")
                    # Rerun so the card disappears immediately
                    st.rerun()

            with btn_detail_col:
                # Use staging keys (_nav_request / _detail_request) rather than setting
                # the widget keys directly.  render_sidebar() applies them before the nav
                # radio and detail selectbox are instantiated, avoiding StreamlitAPIException.
                if st.button("View Detail ->", key=f"detail_{row['mdr_id']}", type="secondary"):
                    st.session_state["_nav_request"]    = "Document Detail"
                    st.session_state["_detail_request"] = row["mdr_id"]
                    _log("LOAD", f"Navigating to Document Detail for {row['mdr_id']}")
                    st.rerun()

            st.markdown("---")


def page_document_detail(df: pd.DataFrame, current_user: str):
    """Document Detail page.

    Shows a structured view of a single MDR document: key metadata fields
    grouped by category (Classification, Schedule, Status, Key Dates,
    Responsibility), followed by the full STAGED lifecycle timeline —
    all status transitions recorded at pipeline ingestion time for that document.

    Args:
        df:           Full MDR DataFrame — source for document metadata.
        current_user: Currently active mock user (unused here, reserved for future edits).
    """
    st.markdown('<div class="page-title">Document Detail</div>', unsafe_allow_html=True)

    # ── Document selector ─────────────────────────────────────────────────────
    # Build a title lookup so the dropdown shows a readable label, not just an ID.
    title_map = dict(zip(df["mdr_id"], df["document_title"]))
    mdr_ids   = df["mdr_id"].tolist()
    selected  = st.selectbox(
        "Select document",
        options=mdr_ids,
        format_func=lambda mid: f"{title_map.get(mid, mid)}  ({mid})",
        key="detail_doc_select",
    )

    row = df[df["mdr_id"] == selected].iloc[0]
    _log("LOAD", f"Document detail view for {selected}")

    # ── Early lookup: resolved author value ───────────────────────────────────
    # The 'author' field lives in the source system (wc_author on Windchill) and
    # is NOT in mdr_requirements.csv.  When the DC resolves a MISSING_MANDATORY_FIELD
    # flag for 'author', the corrected value lands in edit_log.csv.  We look it up
    # here so it can be shown in the Responsibility section at the top of the page,
    # not just buried in the DQ Flag History section at the bottom.
    _detail_author = "—"
    if DQ_FLAGS_CSV.exists() and EDIT_LOG_CSV.exists():
        _dq_snap = pd.read_csv(DQ_FLAGS_CSV)
        _elog_snap = pd.read_csv(EDIT_LOG_CSV)
        # Find all DQ flags for this document that are about the 'author' field
        _author_flags = _dq_snap[
            (_dq_snap["mdr_id"] == selected)
            & (_dq_snap["field_name"] == "author")
        ]
        if not _author_flags.empty:
            # Try to find the DC's corrected value in edit_log for any of these flags
            for _fid in _author_flags["flag_id"].tolist():
                _res = _elog_snap[
                    (_elog_snap["mdr_id"] == selected)
                    & (_elog_snap["field"] == f"dq_flag_resolved:{_fid}")
                ]
                if not _res.empty:
                    _raw = str(_res.iloc[0].get("new_value", ""))
                    if _raw and _raw.lower() not in ("nan", ""):
                        _detail_author = _raw
                        break
            # If still not resolved, check if the flag itself has a suggested value
            if _detail_author == "—":
                _sugg = str(_author_flags.iloc[0].get("suggested_value", ""))
                if _sugg and _sugg.lower() not in ("nan", ""):
                    _detail_author = f"{_sugg} (pipeline suggestion)"

    st.markdown("---")

    # ── Document header ───────────────────────────────────────────────────────
    col_title, col_rag = st.columns([6, 1])
    with col_title:
        st.markdown(f"### {row['document_title']}")
        st.markdown(
            f"<span style='font-family:\"IBM Plex Mono\",monospace; font-size:0.8rem;"
            f" color:#7a8499;'>{row['mdr_id']}</span>",
            unsafe_allow_html=True,
        )
    with col_rag:
        st.markdown(rag_badge(row["rag_status"]), unsafe_allow_html=True)

    st.markdown("")

    # ── Classification ────────────────────────────────────────────────────────
    st.markdown("**Classification**")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.caption("Discipline")
        st.markdown(f"**{row['discipline']}**")
    with c2:
        st.caption("Document Type")
        st.markdown(f"**{row['document_type']}**")
    with c3:
        st.caption("File Format")
        st.markdown(f"**{row['file_format']}**")
    with c4:
        st.caption("Approval Class")
        st.markdown(f"**{row['approval_class']}**")

    st.markdown("")

    # ── Schedule ──────────────────────────────────────────────────────────────
    st.markdown("**Schedule**")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.caption("Priority")
        st.markdown(f"**{row['priority']}**")
    with c2:
        st.caption("Critical Path")
        # is_on_critical_path may arrive as bool or the strings "True"/"False"
        cp_raw = row["is_on_critical_path"]
        cp_str = "Yes" if str(cp_raw).lower() in ("true", "1", "yes") else "No"
        st.markdown(f"**{cp_str}**")
    with c3:
        st.caption("% Complete")
        st.markdown(f"**{int(row['reported_percent_complete'])}%**")
    with c4:
        st.caption("Schedule Float")
        fv = row.get("schedule_float_days")
        float_str = f"{int(fv)} days" if pd.notna(fv) else "—"
        st.markdown(f"**{float_str}**")

    st.markdown("")

    # ── Status & revision ─────────────────────────────────────────────────────
    st.markdown("**Status**")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.caption("Current Status")
        display_status = str(row["current_canonical_status"]).replace("_", " ").title()
        st.markdown(f"**{display_status}**")
    with c2:
        st.caption("Revision")
        st.markdown(f"**{row['current_revision']}**")
    with c3:
        st.caption("Confidentiality")
        st.markdown(f"**{row['confidentiality_class']}**")
    with c4:
        st.caption("Source System")
        st.markdown(f"**{row['source_system']}**")

    st.markdown("")

    # ── Key dates ─────────────────────────────────────────────────────────────
    st.markdown("**Key Dates**")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.caption("Planned Submission")
        st.markdown(f"**{fmt_date(row['planned_submission_date'])}**")
    with c2:
        st.caption("Planned Approval")
        st.markdown(f"**{fmt_date(row['planned_approval_date'])}**")
    with c3:
        st.caption("Baseline Approval")
        st.markdown(f"**{fmt_date(row.get('baseline_approval_date'))}**")
    with c4:
        st.caption("Last Status Change")
        # last_status_change is a full ISO timestamp — fmt_date trims to date only
        st.markdown(f"**{fmt_date(row.get('last_status_change'))}**")

    st.markdown("")

    # ── Responsibility ────────────────────────────────────────────────────────
    st.markdown("**Responsibility**")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.caption("Responsible Person")
        st.markdown(f"**{row['responsible_person']}**")
    with c2:
        st.caption("Responsible Company")
        st.markdown(f"**{row['responsible_company']}**")
    with c3:
        st.caption("Certifying Body")
        cb = row.get("certifying_body")
        st.markdown(f"**{cb if pd.notna(cb) and str(cb).strip() else '—'}**")
    with c4:
        # Author comes from the source system (wc_author on Windchill); it is not
        # stored in mdr_requirements.csv.  The value shown here is the DC-corrected
        # value from the DQ remediation audit trail (looked up at the top of this
        # function).  Shows '—' if no DQ flag for author exists or it is unresolved.
        st.caption("Author")
        st.markdown(f"**{_detail_author}**")

    # ── Notes (only shown if present) ────────────────────────────────────────
    notes_val = row.get("notes")
    if pd.notna(notes_val) and str(notes_val).strip():
        st.markdown("")
        st.markdown("**Notes**")
        st.info(str(notes_val))

    # ── STAGED lifecycle timeline ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### STAGED Lifecycle Timeline")
    st.caption(
        "Status transitions recorded at pipeline ingestion time from the source system. "
        "Each row is one state change for this document."
    )

    if not STAGED_EVENTS_CSV.exists():
        _log("ERROR", f"staged_events.csv not found at {STAGED_EVENTS_CSV}")
        st.error(
            "staged_events.csv not found. "
            "Run data_generation/generate_staged_layer.py first."
        )
        return

    _log("LOAD", f"Reading staged events from {STAGED_EVENTS_CSV}")
    events_all = pd.read_csv(STAGED_EVENTS_CSV)

    # Join key: mdr row's fulfilled_by_document_id matches staged_events' document_id
    doc_uuid = row["fulfilled_by_document_id"]
    events = events_all[events_all["document_id"] == doc_uuid].copy()

    if events.empty:
        st.warning(f"No STAGED events found for document_id {doc_uuid}.")
        return

    # Sort chronologically so the timeline reads top-to-bottom in time order
    events = events.sort_values("planned_timestamp").reset_index(drop=True)

    # ── Helper formatters for the display table ───────────────────────────────
    def fmt_ts(val):
        """Trim ISO timestamp to date portion only (2025-10-03T... -> 2025-10-03)."""
        s = str(val)
        return s[:10] if len(s) >= 10 and s != "nan" else "—"

    def fmt_status(val):
        """SNAKE_CASE_STATUS -> Title Case Status for human readability."""
        return str(val).replace("_", " ").title() if pd.notna(val) else "—"

    def fmt_num(val):
        """Numeric field to int string, or — if missing."""
        try:
            return str(int(val)) if pd.notna(val) else "—"
        except (ValueError, TypeError):
            return "—"

    # Build a clean display-only DataFrame — raw UUIDs and internal IDs are hidden
    timeline = pd.DataFrame({
        "#":              range(1, len(events) + 1),
        "From Status":    events["from_status"].map(fmt_status),
        "To Status":      events["to_status"].map(fmt_status),
        "Planned":        events["planned_timestamp"].map(fmt_ts),
        "Actual":         events["actual_timestamp"].map(fmt_ts),
        "Variance (d)":   events["variance_days"].map(fmt_num),
        "Rev":            events["target_revision"],
        "Approval Class": events["approval_class"],
        "Entered By":     events["entered_by"].fillna("—"),
        "Comments":       events["comments"].fillna(""),
    })

    st.dataframe(
        timeline,
        width="stretch",
        hide_index=True,
        column_config={
            "#":              st.column_config.NumberColumn("#", width="small"),
            "From Status":    st.column_config.TextColumn("From Status",    width="medium"),
            "To Status":      st.column_config.TextColumn("To Status",      width="medium"),
            "Planned":        st.column_config.TextColumn("Planned",        width="small"),
            "Actual":         st.column_config.TextColumn("Actual",         width="small"),
            "Variance (d)":   st.column_config.TextColumn("Var (d)",        width="small"),
            "Rev":            st.column_config.TextColumn("Rev",            width="small"),
            "Approval Class": st.column_config.TextColumn("Approval",       width="small"),
            "Entered By":     st.column_config.TextColumn("Entered By",     width="medium"),
            "Comments":       st.column_config.TextColumn("Comments",       width="large"),
        },
    )

    st.caption(
        f"{len(events)} events  |  source: staged_events.csv  |  "
        f"document_id: {doc_uuid}"
    )

    # ── DQ Flag History ───────────────────────────────────────────────────────
    # Show every pipeline DQ flag for this document, grouped by resolved / open.
    # Resolved flags also show the corrected value entered by the DC (from edit_log).
    # This is the "author field" and similar corrections made visible on the detail page.
    st.markdown("---")
    st.markdown("### Data Quality Flag History")
    st.caption(
        "DQ flags raised by the pipeline at ingest time for this document, "
        "and DC corrections applied via the Remediation Queue."
    )

    # Load DQ flags and filter to this document
    doc_dq = pd.DataFrame()
    if DQ_FLAGS_CSV.exists():
        _dq_all = pd.read_csv(DQ_FLAGS_CSV)
        _dq_all["resolved"] = _dq_all["resolved"].astype(str).str.lower() == "true"
        doc_dq = _dq_all[_dq_all["mdr_id"] == selected].copy()

    # Load edit log corrections for this document (DQ_REMEDIATION events)
    dc_corrections = {}  # flag_id -> {"corrected_value": ..., "resolved_by": ..., "resolved_at": ...}
    if EDIT_LOG_CSV.exists():
        _elog = pd.read_csv(EDIT_LOG_CSV)
        _dc_events = _elog[
            (_elog["mdr_id"] == selected)
            & _elog["field"].str.startswith("dq_flag_resolved:", na=False)
        ].copy()
        for _, ev in _dc_events.iterrows():
            fid = str(ev["field"]).replace("dq_flag_resolved:", "")
            dc_corrections[fid] = {
                "corrected_value": str(ev.get("new_value", "")),
                "resolved_by":     str(ev.get("username", "")),
                "resolved_at":     str(ev.get("timestamp", ""))[:19],
            }

    if doc_dq.empty:
        st.info("No DQ flags raised for this document by the pipeline.")
    else:
        resolved_dq   = doc_dq[doc_dq["resolved"]]
        unresolved_dq = doc_dq[~doc_dq["resolved"]]

        if not resolved_dq.empty:
            st.markdown(
                f'<div style="font-size:0.75rem; color:#22c55e; margin:0.25rem 0 0.5rem 0;">'
                f'<b>{len(resolved_dq)}</b> flag(s) resolved</div>',
                unsafe_allow_html=True,
            )
            for _, dqrow in resolved_dq.iterrows():
                fid    = str(dqrow["flag_id"])
                field  = str(dqrow.get("field_name", "—"))
                ftype  = str(dqrow.get("flag_type", ""))
                orig   = str(dqrow.get("original_value", ""))
                sugg   = str(dqrow.get("suggested_value", ""))
                corr   = dc_corrections.get(fid, {})
                corrected_val = corr.get("corrected_value", sugg or "see audit trail")
                resolved_by   = corr.get("resolved_by",  str(dqrow.get("resolved_by", "")))
                resolved_at   = corr.get("resolved_at",  str(dqrow.get("resolved_at", ""))[:19])
                st.markdown(
                    f'<div style="background:#1a2b1a; border:1px solid #22c55e33; border-left:3px solid #22c55e; '
                    f'border-radius:4px; padding:0.6rem 1rem; margin-bottom:0.4rem; font-size:0.8rem;">'
                    f'<b style="color:#22c55e;">{fid}</b> &nbsp; <span style="color:#7a8499;">{ftype}</span>'
                    f'<br><span style="color:#e8ecf4;">Field: <b>{field}</b></span>'
                    + (f'&nbsp;&nbsp; Original: <code>{orig}</code>' if orig else '')
                    + f'&nbsp;&nbsp; <b>Corrected value: <span style="color:#22c55e;">{corrected_val}</span></b>'
                    + f'<br><span style="color:#4a5568;">Resolved by {resolved_by} at {resolved_at}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        if not unresolved_dq.empty:
            st.markdown(
                f'<div style="font-size:0.75rem; color:#ef4444; margin:0.5rem 0 0.5rem 0;">'
                f'<b>{len(unresolved_dq)}</b> open flag(s) — pending DC action</div>',
                unsafe_allow_html=True,
            )
            for _, dqrow in unresolved_dq.iterrows():
                fid   = str(dqrow["flag_id"])
                field = str(dqrow.get("field_name", "—"))
                ftype = str(dqrow.get("flag_type", ""))
                orig  = str(dqrow.get("original_value", ""))
                sugg  = str(dqrow.get("suggested_value", ""))
                det   = str(dqrow.get("flag_detail", ""))
                st.markdown(
                    f'<div style="background:#2b1a1a; border:1px solid #ef444433; border-left:3px solid #ef4444; '
                    f'border-radius:4px; padding:0.6rem 1rem; margin-bottom:0.4rem; font-size:0.8rem;">'
                    f'<b style="color:#ef4444;">{fid}</b> &nbsp; <span style="color:#7a8499;">{ftype}</span>'
                    f'<br><span style="color:#e8ecf4;">Field: <b>{field}</b></span>'
                    + (f'&nbsp;&nbsp; Original: <code>{orig}</code>' if orig else '')
                    + (f'&nbsp;&nbsp; Suggested: <code>{sugg}</code>' if sugg else '')
                    + f'<br><span style="color:#4a5568;">{det[:120]}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            st.caption(
                "Go to Source System Health > DC Remediation Queue to resolve these flags. "
                "In a production CDE, resolving a flag triggers a write-back to the source "
                "system and a pipeline re-ingest — the corrected value would then appear "
                "in the Resolved section above automatically."
            )

    # ── Dashboard Edit History ────────────────────────────────────────────────
    # All PM_UPDATE and DQ_REMEDIATION events logged via the dashboard for this document.
    # Separated from the STAGED timeline (pipeline events only) to maintain clean lineage.
    st.markdown("---")
    st.markdown("### Dashboard Edit History")
    st.caption(
        "All changes made to this document through the dashboard (Project Manager priority/schedule "
        "edits and Document Controller DQ flag resolutions). Separate from the pipeline lifecycle "
        "timeline above — these are human actions, not ingestion events."
    )

    if not EDIT_LOG_CSV.exists():
        st.info("No dashboard edits recorded for this document yet.")
    else:
        _elog_all = pd.read_csv(EDIT_LOG_CSV)
        doc_edits = _elog_all[_elog_all["mdr_id"] == selected].copy()
        if doc_edits.empty:
            st.info("No dashboard edits recorded for this document yet.")
        else:
            doc_edits = doc_edits.sort_values("timestamp", ascending=False).reset_index(drop=True)

            # Friendly field label — strip the "dq_flag_resolved:" prefix for display
            def _friendly_field(f):
                if str(f).startswith("dq_flag_resolved:"):
                    return "DC flag resolved: " + str(f).replace("dq_flag_resolved:", "")
                return str(f)

            doc_edits["event_type"] = doc_edits["field"].apply(
                lambda f: "DC Remediation" if str(f).startswith("dq_flag_resolved:") else "PM Update"
            )
            doc_edits["field_display"] = doc_edits["field"].apply(_friendly_field)

            st.dataframe(
                doc_edits[["timestamp", "username", "event_type", "field_display", "old_value", "new_value"]],
                column_config={
                    "timestamp":     st.column_config.TextColumn("When",        width="medium"),
                    "username":      st.column_config.TextColumn("Who",         width="small"),
                    "event_type":    st.column_config.TextColumn("Type",        width="small"),
                    "field_display": st.column_config.TextColumn("Field / Flag",width="medium"),
                    "old_value":     st.column_config.TextColumn("Old Value",   width="medium"),
                    "new_value":     st.column_config.TextColumn("New Value",   width="medium"),
                },
                hide_index=True,
                use_container_width=True,
            )


def page_audit_trail(current_user: str):
    """Unified Audit Trail page.

    Combines three event streams into one filterable log:

    1. **Pipeline — Automated**: rows from staged_transformation_log.csv. Each row
       is a field-level normalisation that happened at pipeline run time (e.g.
       "I&C" -> "Instrumentation"). Actor = "pipeline".

    2. **PM Update**: rows from edit_log.csv where the field column is NOT prefixed
       "dq_flag_resolved:". These are Project Manager edits (priority, critical path,
       % complete, notes). Actor = the username from the row.

    3. **DC Resolution**: rows from edit_log.csv where field starts with
       "dq_flag_resolved:". These are Document Controller flag resolutions.
       If the new_value matches the pipeline's suggested_value for a normalisation
       flag, the event is classified "Pipeline — Confirmed by Human". Otherwise it
       is "Human — DC Resolution".

    Filter controls: actor type, specific actor, document (mdr_id).

    Args:
        current_user: Currently active mock user (not used for filtering by default;
                      shown in the header so the user knows whose session this is).
    """
    st.markdown('<div class="page-title">Audit Trail</div>', unsafe_allow_html=True)
    st.caption(
        "All events that have modified or confirmed data in this project: "
        "pipeline transformations, PM edits, and DC flag resolutions."
    )

    # ── Load event sources ─────────────────────────────────────────────────────

    # --- Source 1: pipeline transformation log (staged_transformation_log.csv) ---
    # Each row = one field normalisation applied at pipeline ingest time.
    # We do not have a real timestamp so we mark these as "pipeline run" events.
    pipeline_rows = []
    if TRANSFORMATION_LOG_CSV.exists():
        _log("LOAD", f"Audit Trail: reading transformation log from {TRANSFORMATION_LOG_CSV}")
        tlog = pd.read_csv(TRANSFORMATION_LOG_CSV)
        # Convert all string cols from StringDtype (pandas 3.0) to object
        str_cols = tlog.select_dtypes(include="string").columns.tolist()
        if str_cols:
            tlog[str_cols] = tlog[str_cols].astype(object)
        for _, r in tlog.iterrows():
            pipeline_rows.append({
                "timestamp":  pd.NaT,             # pipeline events have no wall-clock ts
                "actor":      "pipeline",
                "actor_type": "Pipeline -- Automated",
                "mdr_id":     str(r.get("mdr_id", "")),
                "event_type": "Pipeline Transform",
                "field":      str(r.get("field_name", "")),
                "from_val":   str(r.get("original_value", "")),
                "to_val":     str(r.get("normalised_value", "")),
                "note":       str(r.get("normalisation_rule", "")),
            })
    else:
        _log("ERROR", f"Audit Trail: transformation log not found at {TRANSFORMATION_LOG_CSV}")
        st.warning(
            "staged_transformation_log.csv not found. "
            "Run data_generation/generate_staged_layer.py to populate it."
        )

    # --- Source 2 + 3: edit_log.csv (PM edits and DC resolutions) ---

    # Build a flag_id -> field_name lookup from staged_dq_flags.csv so that
    # DC resolution events in the audit trail show the actual field that was
    # corrected (e.g. "author") rather than just the opaque flag ID ("DQF-0001").
    _flag_field_map: dict[str, str] = {}
    if DQ_FLAGS_CSV.exists():
        try:
            _dq_for_audit = pd.read_csv(DQ_FLAGS_CSV)
            _flag_field_map = dict(
                zip(
                    _dq_for_audit["flag_id"].astype(str),
                    _dq_for_audit["field_name"].astype(str),
                )
            )
        except Exception:
            pass  # non-fatal; flag IDs will still be shown as-is

    edit_rows = []
    if EDIT_LOG_CSV.exists():
        _log("LOAD", f"Audit Trail: reading edit log from {EDIT_LOG_CSV}")
        elog = pd.read_csv(EDIT_LOG_CSV)
        str_cols = elog.select_dtypes(include="string").columns.tolist()
        if str_cols:
            elog[str_cols] = elog[str_cols].astype(object)

        for _, r in elog.iterrows():
            field_val = str(r.get("field", ""))
            actor     = str(r.get("username", ""))
            ts_raw    = r.get("timestamp")
            try:
                ts = pd.to_datetime(ts_raw, utc=True)
            except Exception:
                ts = pd.NaT

            if field_val.startswith("dq_flag_resolved:"):
                # DC resolution — classify as "confirmed by pipeline" if the new_value
                # matches the suggested normalised value (i.e. DC confirmed the mapping
                # rather than supplying a freetext correction).
                flag_id  = field_val.split(":", 1)[1]
                new_val  = str(r.get("new_value", ""))
                old_val  = str(r.get("old_value", ""))
                # Show "field_name (flag_id)" so the user can see WHAT was corrected
                # (e.g. "author (DQF-0001)") without needing to cross-reference the flag table.
                field_display = _flag_field_map.get(flag_id, flag_id)
                if field_display != flag_id:
                    field_display = f"{field_display} ({flag_id})"

                # Check whether this resolution confirmed a pipeline suggestion by
                # looking up the flag in the transformation log rows we already have.
                # A pipeline-suggested value is one where the pipeline produced a
                # normalised_value and the DC wrote exactly that value.
                is_pipeline_confirmed = any(
                    pr["to_val"] == new_val and pr["mdr_id"] == str(r.get("mdr_id", ""))
                    for pr in pipeline_rows
                )
                actor_type = (
                    "Pipeline -- Confirmed by Human"
                    if is_pipeline_confirmed
                    else "Human -- DC Resolution"
                )
                edit_rows.append({
                    "timestamp":  ts,
                    "actor":      actor,
                    "actor_type": actor_type,
                    "mdr_id":     str(r.get("mdr_id", "")),
                    "event_type": "DQ Resolution",
                    "field":      field_display,   # e.g. "author (DQF-0001)"
                    "from_val":   old_val,
                    "to_val":     new_val,
                    "note":       "",
                })
            else:
                # PM edit — priority, critical path, % complete, notes, etc.
                edit_rows.append({
                    "timestamp":  ts,
                    "actor":      actor,
                    "actor_type": "Human -- PM Edit",
                    "mdr_id":     str(r.get("mdr_id", "")),
                    "event_type": "PM Edit",
                    "field":      field_val,
                    "from_val":   str(r.get("old_value", "")),
                    "to_val":     str(r.get("new_value", "")),
                    "note":       "",
                })
    else:
        _log("ERROR", f"Audit Trail: edit log not found at {EDIT_LOG_CSV}")
        st.warning("No edit_log.csv found — no PM edits or DC resolutions to display.")

    # ── Build combined DataFrame ───────────────────────────────────────────────
    all_events = pipeline_rows + edit_rows
    if not all_events:
        st.info("No audit events found.")
        return

    audit = pd.DataFrame(all_events)

    # Sort: timestamped events first (newest first), then pipeline events (no ts) last
    has_ts  = audit["timestamp"].notna()
    ts_part = audit[has_ts].sort_values("timestamp", ascending=False)
    no_ts   = audit[~has_ts]
    audit   = pd.concat([ts_part, no_ts], ignore_index=True)

    _log("LOAD", f"Audit Trail: {len(audit)} total events ({len(pipeline_rows)} pipeline, {len(edit_rows)} human)")

    # ── Filter controls ────────────────────────────────────────────────────────
    st.markdown("---")
    f1, f2, f3 = st.columns(3)

    # Actor type filter — covers the four categories defined above
    all_actor_types = sorted(audit["actor_type"].unique().tolist())
    with f1:
        sel_type = st.selectbox(
            "Actor type",
            ["All"] + all_actor_types,
            key="aud_actor_type",
        )

    # Specific actor filter — "pipeline" plus all real usernames
    all_actors = sorted(audit["actor"].unique().tolist())
    with f2:
        sel_actor = st.selectbox(
            "Specific actor",
            ["All"] + all_actors,
            key="aud_actor",
        )

    # Document filter — mdr_id dropdown (show only docs that have events)
    all_docs = sorted(audit["mdr_id"].unique().tolist())
    with f3:
        sel_doc = st.selectbox(
            "Document (MDR ID)",
            ["All"] + all_docs,
            key="aud_doc",
        )

    # Apply filters
    filtered = audit.copy()
    if sel_type != "All":
        filtered = filtered[filtered["actor_type"] == sel_type]
    if sel_actor != "All":
        filtered = filtered[filtered["actor"] == sel_actor]
    if sel_doc != "All":
        filtered = filtered[filtered["mdr_id"] == sel_doc]

    _log("FILTER", f"Audit Trail: {len(filtered)} of {len(audit)} events after filter")

    st.markdown(f"**{len(filtered)}** events")
    st.markdown("")

    # ── Display table ──────────────────────────────────────────────────────────
    # Format timestamp for display — strip microseconds, show as date+time
    def fmt_audit_ts(val):
        """Render a Timestamp as 'YYYY-MM-DD HH:MM', or 'pipeline run' if NaT."""
        if pd.isna(val):
            return "pipeline run"
        try:
            return str(val)[:16]   # '2026-05-11 16:28'
        except Exception:
            return str(val)

    display = pd.DataFrame({
        "When":       filtered["timestamp"].map(fmt_audit_ts),
        "Actor":      filtered["actor"],
        "Actor Type": filtered["actor_type"],
        "Document":   filtered["mdr_id"],
        "Event":      filtered["event_type"],
        "Field":      filtered["field"],
        "From":       filtered["from_val"],
        "To":         filtered["to_val"],
        "Note":       filtered["note"],
    })

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "When":       st.column_config.TextColumn("When",        width="medium"),
            "Actor":      st.column_config.TextColumn("Actor",       width="medium"),
            "Actor Type": st.column_config.TextColumn("Actor Type",  width="large"),
            "Document":   st.column_config.TextColumn("Document",    width="large"),
            "Event":      st.column_config.TextColumn("Event",       width="medium"),
            "Field":      st.column_config.TextColumn("Field",       width="medium"),
            "From":       st.column_config.TextColumn("From",        width="medium"),
            "To":         st.column_config.TextColumn("To",          width="medium"),
            "Note":       st.column_config.TextColumn("Note",        width="large"),
        },
    )

    # ── Navigate to Document Detail for the filtered document ────────────────
    # Only show this when the user has filtered to a single document; it makes
    # no sense when "All" documents are shown in the table.
    if sel_doc != "All":
        if st.button(
            f"Open {sel_doc} in Document Detail ->",
            key="aud_trail_open_detail",
            type="secondary",
        ):
            st.session_state["_nav_request"]    = "Document Detail"
            st.session_state["_detail_request"] = sel_doc
            st.rerun()

    # ── Legend ─────────────────────────────────────────────────────────────────
    with st.expander("Actor type legend", expanded=False):
        st.markdown(
            "**Pipeline -- Automated**: normalisation applied automatically at pipeline "
            "ingest time (e.g. discipline code expansion, status vocabulary mapping). "
            "No human involved.\n\n"
            "**Pipeline -- Confirmed by Human**: a DC saw the pipeline's suggested "
            "mapping and pressed 'Confirm mapping' — they did not change the value, "
            "they confirmed it. Counts as human sign-off on the pipeline's decision.\n\n"
            "**Human -- DC Resolution**: a DC supplied a value the pipeline could not "
            "determine automatically (e.g. filled a missing author).\n\n"
            "**Human -- PM Edit**: a Project Manager changed a planning field "
            "(priority, critical path, % complete, or notes)."
        )


def page_source_health(df: pd.DataFrame, current_user: str, active_role: str):
    """Source System Health page.

    Shows per-source RAG status, DQ flag counts, and a Document Controller
    remediation queue for unresolved flags. DCs can tick 'resolved' to mark
    a flag as fixed, which writes resolved_by and resolved_at to the CSV and
    logs a DQ_REMEDIATION event to the audit trail.

    Args:
        df:           Full MDR DataFrame — used for record counts per source.
        current_user: Currently active mock user (for audit logging).
        active_role:  "Read Only", "Project Manager", or "Document Controller".
    """
    st.markdown('<div class="page-title">Source System Health — Pipeline Ingestion Status</div>', unsafe_allow_html=True)

    # ── Load DQ flags ──────────────────────────────────────────────────────────
    if not DQ_FLAGS_CSV.exists():
        _log("ERROR", f"DQ flags CSV not found at {DQ_FLAGS_CSV}")
        st.error(
            "DQ flags file not found. Run data_generation/generate_staged_layer.py first "
            "to populate staged_dq_flags.csv."
        )
        return

    _log("LOAD", f"Reading DQ flags from {DQ_FLAGS_CSV}")
    dq_raw = pd.read_csv(DQ_FLAGS_CSV)

    # The 'resolved' column is stored as the string "True"/"False" in CSV.
    # We normalise it to a Python bool so comparisons and filtering work correctly.
    dq_raw["resolved"] = dq_raw["resolved"].astype(str).str.lower() == "true"

    # resolved_by / resolved_at / resolved_note are all-null until a DC resolves a flag.
    # pandas reads all-null columns as float64; casting to object lets us assign
    # string values later without a TypeError.
    for _col in ("resolved_by", "resolved_at", "resolved_note"):
        if _col in dq_raw.columns:
            dq_raw[_col] = dq_raw[_col].astype(object)

    n_total    = len(dq_raw)
    n_resolved = int(dq_raw["resolved"].sum())
    _log("LOAD", f"DQ flags loaded — {n_total} total, {n_resolved} resolved, {n_total - n_resolved} open")

    # ── Section 1: Per-source summary tiles ───────────────────────────────────
    st.markdown('<div class="section-header">Source System Overview</div>', unsafe_allow_html=True)

    # Precompute filter masks once so groupby calls below don't repeat the boolean ops
    unresolved_dq = dq_raw[~dq_raw["resolved"]]
    missing_dq    = unresolved_dq[unresolved_dq["flag_type"] == "MISSING_MANDATORY_FIELD"]

    summary = pd.concat([
        df.groupby("source_system").size().rename("records"),
        dq_raw.groupby("source_system").size().rename("total_flags"),
        unresolved_dq.groupby("source_system").size().rename("unresolved"),
        missing_dq.groupby("source_system").size().rename("missing_fields"),
    ], axis=1).fillna(0).astype(int).reset_index()
    summary.rename(columns={"source_system": "source"}, inplace=True)

    # RAG logic per source:
    #   RED   = unresolved MISSING_MANDATORY_FIELD flags (ISO 19650 breach)
    #   AMBER = other unresolved flags (normalisation / format issues)
    #   GREEN = all flags resolved (or no flags at all)
    def _rag(row):
        if row["missing_fields"] > 0:
            return "RED"
        if row["unresolved"] > 0:
            return "AMBER"
        return "GREEN"

    summary["rag"] = summary.apply(_rag, axis=1)

    # Display one tile per source system, styled to match the rest of the dashboard
    tile_cols = st.columns(len(summary))
    for i, row in summary.iterrows():
        color = RAG_COLOR_MAP[row["rag"]]
        with tile_cols[i]:
            st.markdown(
                f"""
                <div style="background:#1e2333; border:1px solid #2a3040;
                            border-top:3px solid {color}; border-radius:4px;
                            padding:1rem 1.25rem; margin-bottom:0.75rem;">
                  <div style="font-family:'IBM Plex Mono',monospace; font-size:1.6rem;
                              font-weight:600; color:{color}; margin-bottom:0.25rem;">
                    {row['rag']}
                  </div>
                  <div style="font-size:0.9rem; font-weight:600; color:#e8ecf4;
                              margin-bottom:0.5rem;">
                    {row['source']}
                  </div>
                  <div style="font-size:0.72rem; color:#7a8499;
                              font-family:'IBM Plex Mono',monospace; line-height:1.8;">
                    Records ingested: {row['records']}<br>
                    Total DQ flags: {row['total_flags']}<br>
                    Unresolved flags: {row['unresolved']}<br>
                    Missing mandatory fields: {row['missing_fields']}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # ── Tabs: Remediation Queue | Pipeline Run Report | Resolution Audit Trail ─
    tab_queue, tab_report, tab_audit = st.tabs([
        "DC Remediation Queue", "Pipeline Run Report", "Resolution Audit Trail"
    ])

    # ────────────────────────────────────────────────────────────────────────
    # TAB 1 — DC Remediation Queue
    # ────────────────────────────────────────────────────────────────────────
    with tab_queue:
        st.markdown('<div class="section-header">Remediation Queue — Unresolved Flags</div>', unsafe_allow_html=True)

        # Role notice — only DCs can resolve flags
        is_dc = (active_role == ROLE_DOCUMENT_CONTROLLER)
        if is_dc:
            st.success(f"{ROLE_DOCUMENT_CONTROLLER} — expand a flag below to fill in the corrected value and confirm resolution.")
        else:
            st.info(f"Read Only — switch to {ROLE_DOCUMENT_CONTROLLER} in the sidebar to resolve DQ flags.")

        # ── Filter controls ───────────────────────────────────────────────────────
        fc1, fc2, fc3 = st.columns([2, 3, 2])
        src_options  = ["All"] + sorted(dq_raw["source_system"].dropna().unique().tolist())
        type_options = ["All"] + sorted(dq_raw["flag_type"].dropna().unique().tolist())
        sel_src       = fc1.selectbox("Source system", src_options,  key="health_src_filter")
        sel_type      = fc2.selectbox("Flag type",     type_options, key="health_type_filter")
        show_resolved = fc3.checkbox(
            "Show resolved flags",
            key="health_show_resolved",
            help="Include flags that have already been resolved by a Document Controller.",
        )

        # By default show only unresolved flags; toggle includes resolved ones too.
        queue = dq_raw.copy() if show_resolved else dq_raw[~dq_raw["resolved"]].copy()
        if sel_src  != "All":
            queue = queue[queue["source_system"] == sel_src]
        if sel_type != "All":
            queue = queue[queue["flag_type"] == sel_type]

        _log("FILTER", f"Health queue — src={sel_src} type={sel_type} -> {len(queue)} flags")

        n_unresolved = int((~queue["resolved"]).sum())
        n_shown      = len(queue)
        st.markdown(
            f'<div style="font-size:0.75rem; color:#7a8499; margin:0.25rem 0 0.5rem 0;">'
            f'Showing <b>{n_shown}</b> flag(s)'
            f' — <b style="color:#ef4444;">{n_unresolved} unresolved</b>'
            f', <b style="color:#22c55e;">{n_shown - n_unresolved} resolved</b>'
            f'</div>',
            unsafe_allow_html=True,
        )

        if queue.empty:
            st.success("No flags for the selected filters — all clear.")
        else:
            # ── Per-flag resolution cards ─────────────────────────────────────────────
            # Each flag is an expander.  DCs see a value input + Confirm button inside.
            # In a real system the confirmed value would update the source system record;
            # here it is captured in the audit log (edit_log.csv) and the flag is closed.
            for _, row in queue.iterrows():
                flag_id    = str(row["flag_id"])
                ftype      = str(row.get("flag_type",      ""))
                field      = str(row.get("field_name",     "—"))
                orig       = str(row.get("original_value", ""))
                sugg       = str(row.get("suggested_value",""))
                detail     = str(row.get("flag_detail",    ""))
                mdr_id     = str(row.get("mdr_id",         ""))
                src_sys    = str(row.get("source_system",  ""))
                is_resolved = bool(row.get("resolved", False))

                # Visual marker so resolved flags are distinguishable at a glance
                status_tag = " [RESOLVED]" if is_resolved else ""
                label = f"{flag_id}{status_tag}  |  {src_sys}  |  {ftype}  |  field: {field}"
                with st.expander(label, expanded=False):
                    # Flag metadata
                    m1, m2, m3 = st.columns(3)
                    m1.markdown(f"**Document:** `{mdr_id}`")
                    m2.markdown(f"**Field:** `{field}`")
                    m3.markdown(f"**Source system:** {src_sys}")

                    if orig:
                        st.markdown(f"**Original value:** `{orig}`")
                    if sugg:
                        st.markdown(f"**Suggested correction:** `{sugg}`")
                    if detail:
                        st.caption(detail)

                    # ── Show resolution summary if the flag is already resolved ────────
                    if is_resolved:
                        resolved_by = str(row.get("resolved_by", ""))
                        resolved_at = str(row.get("resolved_at", ""))
                        st.success(
                            f"Resolved by **{resolved_by}** at {resolved_at[:19]}  \n"
                            f"See Resolution Audit Trail tab for the corrected value."
                        )
                        # Navigation link to Document Detail for this document
                        if st.button(
                            f"View {mdr_id} in Document Detail ->",
                            key=f"btn_detail_{flag_id}",
                        ):
                            st.session_state["_nav_request"]    = "Document Detail"
                            st.session_state["_detail_request"] = mdr_id
                            st.rerun()

                    elif is_dc:
                        # ── Resolution UX — varies by flag type ───────────────────────────
                        #
                        # NORMALISATION_REQUIRED with a suggested value:
                        #   The pipeline already knows the correct canonical value — the DC
                        #   only needs to confirm it.  A single button is clearer than a text
                        #   input (and avoids audit trail entries like old=I&C, new=confirmed).
                        #
                        # Everything else (MISSING_MANDATORY_FIELD, DUPLICATE_CANDIDATE, or
                        # NORMALISATION_REQUIRED without a suggested value):
                        #   The DC must supply the actual corrected value, so a text input
                        #   is required.

                        is_norm_with_suggestion = (
                            ftype == "NORMALISATION_REQUIRED" and bool(sugg.strip())
                        )

                        if is_norm_with_suggestion:
                            # One-click confirm — writes the suggested canonical value, not
                            # a free-text note, so the audit trail reads correctly:
                            # old = original source value, new = canonical mapped value.
                            st.info(
                                f"Pipeline mapping: `{orig}` → `{sugg}`  \n"
                                f"This mapping was applied automatically with HIGH confidence. "
                                f"Confirm to close the flag and record your sign-off."
                            )
                            if st.button(
                                f"Confirm mapping: {orig} -> {sugg}",
                                key=f"btn_resolve_{flag_id}",
                                type="primary",
                            ):
                                now_str = str(datetime.now(timezone.utc))
                                mask = dq_raw["flag_id"] == flag_id
                                dq_raw.loc[mask, "resolved"]       = True
                                dq_raw.loc[mask, "resolved_by"]    = current_user
                                dq_raw.loc[mask, "resolved_at"]    = now_str
                                dq_raw.loc[mask, "resolved_value"] = sugg
                                # Write the canonical suggested value — not "confirmed" —
                                # so the audit trail records the actual mapping applied.
                                log_edit(
                                    current_user, mdr_id,
                                    f"dq_flag_resolved:{flag_id}",
                                    orig if orig else "MISSING",
                                    sugg,
                                )
                                _log("EDIT", f"DC {current_user} confirmed normalisation {flag_id} on {mdr_id}: '{orig}' -> '{sugg}'")
                                save_dq_flags(dq_raw)
                                st.toast(f"{flag_id} confirmed and resolved.")
                                st.rerun()
                        else:
                            # DC must supply the corrected value (missing field, duplicate, or
                            # normalisation without a suggested canonical value).
                            placeholder = (
                                f"Enter corrected {field} value..."
                                if ftype == "MISSING_MANDATORY_FIELD"
                                else "Describe which record was kept, or enter the correct value..."
                            )
                            resolution_val = st.text_input(
                                "Resolution value / note",
                                key=f"resolve_val_{flag_id}",
                                placeholder=placeholder,
                                help=(
                                    "For missing fields: enter the actual value (e.g. the author name). "
                                    "For duplicates: describe which record was kept and why. "
                                    "This becomes the corrected value in the audit trail."
                                ),
                            )
                            if st.button("Confirm Resolved", key=f"btn_resolve_{flag_id}", type="primary"):
                                if resolution_val.strip():
                                    now_str = str(datetime.now(timezone.utc))
                                    mask = dq_raw["flag_id"] == flag_id
                                    dq_raw.loc[mask, "resolved"]       = True
                                    dq_raw.loc[mask, "resolved_by"]    = current_user
                                    dq_raw.loc[mask, "resolved_at"]    = now_str
                                    dq_raw.loc[mask, "resolved_value"] = resolution_val.strip()
                                    log_edit(
                                        current_user, mdr_id,
                                        f"dq_flag_resolved:{flag_id}",
                                        orig if orig else "MISSING",
                                        resolution_val.strip(),
                                    )
                                    _log("EDIT", f"DC {current_user} resolved {flag_id} on {mdr_id} -> '{resolution_val.strip()}'")
                                    save_dq_flags(dq_raw)
                                    st.toast(f"{flag_id} marked as resolved.")
                                    st.rerun()
                                else:
                                    st.warning("Enter a resolution value before confirming. In a real system this becomes the corrected field value in the source record.")

                        # Navigation link to Document Detail for this document
                        # (shown for unresolved flags so the DC can review the full
                        # document context before deciding how to resolve the flag).
                        if st.button(
                            f"View {mdr_id} in Document Detail ->",
                            key=f"btn_detail_unres_{flag_id}",
                            type="secondary",
                        ):
                            st.session_state["_nav_request"]    = "Document Detail"
                            st.session_state["_detail_request"] = mdr_id
                            st.rerun()


    # ────────────────────────────────────────────────────────────────────────
    # TAB 2 — Pipeline Run Report (transformation log)
    # ────────────────────────────────────────────────────────────────────────
    with tab_report:
        st.markdown('<div class="section-header">Pipeline Run Report — Automated Transformations</div>', unsafe_allow_html=True)

        # Explanation of what this tab shows and why it matters
        with st.expander("What is this?", expanded=False):
            st.markdown("""
**Automated transformations vs. DQ flags** — two distinct outcomes of the pipeline:

| Outcome | Meaning | Who acts? |
|---|---|---|
| **DQ Flag** (Remediation Queue tab) | The pipeline detected a data quality issue it could not safely resolve — a missing mandatory field, a non-standard value it couldn't map with confidence, or a format it couldn't parse. **Human action required.** | Document Controller |
| **Transformation** (this tab) | The pipeline applied a deterministic, rule-based conversion with HIGH confidence — date format normalisation, vocabulary mapping, revision format conversion. **No action required — for information only.** | Nobody (automated) |

**Confidence levels:**

| Level | Meaning | Example |
|---|---|---|
| **HIGH** | Unambiguous, deterministic rule. No DQ flag raised. | `MECH` → `Mechanical` (standard code), `01/15/2025` → `2025-01-15` (known format) |
| **MEDIUM** | Plausible mapping via extended lookup, but the source used a non-standard form. A DQ flag is ALSO raised so a DC can confirm. | `MECHANICAL` → `Mechanical` (full word instead of code), `I&C` → `Instrumentation` (vendor notation) |
| **LOW** *(reserved)* | Fuzzy or inferred match with low certainty. Would be used for typos/near-matches (e.g. `MEXLXCL` ≈ `Mechanical`). Currently not produced — that level of ambiguity goes straight to a DQ flag with no suggested value. | — |

Any code the pipeline cannot map at all (not in any lookup table, no close match) produces a DQ flag with no suggested value, and NO transformation record is written — the field is left as-is for the DC to correct manually.

This distinction is the audit trail: every silent transformation is recorded here so nothing the pipeline does is invisible.
            """)

        if not TRANSFORMATION_LOG_CSV.exists():
            st.warning(
                "staged_transformation_log.csv not found. "
                "Re-run data_generation/generate_staged_layer.py to generate it."
            )
        else:
            _log("LOAD", f"Reading transformation log from {TRANSFORMATION_LOG_CSV}")
            tx = pd.read_csv(TRANSFORMATION_LOG_CSV)
            _log("LOAD", f"Transformation log loaded — {len(tx)} records")

            # ── Summary metrics ───────────────────────────────────────────────
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Total transformations", len(tx))
            m2.metric("Source systems", tx["source_system"].nunique())
            m3.metric("Fields transformed", tx["field_name"].nunique())
            m4.metric("Rules applied", tx["normalisation_rule"].nunique())
            if "confidence" in tx.columns:
                n_med = int((tx["confidence"] == "MEDIUM").sum())
                m5.metric("MEDIUM confidence", n_med,
                          help="Transformations the pipeline applied but flagged for DC confirmation.")

            # ── Breakdown charts ──────────────────────────────────────────────
            bc1, bc2 = st.columns(2)

            with bc1:
                st.markdown(
                    '<div style="font-size:0.78rem; font-weight:600; color:#7a8499; '
                    'margin:0.75rem 0 0.4rem 0;">Transformations by Rule</div>',
                    unsafe_allow_html=True,
                )
                by_rule = (
                    tx.groupby("normalisation_rule").size()
                    .reset_index(name="count")
                    .sort_values("count", ascending=False)
                )
                st.dataframe(
                    by_rule,
                    column_config={
                        "normalisation_rule": st.column_config.TextColumn("Rule"),
                        "count":              st.column_config.NumberColumn("Count", format="%d"),
                    },
                    hide_index=True,
                    width="stretch",
                )

            with bc2:
                st.markdown(
                    '<div style="font-size:0.78rem; font-weight:600; color:#7a8499; '
                    'margin:0.75rem 0 0.4rem 0;">Transformations by Source System</div>',
                    unsafe_allow_html=True,
                )
                by_src = (
                    tx.groupby("source_system").size()
                    .reset_index(name="count")
                    .sort_values("count", ascending=False)
                )
                st.dataframe(
                    by_src,
                    column_config={
                        "source_system": st.column_config.TextColumn("Source"),
                        "count":         st.column_config.NumberColumn("Count", format="%d"),
                    },
                    hide_index=True,
                    width="stretch",
                )

            # ── Full transformation log with filters ──────────────────────────
            st.markdown('<div class="section-header">Full Transformation Log</div>', unsafe_allow_html=True)

            tf1, tf2, tf3, tf4 = st.columns([2, 3, 3, 3])
            tx_src_opts  = ["All"] + sorted(tx["source_system"].dropna().unique().tolist())
            tx_rule_opts = ["All"] + sorted(tx["normalisation_rule"].dropna().unique().tolist())
            tx_fld_opts  = ["All"] + sorted(tx["field_name"].dropna().unique().tolist())
            tx_mdr_opts  = ["All"] + sorted(tx["mdr_id"].dropna().unique().tolist()) if "mdr_id" in tx.columns else ["All"]
            sel_tx_src  = tf1.selectbox("Source system", tx_src_opts,  key="tx_src_filter")
            sel_tx_rule = tf2.selectbox("Rule",          tx_rule_opts, key="tx_rule_filter")
            sel_tx_fld  = tf3.selectbox("Field",         tx_fld_opts,  key="tx_fld_filter")
            sel_tx_mdr  = tf4.selectbox("Document (MDR ID)", tx_mdr_opts, key="tx_mdr_filter")

            tx_view = tx.copy()
            if sel_tx_src  != "All":
                tx_view = tx_view[tx_view["source_system"] == sel_tx_src]
            if sel_tx_rule != "All":
                tx_view = tx_view[tx_view["normalisation_rule"] == sel_tx_rule]
            if sel_tx_fld  != "All":
                tx_view = tx_view[tx_view["field_name"] == sel_tx_fld]
            if sel_tx_mdr  != "All" and "mdr_id" in tx_view.columns:
                tx_view = tx_view[tx_view["mdr_id"] == sel_tx_mdr]

            _log("FILTER", f"Transform log — src={sel_tx_src} rule={sel_tx_rule} field={sel_tx_fld} mdr={sel_tx_mdr} -> {len(tx_view)} rows")

            st.markdown(
                f'<div style="font-size:0.75rem; color:#7a8499; margin:0.25rem 0 0.5rem 0;">'
                f'Showing <b>{len(tx_view)}</b> of {len(tx)} transformation records</div>',
                unsafe_allow_html=True,
            )

            # Show run_timestamp if the column exists (added by the pipeline after
            # a fresh generate_staged_layer.py run; may be absent in older files).
            tx_display_cols = [c for c in [
                "run_timestamp", "source_system", "source_native_id", "mdr_id",
                "field_name", "original_value", "normalised_value",
                "normalisation_rule", "confidence",
            ] if c in tx_view.columns]

            st.dataframe(
                tx_view[tx_display_cols].reset_index(drop=True),
                column_config={
                    "run_timestamp":      st.column_config.TextColumn("Pipeline Run",width="medium"),
                    "source_system":      st.column_config.TextColumn("Source",      width="small"),
                    "source_native_id":   st.column_config.TextColumn("Source ID",   width="small"),
                    "mdr_id":             st.column_config.TextColumn("MDR ID"),
                    "field_name":         st.column_config.TextColumn("Field",       width="small"),
                    "original_value":     st.column_config.TextColumn("Original",    width="medium"),
                    "normalised_value":   st.column_config.TextColumn("Normalised",  width="medium"),
                    "normalisation_rule": st.column_config.TextColumn("Rule"),
                    "confidence":         st.column_config.TextColumn("Confidence",  width="small"),
                },
                hide_index=True,
                width="stretch",
            )
            if "run_timestamp" not in tx_view.columns:
                st.caption("Pipeline Run column not present — re-run generate_staged_layer.py to populate it.")

            # Navigate to Document Detail when a specific MDR ID is selected.
            # Shows a button so the user can jump to the full document view.
            if sel_tx_mdr != "All":
                if st.button(
                    f"Open {sel_tx_mdr} in Document Detail ->",
                    key="tx_open_detail",
                    type="secondary",
                ):
                    st.session_state["_nav_request"]    = "Document Detail"
                    st.session_state["_detail_request"] = sel_tx_mdr
                    st.rerun()

    # ────────────────────────────────────────────────────────────────────────
    # TAB 3 — Resolution Audit Trail
    # Shows every DQ_REMEDIATION event from edit_log.csv — the permanent record
    # of who resolved which flag, when, and what corrected value they provided.
    # ────────────────────────────────────────────────────────────────────────
    with tab_audit:
        st.markdown('<div class="section-header">Resolution Audit Trail — DQ Remediation Events</div>', unsafe_allow_html=True)

        with st.expander("What is this?", expanded=False):
            st.markdown("""
**Where resolved flag values are stored:**

When a Document Controller enters a correction value and clicks *Confirm Resolved*, two things happen:

1. The flag is marked `resolved = True` in `staged_dq_flags.csv` — it leaves the remediation queue.
2. The event (who, when, which document, which flag, what value was entered) is appended to `dashboard/edit_log.csv` — the permanent, append-only audit trail.

This table shows only the DQ remediation entries from that log. In a live system, the corrected value would also be written back to the source system record.
            """)

        if not EDIT_LOG_CSV.exists():
            st.info("No audit log yet — no edits have been made in this session.")
        else:
            _log("LOAD", f"Reading audit log for Resolution Audit Trail tab")
            audit = pd.read_csv(EDIT_LOG_CSV)

            # Filter to DQ remediation events only (field starts with "dq_flag_resolved:")
            dq_audit = audit[audit["field"].str.startswith("dq_flag_resolved:", na=False)].copy()

            if dq_audit.empty:
                st.info("No DQ flag resolution events recorded yet. Resolve a flag in the DC Remediation Queue tab to see it here.")
            else:
                # Parse the flag ID out of the field column: "dq_flag_resolved:DQF-0001" -> "DQF-0001"
                dq_audit["flag_id"] = dq_audit["field"].str.replace("dq_flag_resolved:", "", regex=False)

                # Sort newest first
                dq_audit = dq_audit.sort_values("timestamp", ascending=False).reset_index(drop=True)

                # ── Search filter ────────────────────────────────────────────────
                at_search = st.text_input(
                    "Search audit trail",
                    placeholder="Filter by document ID, flag ID, user, or corrected value...",
                    key="audit_trail_search",
                    help="Case-insensitive partial match across all columns.",
                )
                if at_search.strip():
                    q = at_search.strip().lower()
                    dq_audit = dq_audit[
                        dq_audit["mdr_id"].str.lower().str.contains(q, na=False)
                        | dq_audit["flag_id"].str.lower().str.contains(q, na=False)
                        | dq_audit["username"].str.lower().str.contains(q, na=False)
                        | dq_audit["new_value"].astype(str).str.lower().str.contains(q, na=False)
                        | dq_audit["old_value"].astype(str).str.lower().str.contains(q, na=False)
                    ]

                st.markdown(
                    f'<div style="font-size:0.75rem; color:#7a8499; margin:0.25rem 0 0.5rem 0;">'
                    f'<b>{len(dq_audit)}</b> DQ remediation event(s) on record.</div>',
                    unsafe_allow_html=True,
                )

                # ── Per-row cards with inline Document Detail link ────────────────
                # Rendered as column rows rather than st.dataframe so each row can have
                # a live navigation button.  Streamlit's dataframe widget has no link cell type.
                hc1, hc2, hc3, hc4, hc5, hc6, hc7 = st.columns([2.2, 1.2, 2.5, 1.0, 1.5, 1.5, 1.2])
                for lbl, col in zip(
                    ["Resolved At", "By", "Document (MDR ID)", "Flag", "Original", "Corrected", ""],
                    [hc1, hc2, hc3, hc4, hc5, hc6, hc7],
                ):
                    col.markdown(
                        f'<div style="font-size:0.68rem; font-weight:600; color:#4a5568; '
                        f'padding-bottom:0.2rem; border-bottom:1px solid #2a3040;">{lbl}</div>',
                        unsafe_allow_html=True,
                    )

                for _idx_r, ev_row in dq_audit.iterrows():
                    rc1, rc2, rc3, rc4, rc5, rc6, rc7 = st.columns([2.2, 1.2, 2.5, 1.0, 1.5, 1.5, 1.2])
                    rc1.markdown(
                        f'<div style="font-size:0.75rem; color:#7a8499; padding:0.2rem 0;">'
                        f'{str(ev_row.get("timestamp",""))[:19]}</div>', unsafe_allow_html=True)
                    rc2.markdown(
                        f'<div style="font-size:0.75rem; color:#e8ecf4; padding:0.2rem 0;">'
                        f'{ev_row.get("username","")}</div>', unsafe_allow_html=True)
                    rc3.markdown(
                        f'<div style="font-size:0.72rem; color:#93c5fd; font-family:\'IBM Plex Mono\',monospace; padding:0.2rem 0;">'
                        f'{ev_row.get("mdr_id","")}</div>', unsafe_allow_html=True)
                    rc4.markdown(
                        f'<div style="font-size:0.72rem; color:#7a8499; padding:0.2rem 0;">'
                        f'{ev_row.get("flag_id","")}</div>', unsafe_allow_html=True)
                    rc5.markdown(
                        f'<div style="font-size:0.72rem; color:#ef4444; padding:0.2rem 0;">'
                        f'<code>{str(ev_row.get("old_value",""))[:30]}</code></div>', unsafe_allow_html=True)
                    rc6.markdown(
                        f'<div style="font-size:0.72rem; color:#22c55e; padding:0.2rem 0;">'
                        f'<b>{str(ev_row.get("new_value",""))[:30]}</b></div>', unsafe_allow_html=True)
                    _doc_id = str(ev_row.get("mdr_id", ""))
                    if rc7.button("View ->", key=f"audit_nav_{_idx_r}"):
                        st.session_state["_nav_request"]    = "Document Detail"
                        st.session_state["_detail_request"] = _doc_id
                        st.rerun()

                # Also show PM edits (non-DQ) as a secondary section so the full log is accessible
                pm_audit = audit[~audit["field"].str.startswith("dq_flag_resolved:", na=False)].copy()
                if not pm_audit.empty:
                    with st.expander(f"Other dashboard edits ({len(pm_audit)} events — priority, critical path, notes)", expanded=False):
                        pm_audit = pm_audit.sort_values("timestamp", ascending=False).reset_index(drop=True)
                        st.dataframe(
                            pm_audit[["timestamp", "username", "mdr_id", "field", "old_value", "new_value"]],
                            column_config={
                                "timestamp": st.column_config.TextColumn("Timestamp"),
                                "username":  st.column_config.TextColumn("User",      width="small"),
                                "mdr_id":    st.column_config.TextColumn("Document"),
                                "field":     st.column_config.TextColumn("Field",     width="small"),
                                "old_value": st.column_config.TextColumn("Old Value", width="medium"),
                                "new_value": st.column_config.TextColumn("New Value", width="medium"),
                            },
                            hide_index=True,
                            use_container_width=True,
                        )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # Prevent Ctrl+C (Strg+C) from triggering Streamlit's built-in "Clear cache"
    # keyboard shortcut. Streamlit's handler listens for the 'c' key and does not
    # check whether Ctrl is held — so Ctrl+C trips it. We intercept in the capture
    # phase (before Streamlit sees the event) and stop propagation only when Ctrl
    # or Meta (Mac Cmd) is held. The browser's system-level copy action is separate
    # from JS keyboard events and is unaffected — selected text still copies normally.
    st.html("""
    <script>
    (function() {
        /* 1 — Prevent Ctrl+C from triggering Streamlit's "Clear cache" shortcut.
               Streamlit's keyboard handler lives on window.parent (the main page),
               not on this iframe.  We therefore register the blocker on BOTH
               the iframe window (belt) and the parent window (suspenders).
               stopPropagation() alone is not enough once the event reaches the
               parent — stopImmediatePropagation() stops other listeners on the
               SAME target, which is what kills Streamlit's handler. */
        function blockCtrlC(e) {
            if ((e.ctrlKey || e.metaKey) && e.key === 'c') {
                e.stopPropagation();
                e.stopImmediatePropagation();
                /* Do NOT call preventDefault() — that would block the browser's
                   native copy action (the actual Clipboard API event is separate). */
            }
        }
        window.addEventListener('keydown', blockCtrlC, true);
        try {
            /* window.parent may throw a SecurityError in cross-origin iframes;
               guard with try/catch so the rest of the script still runs. */
            window.parent.addEventListener('keydown', blockCtrlC, true);
        } catch(err) {}

        /* 2 — Keep sidebar permanently open.
               Two mechanisms work together:
               a) hideCollapseBtn() finds the collapse arrow (the "<" button inside
                  the sidebar) and hides it via inline style so the user cannot click
                  it. We target window.parent.document because st.html() runs inside
                  an iframe; the sidebar lives in the parent page.
               b) A MutationObserver watches the parent DOM continuously. Whenever
                  Streamlit collapses the sidebar it adds
                  [data-testid="collapsedControl"] to the DOM (the ">" expand arrow
                  in the main area). The observer sees that node appear and
                  immediately clicks it to re-open the sidebar. A debounce timer
                  (100 ms) prevents the observer from firing dozens of times during
                  rapid Streamlit re-renders. A one-second cooldown after each
                  expand-click prevents click loops. */
        var doc = window.parent.document;
        var _debounceTimer = null;
        var _expandCooldown = false;

        function hideCollapseBtn() {
            /* Try both element+attribute and attribute-only selectors for
               compatibility across Streamlit versions. */
            ['button[data-testid="stSidebarCollapseButton"]',
             '[data-testid="stSidebarCollapseButton"]'
            ].forEach(function(sel) {
                var btn = doc.querySelector(sel);
                if (btn) {
                    btn.style.display     = 'none';
                    btn.style.visibility  = 'hidden';
                    btn.style.pointerEvents = 'none';
                }
            });
        }

        function expandIfCollapsed() {
            if (_expandCooldown) return;
            var expBtn = doc.querySelector('[data-testid="collapsedControl"]');
            if (expBtn) {
                _expandCooldown = true;
                expBtn.click();
                /* Reset cooldown after 1 s so a genuine re-collapse can be caught. */
                setTimeout(function() { _expandCooldown = false; }, 1000);
            }
        }

        /* 3 — Auto-dismiss Streamlit's "Clear cache?" dialog.
               Pressing Ctrl+C (Strg+C) triggers Streamlit's built-in 'c' keyboard
               shortcut which opens the "Clear cache?" modal — even when Ctrl is held.
               Our capture-phase listener above intercepts when it can, but event
               registration order means Streamlit sometimes fires first.  As a
               belt-and-suspenders, we watch the DOM for the dialog and click
               "Cancel" automatically within ~100 ms if it appears. */
        function dismissClearCacheDialog() {
            /* Identify the modal by presence of a "Clear cache" button alongside
               a "Cancel" button — this is unique to Streamlit's cache-clear dialog.
               We do NOT use text-matching on the heading because Streamlit may
               render it in shadow DOM or in a different element depending on version. */
            var modals = doc.querySelectorAll('[role="dialog"]');
            modals.forEach(function(modal) {
                var btns = Array.from(modal.querySelectorAll('button'));
                var hasClearBtn = btns.some(function(b) {
                    return b.textContent.trim().toLowerCase().indexOf('clear cache') >= 0;
                });
                if (!hasClearBtn) return;
                /* Found the Clear Cache dialog — locate and click Cancel. */
                var cancelBtn = btns.find(function(b) {
                    var t = b.textContent.trim().toLowerCase();
                    return t === 'cancel' || t === 'abbrechen';
                });
                if (cancelBtn) cancelBtn.click();
            });
        }

        function fixSidebar() {
            hideCollapseBtn();
            expandIfCollapsed();
            dismissClearCacheDialog();
        }

        /* Run on load — handle localStorage "collapsed" state and initial render. */
        setTimeout(fixSidebar, 300);
        setTimeout(fixSidebar, 900);
        setTimeout(fixSidebar, 2000);

        /* MutationObserver — runs on every DOM change (debounced to 100 ms) so
           any mid-session collapse is caught and reversed immediately. */
        new MutationObserver(function() {
            clearTimeout(_debounceTimer);
            _debounceTimer = setTimeout(fixSidebar, 100);
        }).observe(doc.body, { childList: true, subtree: true });

    })();
    </script>
    """)

    # App header
    st.markdown("""
    <div class="app-header">
        <h1>MDR CONTROL CENTRE</h1>
        <span class="sub">PROJ1 · Master Document Register · ISO 19650-2</span>
    </div>
    """, unsafe_allow_html=True)

    # Working copy — lives in session state so in-session edits (e.g. priority changes
    # that trigger RAG recomputation) survive reruns without hitting the CSV on every cycle.
    # Cleared by the "Reset to pipeline state" button in the sidebar, which reloads
    # fresh from CSV and recomputes RAG from the stored field values.
    if "mdr_working" not in st.session_state:
        st.session_state["mdr_working"] = load_mdr()
    df = st.session_state["mdr_working"]

    # Sidebar — returns active user, selected saved view, and active role
    current_user, active_view, active_role = render_sidebar(df)

    # Route to page
    page = st.session_state.get("nav_page", "Overview")

    if page == "Overview":
        page_overview(df, current_user, active_role)
    elif page == "MDR":
        page_mdr_register(df, current_user, active_view, active_role)
    elif page == "My Watchlist":
        page_watchlist(df, current_user)
    elif page == "Document Detail":
        page_document_detail(df, current_user)
    elif page == "Source System Health":
        page_source_health(df, current_user, active_role)
    elif page == "Audit Trail":
        page_audit_trail(current_user)


if __name__ == "__main__":
    main()
