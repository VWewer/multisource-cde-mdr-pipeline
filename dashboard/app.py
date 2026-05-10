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
DQ_FLAGS_CSV     = DATA_DIR / "staged_dq_flags.csv"

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
    "sarah.chen":       {"display": "Sarah Chen",        "role": "Instrumentation Lead",   "discipline": "Instrumentation"},
    "james.okafor":     {"display": "James Okafor",      "role": "Mechanical Lead",         "discipline": "Mechanical"},
    "maria.lindqvist":  {"display": "Maria Lindqvist",   "role": "Electrical Lead",         "discipline": "Electrical"},
    "hassan.al-rashid": {"display": "Hassan Al-Rashid",  "role": "Civil Lead",              "discipline": "Civil"},
    "project.director": {"display": "Project Director",  "role": "All Disciplines",         "discipline": None},
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
    _log("LOAD", f"MDR loaded — {len(df)} rows × {len(df.columns)} columns")

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
            {"username": "sarah.chen",       "mdr_id": "MDR-INS-001", "personal_note": "Authority approval — watch closely", "created_at": str(TODAY)},
            {"username": "james.okafor",     "mdr_id": "MDR-MEC-001", "personal_note": "Critical path — vendor drawings pending", "created_at": str(TODAY)},
            {"username": "project.director", "mdr_id": "MDR-MEC-001", "personal_note": "Flagged by client last week", "created_at": str(TODAY)},
            {"username": "project.director", "mdr_id": "MDR-ELE-001", "personal_note": "",                                          "created_at": str(TODAY)},
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
    _log("EDIT", f"{username} | {mdr_id} | {field}: '{old_val}' → '{new_val}'")

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

    with st.sidebar:
        st.markdown('<div class="sidebar-logo">📋 MDR Control · PROJ1</div>', unsafe_allow_html=True)

        # ── User selector ──────────────────────────────────────────────────────
        st.markdown('<div class="sidebar-section">Active User</div>', unsafe_allow_html=True)
        user_options = list(USERS.keys())
        user_labels  = [f"{USERS[u]['display']} — {USERS[u]['role']}" for u in user_options]

        user_idx = st.selectbox(
            "User",
            options=range(len(user_options)),
            format_func=lambda i: user_labels[i],
            label_visibility="collapsed",
            key="user_selector",
        )
        current_user = user_options[user_idx]
        u = USERS[current_user]
        st.markdown(
            f'<div class="user-chip">👤 {u["display"]}</div>'
            f'<div class="user-role">{u["role"]}</div>',
            unsafe_allow_html=True
        )

        # ── Role selector ──────────────────────────────────────────────────────
        st.markdown('<div class="sidebar-section">Active Role</div>', unsafe_allow_html=True)
        active_role = st.radio(
            "Role",
            ROLES,
            index=0,
            label_visibility="collapsed",
            key="active_role",
        )

        st.divider()

        # ── Navigation ────────────────────────────────────────────────────────
        st.markdown('<div class="sidebar-section">Navigation</div>', unsafe_allow_html=True)
        page = st.radio(
            "Page",
            ["Overview", "MDR Register", "My Watchlist", "Document Detail", "Source System Health"],
            label_visibility="collapsed",
            key="nav_page",
        )

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
        if st.button("🔄 Refresh data", use_container_width=True):
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

