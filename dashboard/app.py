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

load_dotenv(PROJECT_ROOT / ".env")

TODAY = date(2026, 5, 8)  # fixed for demo dataset

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


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def load_mdr() -> pd.DataFrame:
    """Load MDR CSV — source of truth for the ANALYTICAL layer."""
    if not MDR_CSV.exists():
        st.error(f"MDR CSV not found at {MDR_CSV}. Run generate_mdr_layer.py first.")
        st.stop()
    df = pd.read_csv(MDR_CSV, parse_dates=["planned_submission_date", "planned_approval_date",
                                            "baseline_approval_date", "previous_approval_date"])
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
    """Append an edit event to edit_log.csv."""
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
    with open(EDIT_LOG_CSV, "a", newline="", encoding="utf-8") as f:
        import csv as _csv
        w = _csv.DictWriter(f, fieldnames=cols)
        if not exists:
            w.writeheader()
        w.writerow(row)


def save_mdr(df: pd.DataFrame):
    """Write the MDR DataFrame back to CSV (date columns as ISO strings)."""
    date_cols = ["planned_submission_date", "planned_approval_date",
                 "baseline_approval_date", "previous_approval_date"]
    out = df.copy()
    for col in date_cols:
        if col in out.columns:
            out[col] = out[col].astype(str)
    out.to_csv(MDR_CSV, index=False)


# ══════════════════════════════════════════════════════════════════════════════
# SNOWFLAKE HELPER (read-only — RAW + STAGED)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource(ttl=300)
def get_snowflake_connection():
    """Returns a Snowflake connection or None if credentials missing."""
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
        return conn
    except Exception:
        return None


@st.cache_data(ttl=300)
def query_snowflake(sql: str) -> pd.DataFrame:
    conn = get_snowflake_connection()
    if conn is None:
        return pd.DataFrame()
    try:
        return pd.read_sql(sql, conn)
    except Exception as e:
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

def render_sidebar(df: pd.DataFrame) -> tuple[str, str | None]:
    """Renders sidebar. Returns (current_user, active_saved_view_name | None)."""

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

    return current_user, active_view


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
                    <span style="color:#ef4444; font-family:'IBM Plex Mono',monospace; font-weight:600;">{int(row['red'])}</span>
                    <span style="color:#f59e0b; font-family:'IBM Plex Mono',monospace; font-weight:600;">{int(row['amber'])}</span>
                    <span style="color:#22c55e; font-family:'IBM Plex Mono',monospace; font-weight:600;">{int(row['green'])}</span>
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

def page_mdr_register(df: pd.DataFrame, current_user: str, active_view: str | None):
    st.markdown('<div class="page-title">MDR Register</div>', unsafe_allow_html=True)
    st.info("🚧 MDR Register — to be built Day 4. Will include full filterable/sortable table with inline editing, bookmark toggles, and Excel export.")


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


def page_source_health(df: pd.DataFrame):
    st.markdown('<div class="page-title">Source System Health</div>', unsafe_allow_html=True)
    st.info("🚧 Source system health view — to be built Day 4.")


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

    # Sidebar — returns active user and selected saved view
    current_user, active_view = render_sidebar(df)

    # Route to page
    page = st.session_state.get("nav_page", "Overview")

    if page == "Overview":
        page_overview(df, current_user)
    elif page == "MDR Register":
        page_mdr_register(df, current_user, active_view)
    elif page == "My Watchlist":
        page_watchlist(df, current_user)
    elif page == "Document Detail":
        page_document_detail(df, current_user)
    elif page == "Source System Health":
        page_source_health(df)


if __name__ == "__main__":
    main()
