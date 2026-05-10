# CLAUDE.md — multisource-cde-mdr-pipeline

Read this file at the start of every session. It defines how we work together on this project.

---

## What this project is

A mock CDE/MDR (Common Data Environment / Master Document Register) pipeline built for
job application portfolio purposes. It simulates a real engineering project (PROJ1) with
60 documents tracked across three source systems (Windchill, SharePoint, Aveva), staged
through a data pipeline, and surfaced in a Streamlit dashboard.

**Primary audience:** Recruitment / technical interviews at Siemens Energy, Accenture, CE-RISE.

**Three-signal portfolio strategy:**
- **Video** — recruiter-facing: explains the business problem and value in plain language
- **Live dashboard** — hiring manager-facing: demonstrates domain knowledge and product thinking
- **Source code** — technical interviewer-facing: demonstrates pipeline architecture and engineering judgment

**60-second pitch (memorise this):**
> Large CAPEX projects run across multiple document systems that do not talk to each other.
> Missing traceability and data ambiguity cause delays and cost overruns that are entirely preventable.
> This pipeline simulates that problem: three source systems — Windchill, SharePoint, Aveva — each
> with distinct schemas, naming conventions, and data quality issues. The STAGED layer harmonises
> them into a single canonical record following ISO 19650, assigns each document a stable identifier
> regardless of what the source system calls it, and flags quality issues before they reach any user.
> The dashboard gives a Document Controller a remediation queue for those flags, and a Project Manager
> visibility on delivery, critical path, and completion. Every edit is written as an immutable event —
> full traceability, no overwriting.

---

## Tech stack

| Layer         | Tool                              |
|---------------|-----------------------------------|
| Language      | Python 3.14                       |
| Data gen      | `faker`, `pandas`                 |
| Database      | Snowflake (free trial)            |
| DB connector  | `snowflake-connector-python`      |
| Dashboard     | `streamlit`                       |
| Config        | `python-dotenv` (.env file)       |
| Environment   | Windows 11, `.venv` venv          |

---

## Folder structure

```
multisource-cde-mdr-pipeline/
├── CLAUDE.md                             ← you are here
├── README.md
├── .env                                  ← real credentials — gitignored, never commit
├── .env.example                          ← placeholder values — committed
├── requirements.txt
├── data_generation/
│   │
│   │   SOURCE GENERATORS (v2) — run first, produce source-native CSVs
│   ├── generate_windchill_source.py      ← Windchill-native schema (ISO dates, MECH codes, A/B/C revisions)
│   ├── generate_sharepoint_source.py     ← SharePoint-native schema (MM/DD/YYYY, verbose depts, 1.0/2.0 versions)
│   ├── generate_aveva_source.py          ← Aveva-native schema (DD.MM.YYYY, I&C, numeric revisions 0/1/2)
│   ├── windchill_source.csv              ← generated output (30 rows, source-native)
│   ├── sharepoint_source.csv             ← generated output (20 rows, source-native)
│   ├── aveva_source.csv                  ← generated output (10 rows, source-native)
│   │
│   │   STAGED LAYER (v2) — reads the three source CSVs, harmonises, detects DQ issues
│   ├── generate_staged_layer.py          ← harmonisation engine
│   ├── staged_events.csv                 ← event log (~471 rows, canonical schema)
│   ├── staged_cross_reference.csv        ← source_id -> mdr_id mapping (golden record table)
│   ├── staged_dq_flags.csv               ← data quality issues flagged at pipeline time
│   │
│   │   ANALYTICAL LAYER — reads staged outputs
│   ├── generate_mdr_layer.py             ← builds MDR register from harmonised data
│   ├── mdr_requirements.csv              ← source of truth for dashboard edits (60 rows)
│   │
│   │   LOAD
│   ├── load_to_snowflake.py              ← pushes all CSVs to Snowflake
│   │
│   │   LEGACY (v1) — kept for reference, superseded by v2 source generators
│   └── generate_raw_layer.py             ← LEGACY: monolithic generator, single schema
│
├── dashboard/
│   ├── app.py                            ← Streamlit dashboard (main file)
│   ├── edit_log.csv                      ← audit trail — append only, never delete
│   ├── bookmarks.csv                     ← per-user watchlist
│   └── saved_views.json                  ← named saved views
└── sql/                                  ← SQL reference scripts (not executed by app)
```