def page_overview(df: pd.DataFrame, current_user: str):
    st.markdown('<div class="page-title">Overview — Project PROJ1 · Snapshot 8 May 2026</div>', unsafe_allow_html=True)

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

    # ── Date Trend Summary ─────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Date Trend</div>', unsafe_allow_html=True)
    trend = compute_trend_counts(df)

    slip_avg = df[df["date_trend"] == "SLIPPING"]["total_slip_days"].mean()
    slip_avg_str = f"avg slip {slip_avg:.0f}d" if not pd.isna(slip_avg) else ""

    stall_avg = df[df["date_trend"] == "STALLED"]["total_slip_days"].mean()
    stall_avg_str = f"avg slip {stall_avg:.0f}d" if not pd.isna(stall_avg) else ""

    st.markdown(f"""
    <div class="trend-grid">
        <div class="trend-tile slipping">
            <div class="trend-icon">📉</div>
            <div>
                <div class="t-count">{trend['SLIPPING']}</div>
                <div class="t-label">Slipping</div>
                <div class="t-sub">{slip_avg_str}</div>
            </div>
        </div>
        <div class="trend-tile stalled">
            <div class="trend-icon">⏸</div>
            <div>
                <div class="t-count">{trend['STALLED']}</div>
                <div class="t-label">Stalled</div>
                <div class="t-sub">{stall_avg_str}</div>
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
            rows_html = ""
            for _, row in crit.iterrows():
                float_html = format_float(row.get("schedule_float_days", ""))
                rag_html   = rag_badge(row.get("rag_status", ""))
                trend_val  = row.get("date_trend", "")
                trend_icon = {"SLIPPING": "📉", "STALLED": "⏸", "RECOVERING": "📈", "STABLE": "→"}.get(trend_val, "")
                slip       = row.get("total_slip_days", "")
                slip_str   = f'+{int(slip)}d slip' if pd.notna(slip) and str(slip) != "" else ""
                title      = str(row.get("document_title", ""))[:45] + ("…" if len(str(row.get("document_title", ""))) > 45 else "")
                disc       = row.get("discipline", "")[:3].upper()
                prio       = row.get("priority", "")
                person     = row.get("responsible_person", "—")
                person_short = person.split()[0] if person else "—"

                rows_html += f"""
                <tr>
                    <td style="font-family:'IBM Plex Mono',monospace; font-size:0.7rem; color:#7a8499;">{row.get('mdr_id','')}</td>
                    <td>
                        <div style="color:#e8ecf4; font-size:0.78rem;">{title}</div>
                        <div style="color:#4a5568; font-size:0.68rem; margin-top:1px;">{disc} · {prio}</div>
                    </td>
                    <td>{rag_html}</td>
                    <td>{float_html}</td>
                    <td style="font-size:0.72rem;">{trend_icon} {slip_str}</td>
                    <td style="color:#7a8499; font-size:0.72rem;">{person_short}</td>
                </tr>"""

            st.markdown(f"""
            <table class="crit-table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Document</th>
                        <th>RAG</th>
                        <th>Float</th>
                        <th>Trend</th>
                        <th>Lead</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
            """, unsafe_allow_html=True)

    with col_right:
        st.markdown('<div class="section-header">Gatekeeper Heatmap — Stalled Documents</div>', unsafe_allow_html=True)

        stalled = df[df["date_trend"] == "STALLED"]
        if stalled.empty:
            st.markdown('<p style="color:#4a5568; font-size:0.8rem;">No stalled documents.</p>', unsafe_allow_html=True)
        else:
            gk_counts = (
                stalled.groupby("responsible_person")
                .size()
                .sort_values(ascending=False)
                .head(10)
            )
            max_count = gk_counts.max()

            rows_html = ""
            for person, count in gk_counts.items():
                bar_width = int(100 * count / max_count)
                bar_color = gk_bar_color(count, max_count)
                rows_html += f"""
                <div class="gk-row">
                    <div class="gk-name">{person}</div>
                    <div class="gk-bar-bg">
                        <div class="gk-bar-fill" style="width:{bar_width}%; background:{bar_color};"></div>
                    </div>
                    <div class="gk-count">{count} doc{"s" if count != 1 else ""}</div>
                </div>"""

            st.markdown(f'<div class="gk-grid">{rows_html}</div>', unsafe_allow_html=True)

            st.markdown(
                f'<div style="font-size:0.68rem; color:#4a5568; margin-top:0.9rem; font-family:\'IBM Plex Mono\',monospace;">'
                f'STALLED = total slip &gt;14d AND recent slip within ±5d. Indicates resource bottleneck.</div>',
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
        for k in ("reg_disc", "reg_rag", "reg_prio", "reg_cp", "reg_appr", "reg_trend", "reg_cols"):
            st.session_state.pop(k, None)

    # ── Filters ───────────────────────────────────────────────────────────────
    with st.expander("Filters", expanded=True):
        fc1, fc2, fc3, fc4, fc5, fc6 = st.columns(6)

        disc_opts  = ["All"] + sorted(df["discipline"].dropna().unique().tolist())
        rag_opts   = ["All", "RED", "AMBER", "GREEN"]
        prio_opts  = ["All", "Very High", "High", "Medium", "Low"]
        cp_opts    = ["All", "Critical Path only", "Non-critical only"]
        appr_opts  = ["All"] + sorted(df["approval_class"].dropna().unique().tolist())
        trend_opts = ["All", "SLIPPING", "STALLED", "RECOVERING", "STABLE"]

        def _idx(lst, val): return lst.index(val) if val in lst else 0

        sel_disc  = fc1.selectbox("Discipline",    disc_opts,  index=_idx(disc_opts,  vf.get("discipline")),     key="reg_disc")
        sel_rag   = fc2.selectbox("RAG",           rag_opts,   index=_idx(rag_opts,   vf.get("rag_status")),     key="reg_rag")
        sel_prio  = fc3.selectbox("Priority",      prio_opts,  index=_idx(prio_opts,  vf.get("priority")),       key="reg_prio")
        vf_cp_val = "Critical Path only" if vf.get("is_on_critical_path") is True else ("Non-critical only" if vf.get("is_on_critical_path") is False else "All")
        sel_cp    = fc4.selectbox("Critical Path", cp_opts,    index=_idx(cp_opts,    vf_cp_val),                key="reg_cp")
        sel_appr  = fc5.selectbox("Approval",      appr_opts,  index=_idx(appr_opts,  vf.get("approval_class")), key="reg_appr")
        sel_trend = fc6.selectbox("Date Trend",    trend_opts, index=_idx(trend_opts, vf.get("date_trend")),     key="reg_trend")

    # Apply filters — each line narrows the DataFrame by one criterion
    filt = df.copy()
    if sel_disc  != "All": filt = filt[filt["discipline"]        == sel_disc]
    if sel_rag   != "All": filt = filt[filt["rag_status"]        == sel_rag]
    if sel_prio  != "All": filt = filt[filt["priority"]          == sel_prio]
    if sel_cp    == "Critical Path only": filt = filt[filt["is_on_critical_path"] == True]
    elif sel_cp  == "Non-critical only":  filt = filt[filt["is_on_critical_path"] != True]
    if sel_appr  != "All": filt = filt[filt["approval_class"]    == sel_appr]
    if sel_trend != "All": filt = filt[filt["date_trend"]        == sel_trend]

    _log("FILTER", (
        f"disc={sel_disc} | rag={sel_rag} | prio={sel_prio} | "
        f"cp={sel_cp} | appr={sel_appr} | trend={sel_trend} "
        f"→ {len(filt)} of {len(df)} rows"
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

    st.markdown(
        f'<div style="font-size:0.75rem;color:#7a8499;margin:0.25rem 0 0.5rem 0;">'
        f'Showing <b>{len(display_df)}</b> of {len(df)} documents</div>',
        unsafe_allow_html=True,
    )

    # ── Column config ─────────────────────────────────────────────────────────
    col_cfg = {
        "mdr_id":                    st.column_config.TextColumn("ID", width="small", disabled=True),
        "bookmarked":                st.column_config.CheckboxColumn("⭐", width="small", help="Add to My Watchlist"),
        "document_title":            st.column_config.TextColumn("Title", width="large", disabled=True),
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
        use_container_width=True,
        hide_index=True,
        key="mdr_register_editor",
        num_rows="fixed",
    )

    # ── Persist MDR edits ─────────────────────────────────────────────────────
    has_changes = False
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
            has_changes = True
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
    st.markdown('<div class="page-title">My Watchlist</div>', unsafe_allow_html=True)
    bookmarks = load_bookmarks()
    user_bm = bookmarks[bookmarks["username"] == current_user]
    if user_bm.empty:
        st.info("No bookmarks yet. Use the MDR Register to add documents to your watchlist.")
    else:
        st.markdown(f"**{len(user_bm)} bookmarked documents**")
        st.dataframe(user_bm, use_container_width=True)
    st.info("🚧 Full watchlist view — to be built Day 4.")


def page_document_detail(df: pd.DataFrame, current_user: str):
    st.markdown('<div class="page-title">Document Detail</div>', unsafe_allow_html=True)
    mdr_ids = df["mdr_id"].tolist()
    selected = st.selectbox("Select document", mdr_ids)
    row = df[df["mdr_id"] == selected].iloc[0]
    st.json(row.to_dict())
    st.info("🚧 Full detail view with STAGED timeline — to be built Day 4.")


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

    # ── Section 2: Remediation queue ─────────────────────────────────────────
    st.markdown('<div class="section-header">Remediation Queue — Unresolved Flags</div>', unsafe_allow_html=True)

    # Role notice — only DCs can mark flags as resolved
    is_dc = (active_role == ROLE_DOCUMENT_CONTROLLER)
    if is_dc:
        st.success(f"{ROLE_DOCUMENT_CONTROLLER} — tick the 'Resolved' checkbox to mark a flag as fixed.")
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
        return

    # ── Remediation table ─────────────────────────────────────────────────────
    # Columns shown in the drilldown. flag_detail is last as it's the longest field.
    QUEUE_COLS = [
        "flag_id", "source_system", "mdr_id", "field_name",
        "flag_type", "original_value", "suggested_value", "resolved", "flag_detail",
    ]
    queue_display = queue[QUEUE_COLS].reset_index(drop=True)
    queue_orig    = queue_display.copy()  # snapshot for change detection

    queue_col_cfg = {
        "flag_id":         st.column_config.TextColumn("Flag ID",      width="small",  disabled=True),
        "source_system":   st.column_config.TextColumn("Source",       width="small",  disabled=True),
        "mdr_id":          st.column_config.TextColumn("MDR ID",                       disabled=True),
        "field_name":      st.column_config.TextColumn("Field",        width="small",  disabled=True),
        "flag_type":       st.column_config.TextColumn("Flag Type",                    disabled=True),
        "original_value":  st.column_config.TextColumn("Original",     width="small",  disabled=True),
        "suggested_value": st.column_config.TextColumn("Suggested",    width="small",  disabled=True),
        # Only Document Controllers can tick this checkbox
        "resolved":        st.column_config.CheckboxColumn("Resolved",                 disabled=not is_dc),
        "flag_detail":     st.column_config.TextColumn("Detail",       width="large",  disabled=True),
    }

    edited_queue = st.data_editor(
        queue_display,
        column_config=queue_col_cfg,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key="health_queue_editor",
    )

    # ── Persist resolved changes (DC only) ────────────────────────────────────
    if is_dc:
        # queue only shows unresolved flags, so any checkbox change is False -> True.
        changed = ~(queue_orig["resolved"].eq(edited_queue["resolved"]))
        if changed.any():
            now_str = str(datetime.now(timezone.utc))
            for i, row in edited_queue[changed].iterrows():
                flag_id = row["flag_id"]
                mask = dq_raw["flag_id"] == flag_id
                dq_raw.loc[mask, "resolved"]    = True
                dq_raw.loc[mask, "resolved_by"] = current_user
                dq_raw.loc[mask, "resolved_at"] = now_str
                log_edit(current_user, row["mdr_id"], f"dq_flag_resolved:{flag_id}", "False", "True")
                _log("EDIT", f"DC {current_user} resolved flag {flag_id} on {row['mdr_id']}")
            save_dq_flags(dq_raw)
            st.toast(f"{changed.sum()} flag(s) marked as resolved.", icon="✅")
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # App header
    st.markdown("""
    <div class="app-header">
        <h1>MDR CONTROL CENTRE</h1>
        <span class="sub">PROJ1 · Master Document Register · ISO 19650-2</span>
    </div>
    """, unsafe_allow_html=True)

    # Load data
    df = load_mdr()

    # Sidebar — returns active user, selected saved view, and active role
    current_user, active_view, active_role = render_sidebar(df)

    # Route to page
    page = st.session_state.get("nav_page", "Overview")

    if page == "Overview":
        page_overview(df, current_user)
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
