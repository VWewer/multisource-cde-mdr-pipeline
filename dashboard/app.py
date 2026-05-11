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

/* ── Remove sidebar collapse button ── */
/* Hides only the collapse arrow INSIDE the sidebar so users cannot close it
   accidentally. The expand arrow in the main content area is left untouched —
   the JS in main() auto-clicks it on load to recover from any localStorage
   "collapsed" state, so manual cache clearing is no longer required. */
button[data-testid="stSidebarCollapseButton"] {
    display: none !important;
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
    if st.button("Close", type="primary", key="src_doc_close"):
        st.rerun()


def rag_badge(rag: str) -> str:
    cls = rag.lower() if rag in ("RED", "AMBER", "GREEN") else "blue"
    return f'<span class="badge {cls}">{rag}</span>'


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
    if "_nav_request" in st.session_state:
        # Push current page to history before navigating so "← Back" can return here.
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
            ["Overview", "MDR Register", "My Watchlist", "Document Detail", "Source System Health"],
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
            st.session_state["nav_page"]     = prev["page"]
            if prev["detail"]:
                st.session_state["detail_doc_select"] = prev["detail"]
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
        if st.button("Reset to pipeline state", width="stretch",
                     help="Discard in-session edits and reload from the pipeline CSV. "
                          "Saved changes (notes, % complete) are preserved on disk."):
            # Drop the working copy — next render re-reads CSV and recomputes RAG fresh.
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
| MDR Register | Full document list — filter, sort, export, inline edits (PM role) |
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
        for k in ("reg_disc", "reg_rag", "reg_prio", "reg_cp", "reg_appr", "reg_trend", "reg_person"):
            st.session_state[k] = "All"
        if rag_val:
            st.session_state["reg_rag"]    = rag_val
        if trend_val:
            st.session_state["reg_trend"]  = trend_val
        if disc_val:
            st.session_state["reg_disc"]   = disc_val
        if person_val:
            st.session_state["reg_person"] = person_val
        st.session_state["_nav_request"] = "MDR Register"
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
        '<div style="font-size:0.68rem; color:#4a5568; margin-top:0.2rem; margin-bottom:0.5rem;">'
        '<b style="color:#ef4444;">RED</b> = at or past the deadline warning threshold for this priority &nbsp;|&nbsp; '
        '<b style="color:#f59e0b;">AMBER</b> = within warning window (Very High: &le;21d, High: &le;14d, Medium: &le;7d) &nbsp;|&nbsp; '
        '<b style="color:#22c55e;">GREEN</b> = on track'
        '</div>',
        unsafe_allow_html=True
    )

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
            # Build a display table — trend icons added as a plain text column
            TREND_ICON = {"SLIPPING": "📉 Slipping", "STALLED": "⏸ Stalled",
                          "RECOVERING": "📈 Recovering", "STABLE": "Stable"}
            crit_display = crit[[
                "mdr_id", "document_title", "discipline", "rag_status",
                "schedule_float_days", "date_trend", "responsible_person",
            ]].copy()
            crit_display["date_trend"] = crit_display["date_trend"].map(TREND_ICON).fillna("")

            st.caption("Click a row to open Document Detail for that document.")
            evt = st.dataframe(
                crit_display,
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={
                    "mdr_id":              st.column_config.TextColumn("ID",         width="small"),
                    "document_title":      st.column_config.TextColumn("Document",   width="large"),
                    "discipline":          st.column_config.TextColumn("Disc.",       width="small"),
                    "rag_status":          st.column_config.TextColumn("RAG",         width="small"),
                    "schedule_float_days": st.column_config.NumberColumn("Float (d)", format="%d"),
                    "date_trend":          st.column_config.TextColumn("Trend",       width="medium"),
                    "responsible_person":  st.column_config.TextColumn("Lead",        width="medium"),
                },
            )
            # Row click triggers navigation to Document Detail
            if evt.selection.rows:
                selected_id = crit_display.iloc[evt.selection.rows[0]]["mdr_id"]
                st.session_state["_nav_request"]    = "Document Detail"
                st.session_state["_detail_request"] = selected_id
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
    st.markdown('<div class="page-title">MDR Register</div>', unsafe_allow_html=True)

    # Load saved view defaults
    views     = load_saved_views()
    view_data = views.get(active_view, {}) if active_view else {}
    vf        = view_data.get("filters", {})
    v_cols    = view_data.get("columns", [])
    v_sort    = view_data.get("sort", {})

    # Reset filter widget state when the active view changes so new defaults apply
    if st.session_state.get("_reg_view") != active_view:
        st.session_state["_reg_view"] = active_view
        for k in ("reg_disc", "reg_rag", "reg_prio", "reg_cp", "reg_appr", "reg_trend", "reg_person", "reg_cols"):
            st.session_state.pop(k, None)

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
        fp1, _fp2 = st.columns([2, 4])
        sel_person = fp1.selectbox("Responsible Person", person_opts, index=_idx(person_opts, None), key="reg_person")

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

    _log("FILTER", (
        f"disc={sel_disc} | rag={sel_rag} | prio={sel_prio} | "
        f"cp={sel_cp} | appr={sel_appr} | trend={sel_trend} | person={sel_person} "
        f"-> {len(filt)} of {len(df)} rows"
    ))

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

    # Trigger columns — boolean, always default False.  Checked immediately after the
    # editor renders; the value is never written to the MDR CSV.
    # open_detail: navigates same-tab to Document Detail for the checked row.
    # view_source: pops a demo dialog explaining the CDE document link concept.
    display_df.insert(1, "open_detail", False)
    # Place view_source right after document_title if that column is visible;
    # otherwise append at the end.
    if "document_title" in display_df.columns:
        title_pos = list(display_df.columns).index("document_title")
        display_df.insert(title_pos + 1, "view_source", False)
    else:
        display_df["view_source"] = False

    st.markdown(
        f'<div style="font-size:0.75rem;color:#7a8499;margin:0.25rem 0 0.5rem 0;">'
        f'Showing <b>{len(display_df)}</b> of {len(df)} documents</div>',
        unsafe_allow_html=True,
    )

    # ── Column config ─────────────────────────────────────────────────────────
    col_cfg = {
        "mdr_id":       st.column_config.TextColumn("ID", width="small", disabled=True),
        # Trigger columns — checking fires an action; the value is never saved.
        "open_detail":  st.column_config.CheckboxColumn(
            "-> Detail",
            width="small",
            help="Check to open Document Detail for this document (same tab)",
        ),
        "view_source":  st.column_config.CheckboxColumn(
            "Src Doc",
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

    # ── Navigation triggers — checked first, before any persistence ──────────
    # open_detail: navigate same-tab to Document Detail.
    # view_source: pop the demo dialog explaining the CDE document link concept.
    # Both columns default to False and are never written to the MDR CSV.
    nav_rows = edited_df[edited_df["open_detail"].astype(bool)]
    if not nav_rows.empty:
        target_id = nav_rows.iloc[0]["mdr_id"]
        st.session_state["_nav_request"]    = "Document Detail"
        st.session_state["_detail_request"] = target_id
        _log("LOAD", f"MDR Register: navigating to Document Detail for {target_id}")
        st.rerun()

    src_rows = edited_df[edited_df["view_source"].astype(bool)]
    if not src_rows.empty:
        src_row   = src_rows.iloc[0]
        src_title = str(src_row.get("document_title", src_row["mdr_id"]))
        _source_doc_dialog(src_row["mdr_id"], src_title)

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
    c1, c2, c3 = st.columns(3)
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

    # ── Tabs: Remediation Queue | Pipeline Run Report ────────────────────────
    tab_queue, tab_report = st.tabs(["DC Remediation Queue", "Pipeline Run Report"])

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
        fc1, fc2, _ = st.columns([2, 3, 3])
        src_options  = ["All"] + sorted(dq_raw["source_system"].dropna().unique().tolist())
        type_options = ["All"] + sorted(dq_raw["flag_type"].dropna().unique().tolist())
        sel_src  = fc1.selectbox("Source system", src_options,  key="health_src_filter")
        sel_type = fc2.selectbox("Flag type",     type_options, key="health_type_filter")

        # Always show only unresolved flags in the remediation queue
        queue = dq_raw[~dq_raw["resolved"]].copy()
        if sel_src  != "All":
            queue = queue[queue["source_system"] == sel_src]
        if sel_type != "All":
            queue = queue[queue["flag_type"] == sel_type]

        _log("FILTER", f"Health queue — src={sel_src} type={sel_type} -> {len(queue)} flags")

        st.markdown(
            f'<div style="font-size:0.75rem; color:#7a8499; margin:0.25rem 0 0.5rem 0;">'
            f'Showing <b>{len(queue)}</b> unresolved flags</div>',
            unsafe_allow_html=True,
        )

        if queue.empty:
            st.success("No unresolved flags for the selected filters — all clear.")
        else:
            # ── Per-flag resolution cards ─────────────────────────────────────────────
            # Each flag is an expander.  DCs see a value input + Confirm button inside.
            # In a real system the confirmed value would update the source system record;
            # here it is captured in the audit log (edit_log.csv) and the flag is closed.
            for _, row in queue.iterrows():
                flag_id  = str(row["flag_id"])
                ftype    = str(row.get("flag_type",      ""))
                field    = str(row.get("field_name",     "—"))
                orig     = str(row.get("original_value", ""))
                sugg     = str(row.get("suggested_value",""))
                detail   = str(row.get("flag_detail",    ""))
                mdr_id   = str(row.get("mdr_id",         ""))
                src_sys  = str(row.get("source_system",  ""))

                label = f"{flag_id}  |  {src_sys}  |  {ftype}  |  field: {field}"
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

                    if is_dc:
                        # Prompt for the corrected value or resolution note
                        placeholder = (
                            f"Enter corrected {field} value..." if ftype == "MISSING_MANDATORY_FIELD"
                            else "Enter resolution note or confirmed correct value..."
                        )
                        resolution_val = st.text_input(
                            "Resolution value / note",
                            key=f"resolve_val_{flag_id}",
                            placeholder=placeholder,
                            help=(
                                "For missing fields: enter the actual value (e.g. the author name). "
                                "For normalisation issues: confirm the correct canonical value. "
                                "For duplicates: describe which record was kept. "
                                "This is recorded in the audit log."
                            ),
                        )
                        if st.button("Confirm Resolved", key=f"btn_resolve_{flag_id}", type="primary"):
                            if resolution_val.strip():
                                now_str = str(datetime.now(timezone.utc))
                                mask = dq_raw["flag_id"] == flag_id
                                dq_raw.loc[mask, "resolved"]    = True
                                dq_raw.loc[mask, "resolved_by"] = current_user
                                dq_raw.loc[mask, "resolved_at"] = now_str
                                # Log to audit trail: old value = original, new value = resolution
                                log_edit(
                                    current_user, mdr_id,
                                    f"dq_flag_resolved:{flag_id}",
                                    orig if orig else "MISSING",
                                    resolution_val.strip(),
                                )
                                _log("EDIT", f"DC {current_user} resolved {flag_id} on {mdr_id} -> '{resolution_val.strip()}'")
                                save_dq_flags(dq_raw)
                                st.toast(f"{flag_id} marked as resolved.", icon="✅")
                                st.rerun()
                            else:
                                st.warning("Enter a resolution value before confirming. In a real system this becomes the corrected field value in the source record.")

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
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total transformations", len(tx))
            m2.metric("Source systems", tx["source_system"].nunique())
            m3.metric("Fields transformed", tx["field_name"].nunique())
            m4.metric("Rules applied", tx["normalisation_rule"].nunique())

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

            tf1, tf2, tf3 = st.columns([2, 3, 3])
            tx_src_opts  = ["All"] + sorted(tx["source_system"].dropna().unique().tolist())
            tx_rule_opts = ["All"] + sorted(tx["normalisation_rule"].dropna().unique().tolist())
            tx_fld_opts  = ["All"] + sorted(tx["field_name"].dropna().unique().tolist())
            sel_tx_src  = tf1.selectbox("Source system", tx_src_opts,  key="tx_src_filter")
            sel_tx_rule = tf2.selectbox("Rule",          tx_rule_opts, key="tx_rule_filter")
            sel_tx_fld  = tf3.selectbox("Field",         tx_fld_opts,  key="tx_fld_filter")

            tx_view = tx.copy()
            if sel_tx_src  != "All":
                tx_view = tx_view[tx_view["source_system"] == sel_tx_src]
            if sel_tx_rule != "All":
                tx_view = tx_view[tx_view["normalisation_rule"] == sel_tx_rule]
            if sel_tx_fld  != "All":
                tx_view = tx_view[tx_view["field_name"] == sel_tx_fld]

            _log("FILTER", f"Transform log — src={sel_tx_src} rule={sel_tx_rule} field={sel_tx_fld} -> {len(tx_view)} rows")

            st.markdown(
                f'<div style="font-size:0.75rem; color:#7a8499; margin:0.25rem 0 0.5rem 0;">'
                f'Showing <b>{len(tx_view)}</b> of {len(tx)} transformation records</div>',
                unsafe_allow_html=True,
            )

            st.dataframe(
                tx_view.reset_index(drop=True),
                column_config={
                    "source_system":      st.column_config.TextColumn("Source",    width="small"),
                    "source_native_id":   st.column_config.TextColumn("Source ID", width="small"),
                    "mdr_id":             st.column_config.TextColumn("MDR ID"),
                    "field_name":         st.column_config.TextColumn("Field",     width="small"),
                    "original_value":     st.column_config.TextColumn("Original",  width="medium"),
                    "normalised_value":   st.column_config.TextColumn("Normalised",width="medium"),
                    "normalisation_rule": st.column_config.TextColumn("Rule"),
                    "confidence":         st.column_config.TextColumn("Confidence",width="small"),
                },
                hide_index=True,
                width="stretch",
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
               Streamlit's handler fires on any 'c' keydown regardless of Ctrl.
               We intercept in the capture phase and stop propagation only when
               Ctrl or Meta is held; the browser's native copy action is unaffected. */
        window.addEventListener('keydown', function(e) {
            if ((e.ctrlKey || e.metaKey) && e.key === 'c') {
                e.stopPropagation();
            }
        }, true);

        /* 2 — Restore sidebar if localStorage left it collapsed.
               [data-testid="collapsedControl"] is the expand arrow that Streamlit
               renders in the main content area when the sidebar is closed.
               It only exists in the DOM when the sidebar IS collapsed, so clicking
               it is a no-op when the sidebar is already open.
               We try twice with a delay to handle Streamlit's async render timing. */
        function tryExpand() {
            var btn = window.parent.document.querySelector('[data-testid="collapsedControl"]');
            if (btn) btn.click();
        }
        setTimeout(tryExpand, 300);
        setTimeout(tryExpand, 900);
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
    elif page == "MDR Register":
        page_mdr_register(df, current_user, active_view, active_role)
    elif page == "My Watchlist":
        page_watchlist(df, current_user)
    elif page == "Document Detail":
        page_document_detail(df, current_user)
    elif page == "Source System Health":
        page_source_health(df, current_user, active_role)


if __name__ == "__main__":
    main()