---

## Architecture design decisions (v2 — established 2026-05-10)

These decisions were stress-tested in a structured design review. Do not change them without good reason.

### Pipeline architecture

The pipeline has three distinct layers with deliberate separation of concerns:

```
SOURCE SYSTEMS          STAGED (harmonisation)        ANALYTICAL
windchill_source.csv  ──┐
sharepoint_source.csv ──┤── generate_staged_layer.py ──► staged_events.csv
aveva_source.csv      ──┘                              ► staged_cross_reference.csv
                                                       ► staged_dq_flags.csv
                                                              │
                                                      generate_mdr_layer.py
                                                              │
                                                       mdr_requirements.csv
```

**Key principle:** Data quality detection is a pipeline responsibility, not a dashboard responsibility.
By the time a user sees a record, it is already classified, flagged, and queued. The dashboard
is a remediation tool, not an inspection tool.

### Source system schemas — intentional differences

Each source system produces source-native field names and formats. These differences are the
pipeline problem the STAGED layer exists to solve.

| Property | Windchill | SharePoint | Aveva |
|---|---|---|---|
| Document ID field | `wc_doc_id` | `sp_item_id` | `aveva_id` |
| Status field | `wc_lifecycle_state` | `sp_status` | `aveva_document_status` |
| Revision field | `wc_revision` (A/B/C) | `sp_version` (1.0/2.0) | `aveva_revision_no` (0/1/2) |
| Date format | ISO 8601 (2025-01-15T09:23Z) | MM/DD/YYYY (01/15/2025) | DD.MM.YYYY (15.01.2025) |
| Discipline field | `wc_discipline_code` (MECH/ELEC) | `sp_department` (Mechanical Engineering) | `aveva_discipline` (I&C) |
| Confidentiality vocab | Internal/Restricted/Confidential | Public/Internal Use Only/Confidential | Unrestricted/Restricted/Confidential/Highly Confidential |

### Injected data quality issues (deliberate, for demo)

| Issue | Source | Field | DQ Flag type |
|---|---|---|---|
| Missing author (~3 records) | Windchill | `wc_author` | MISSING_MANDATORY_FIELD |
| Non-standard discipline code (~2 records) | Windchill | `wc_discipline_code` = "MECHANICAL" | NORMALISATION_REQUIRED |
| Missing created_by (~2 records) | SharePoint | `sp_created_by` | MISSING_MANDATORY_FIELD |
| Missing company (~2 records) | SharePoint | `sp_company` | MISSING_MANDATORY_FIELD |
| Missing prepared_by (~1 record) | Aveva | `aveva_prepared_by` | MISSING_MANDATORY_FIELD |
| I&C discipline code (~all Aveva Instrumentation) | Aveva | `aveva_discipline` = "I&C" | NORMALISATION_REQUIRED |
| Cross-system duplicates (~2 pairs) | WC + SP | same document, different IDs | DUPLICATE_CANDIDATE |

### Canonical ID — ISO 19650 construction rule

Format: `{PROJECT}-{ORIGINATOR}-{VOLUME}-{TYPE}-{DISCIPLINE}-{SEQUENCE}`
Example: `PROJ1-ALPHAENG-ZZ-DR-ME-000042`

**Rules:**
- Assigned ONCE in the STAGED layer. Never changes.
- Revision, status, and confidentiality are ATTRIBUTES on the event record — not part of the ID.
- The cross-reference table (`staged_cross_reference.csv`) maps `(source_system, source_native_id) → mdr_id`.
- If a source system renumbers its documents, update the cross-reference. The canonical ID is immutable.

### Event types in STAGED

Two distinct event types, written by two distinct roles:

| Event type | Written by | Trigger |
|---|---|---|
| `PM_UPDATE` | Project Manager | Edit to priority / critical path / percent complete / notes |
| `DQ_REMEDIATION` | Document Controller | Fill missing field / confirm duplicate / normalisation override |

Both are immutable appends. No record is ever overwritten. Full audit trail always recoverable.

### Dashboard roles

Three-state radio selector in sidebar (no credentials required — demo-friendly):

