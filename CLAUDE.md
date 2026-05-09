# CLAUDE.md — multisource-cde-mdr-pipeline

Read this file at the start of every session. It defines how we work together on this project.

---

## What this project is

A mock CDE/MDR (Common Data Environment / Master Document Register) pipeline built for
job application portfolio purposes. It simulates a real engineering project (PROJ1) with
60 documents tracked across three source systems (Windchill, SharePoint, Aveva), staged
through a data pipeline, and surfaced in a Streamlit dashboard.

**Primary audience:** Recruitment / technical interviews at Siemens Energy, Accenture, CE-RISE.

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
├── CLAUDE.md                          ← you are here
├── README.md
├── .env                               ← real credentials — gitignored, never commit
├── .env.example                       ← placeholder values — committed
├── requirements.txt
├── data_generation/
│   ├── generate_raw_layer.py          ← Day 2: creates raw_documents.csv
│   ├── generate_staged_layer.py       ← Day 2: creates staged_events.csv
│   ├── generate_mdr_layer.py          ← Day 2: creates mdr_requirements.csv
│   ├── load_to_snowflake.py           ← Day 2: pushes all CSVs to Snowflake
│   ├── raw_documents.csv              ← generated output (60 rows)
│   ├── staged_events.csv              ← generated output (~471 rows)
│   └── mdr_requirements.csv          ← source of truth for dashboard edits
├── dashboard/
│   ├── app.py                         ← Streamlit dashboard (main file)
│   ├── edit_log.csv                   ← audit trail — append only, never delete
│   ├── bookmarks.csv                  ← per-user watchlist
│   └── saved_views.json               ← named saved views
└── sql/                               ← SQL reference scripts (not executed by app)
```

---

## Dashboard build status

| Page | Status | Notes |
|---|---|---|
| Overview | ✅ Complete | RAG tiles, trend summary, critical path table, gatekeeper heatmap, discipline summary |
| MDR Register | ✅ Complete | Filters, column selector, sort, editable table, bookmark toggle, Excel export, saved views |
| My Watchlist | 🔲 Stub | Shows bookmarks df only — full build pending |
| Document Detail | 🔲 Stub | Shows JSON row only — STAGED timeline pending |
| Source System Health | 🔲 Stub | Placeholder only |

---

## How to run

### Generate data (run once, in this order)
```powershell
cd data_generation
python generate_raw_layer.py
python generate_staged_layer.py
python generate_mdr_layer.py
python load_to_snowflake.py
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

| Layer       | Table / File                  | Rows  | Where          |
|-------------|-------------------------------|-------|----------------|
| RAW         | `RAW.DOCUMENTS`               | 60    | Snowflake       |
| STAGED      | `STAGED.EVENTS`               | ~471  | Snowflake       |
| ANALYTICAL  | `ANALYTICAL.MDR_REQUIREMENTS` | 60    | Snowflake + CSV |

**Source of truth for dashboard edits:** `data_generation/mdr_requirements.csv`

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