| Role | Can do |
|---|---|
| Read Only (default) | View all pages, no edits |
| Project Manager | Edit priority, critical path, percent complete, notes → writes PM_UPDATE event |
| Document Controller | Resolve DQ flags in remediation queue → writes DQ_REMEDIATION event |

### Source System Health page (purpose)

Per source system, the page shows:
- Record count and last ingestion timestamp
- DQ issue count by type (missing field / normalisation / duplicate candidate / timestamp parse error)
- RAG status per source system
- Drilldown table of flagged records awaiting DC resolution

---

## Dashboard build status

| Page | Status | Notes |
|---|---|---|
| Overview | ✅ Complete | RAG tiles, trend summary, critical path table, gatekeeper heatmap, discipline summary |
| MDR Register | ✅ Complete | Filters, column selector, sort, editable table, bookmark toggle, Excel export, saved views |
| My Watchlist | ✅ Complete | Per-doc cards: title, RAG badge, discipline/priority/status/%, editable note, Remove button |
| Document Detail | ✅ Complete | Structured metadata sections + STAGED lifecycle timeline from staged_events.csv |
| Source System Health | ✅ Complete | Per-source RAG tiles, unresolved flag drilldown, DC mark-resolved with audit log |

## Pipeline build status (v2)

| Component | Status | Notes |
|---|---|---|
| `generate_windchill_source.py` | ✅ Complete | 30 rows, source-native schema, 5 DQ issues injected |
| `generate_sharepoint_source.py` | ✅ Complete | 20 rows, source-native schema, 4 DQ issues injected |
| `generate_aveva_source.py` | ✅ Complete | 10 rows, source-native schema, 1 DQ issue injected |
| `generate_staged_layer.py` | ✅ Complete | Harmonises all 3 sources, detects 61 DQ flags, writes 4 output files |
| `generate_mdr_layer.py` | ✅ Unchanged | Still works — reads new raw_documents.csv without modification |
| Role selector (dashboard) | ✅ Complete | st.radio in sidebar: Read Only / Project Manager / Document Controller |
| Source System Health page | ✅ Complete | Per-source RAG tiles, DC remediation queue, mark-resolved with audit log |

## Code architecture — key module-level constants (app.py)

These are defined once near the top of `dashboard/app.py` and must be used everywhere
in place of raw string literals:

| Constant | Value | Purpose |
|---|---|---|
| `ROLE_READ_ONLY` | `"Read Only"` | Role gate — view only |
| `ROLE_PROJECT_MANAGER` | `"Project Manager"` | Role gate — PM edits |
| `ROLE_DOCUMENT_CONTROLLER` | `"Document Controller"` | Role gate — DC remediation |
| `ROLES` | list of the three above | Passed directly to `st.radio()` |
| `RAG_COLOR_MAP` | `{"RED": "#ef4444", ...}` | Single source of truth for RAG hex colours |

**Key shared helpers:**

| Helper | Purpose |
|---|---|
| `save_csv(df, path, label, date_cols)` | Generic CSV write with logging and error display. Used by `save_mdr()` and `save_dq_flags()` |
| `save_mdr(df)` | Thin wrapper — calls `save_csv` with MDR date columns |
| `save_dq_flags(dq)` | Thin wrapper — calls `save_csv` with no date columns |
| `log_edit(user, mdr_id, field, old, new)` | Audit trail append — used for both PM_UPDATE and DQ_REMEDIATION events |
| `_log(label, msg)` | Terminal lifecycle log — every key event must call this |

## Next session — start here

**Remaining stub pages:**
- My Watchlist — DONE (Day 7). Cards with title, RAG badge, discipline/priority/status/%, editable note, Remove button.
- Document Detail — DONE (Day 7). Structured metadata sections + STAGED lifecycle timeline joined via fulfilled_by_document_id -> document_id.

**Planned: Transformation log (next pipeline feature)**

Currently `generate_staged_layer.py` makes two kinds of changes:
- HIGH confidence (silent): date normalisation, clean discipline mapping, ID assignment — these happen with no audit trail
- LOW confidence / broken data: DQ flags (already written to staged_dq_flags.csv)

The gap: silent transformations leave no footprint. To close this, add a `staged_transformation_log.csv`
output to `generate_staged_layer.py`. One row per field changed, columns:
  source_system | source_native_id | mdr_id | field_name | original_value | normalised_value | normalisation_rule | confidence

Then add a "Pipeline Run Report" tab to the Source System Health page that reads this file and
shows the full transformation history. DQ flags = human action required. Transformation log = FYI only.
This demonstrates the difference between *automated* and *auditable* — strong interview talking point.

**Architecture note — DQ_REMEDIATION audit trail:**
When a DC marks a flag resolved, the event is written to `dashboard/edit_log.csv`
(the dashboard audit trail), NOT to `staged_events.csv`. The staged_events.csv is the
pipeline's document lifecycle event log (status transitions generated at pipeline run time)
and must not be mixed with dashboard user actions. The log_edit() function handles
both PM_UPDATE and DQ_REMEDIATION records — distinguished by the `field` column value
(e.g. `dq_flag_resolved:DQF-0001`).

## Important field added in v2

`raw_documents.csv` now contains a `mdr_id` field (canonical ISO 19650 ID).
`generate_mdr_layer.py` ignores it (reads only named fields it needs).
The dashboard can use `mdr_id` as the stable display identifier for documents.

---

## How to run

### Generate data — v2 pipeline (run in this order)
```powershell
cd data_generation
python generate_windchill_source.py    # produces windchill_source.csv
python generate_sharepoint_source.py   # produces sharepoint_source.csv
python generate_aveva_source.py        # produces aveva_source.csv
python generate_staged_layer.py        # reads 3 sources, produces staged_events + cross_ref + dq_flags
python generate_mdr_layer.py           # reads staged outputs, produces mdr_requirements.csv
python load_to_snowflake.py            # pushes all CSVs to Snowflake
```

### Generate data — v1 legacy (original single-generator pipeline)
```powershell
cd data_generation
python generate_raw_layer.py           # LEGACY — kept for reference only
```

### Launch the dashboard
```powershell
# from project root
streamlit run dashboard/app.py
```

### Syntax-check without running (fast sanity check)
```powershell
.venv\Scripts\python.exe -c "import py_compile; py_compile.compile('dashboard/app.py', doraise=True); print('OK')"
```

### Enable verbose debug output
Add this line to your `.env` file:
```
DEBUG=true
```
The terminal will print extra detail on every lifecycle event.

---

## Working conventions — Claude must follow these in every session

### Explain before editing
- Always explain what you are about to change, which file, which function, and why — before touching any file.
- If there is a trade-off, name it.
- The user is learning to code. Explanations are not optional.

### Comments
- Comment liberally. Every function gets a docstring (summary + Args + Returns where non-trivial).
- Every non-obvious block of code gets an inline comment explaining the *why*, not just the what.
- Do not omit comments to save space. The user is learning — comments are the teaching mechanism.

### Print statements
- Every key lifecycle event must print to the terminal using the `_log()` helper:
  `[HH:MM:SS] [LABEL] message`
- Labels in use:
  - `[LOAD]`      — reading data from disk into a DataFrame
  - `[SAVE]`      — writing a DataFrame back to disk
  - `[EDIT]`      — a user changed a field in the dashboard
  - `[SNOWFLAKE]` — a query was sent to Snowflake
  - `[FILTER]`    — a filter was applied to reduce the MDR rows
  - `[BOOKMARK]`  — a watchlist entry was added or removed
  - `[VIEW]`      — a saved view was loaded or written
  - `[ERROR]`     — something went wrong
- Key events that must always log: data load, data save, any user edit, any Snowflake query,
  filter application, bookmark change.
- Extra verbose output (data shapes, column lists, sample rows) goes behind `if DEBUG:`.

### Unicode / Windows terminal
- **Never use Unicode box-drawing or symbol characters in `print()` statements.**
  Characters like `✓`, `→`, `←`, `─`, `──`, `█`, `−` cause a `UnicodeEncodeError`
  on Windows PowerShell (cp1252 encoding) and crash the script.
- Use plain ASCII equivalents instead: `OK:` not `✓`, `->` not `→`, `-` not `─`, `#` not `█`.
- This applies to all scripts in `data_generation/` and any new scripts added to this project.
- If you see a `UnicodeEncodeError` in a print statement, this is always the cause.

### Lessons learned — when to update this file
- Any time we hit a systematic error (encoding, path, venv, tool limitation), add it here.
- The test: "would a future Claude session reproduce this mistake without this note?" If yes, write it down.

### Error handling
- Every error path must print `[ERROR]` with: what was attempted, what failed, what to do next.
- Never silently swallow an exception with a bare `except: pass` or `except Exception: return None`.
- In the UI, always show `st.error()` or `st.warning()` — the terminal log alone is not enough.

### Docstrings
- Every function gets a docstring: one-line summary, then Args and Returns if the function
  takes inputs or returns something non-trivial.

---

## Debug workflow (step by step)

1. Open the VSCode terminal (PowerShell)
2. Run: `streamlit run dashboard/app.py`
3. Watch the terminal — the `[LABEL]` prints tell you exactly what the app is doing
4. Add `DEBUG=true` to `.env` and restart to get verbose output
5. To test a single function without running the whole app:
   ```powershell
   .venv\Scripts\python.exe -c "
   import sys; sys.path.insert(0, '.')
   from dashboard.app import load_mdr
   df = load_mdr()
   print(df.shape)
   "
   ```

---

## Data model quick reference

| Layer | Table / File | Rows | Where |
|---|---|---|---|
| SOURCE | `windchill_source.csv` | 30 | CSV only |
| SOURCE | `sharepoint_source.csv` | 20 | CSV only |
| SOURCE | `aveva_source.csv` | 10 | CSV only |
| STAGED | `STAGED.EVENTS` / `staged_events.csv` | ~471 | Snowflake + CSV |
| STAGED | `staged_cross_reference.csv` | 60 | CSV only |
| STAGED | `staged_dq_flags.csv` | ~15 | CSV only |
| ANALYTICAL | `ANALYTICAL.MDR_REQUIREMENTS` / `mdr_requirements.csv` | 60 | Snowflake + CSV |

**Source of truth for dashboard edits:** `data_generation/mdr_requirements.csv`

**Golden record table:** `data_generation/staged_cross_reference.csv`
Maps `(source_system, source_native_id) → mdr_id`. Never deleted. Used for full lineage tracing.

**DQ flags table:** `data_generation/staged_dq_flags.csv`
Populated at pipeline run time. Read by Source System Health page. Resolved by Document Controller.

### Editable fields in the dashboard
| Field                      | Widget         | Effect                          |
|----------------------------|----------------|---------------------------------|
| `is_on_critical_path`      | Checkbox       | Written to CSV + audit log      |
| `priority`                 | Dropdown       | Written to CSV + audit log      |
| `reported_percent_complete`| Number (0–100) | Written to CSV + audit log      |
| `notes`                    | Text           | Written to CSV + audit log      |

### RAG thresholds (float × priority)
| Priority  | AMBER ≤ | RED ≤ |
|-----------|---------|-------|
| Very High | 21d     | 7d    |
| High      | 14d     | 3d    |
| Medium    | 7d      | 0d    |
| Low       | 7d      | -180d |

---

## Snowflake
- **Database:** `WINDCHILL_MDR`
- **Schemas:** `RAW`, `STAGED`, `ANALYTICAL`
- **Credentials:** in `.env` — never commit this file
- **Dashboard usage:** Snowflake is read-only for RAW + STAGED display.
  ANALYTICAL edits write to the local CSV, not back to Snowflake.

---

## Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `Cannot perform CREATE SCHEMA` | Wrong database name in `.env` | Check `SNOWFLAKE_DATABASE` matches Snowflake exactly |
| `MDR CSV not found` | Scripts not run yet | Run `generate_mdr_layer.py` first |
| Streamlit shows blank page | Syntax error in app.py | Run the py_compile check |
| Filter shows 0 rows | Filters combined too aggressively | Reset all filters to "All" |
| `UnicodeEncodeError: 'charmap' codec can't encode character` | Unicode symbol in a `print()` statement — Windows PowerShell uses cp1252 encoding which doesn't support `✓`, `→`, `─`, `█` etc. | Replace with ASCII: `OK:`, `->`, `-`, `#`. Never use Unicode symbols in print statements. |
