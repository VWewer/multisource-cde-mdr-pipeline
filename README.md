# multisource-cde-mdr-pipeline

A mock CDE/MDR (Common Data Environment / Master Document Register) pipeline built as a portfolio project. It simulates a real engineering challenge: 60 documents tracked across three source systems that do not share schemas, naming conventions, or date formats. The pipeline harmonises them into a single canonical register and surfaces the result in an interactive Streamlit dashboard backed by Snowflake.

---

## The problem this solves

Large CAPEX projects run across multiple document systems — Windchill, SharePoint, Aveva — that do not talk to each other. Missing traceability and data ambiguity cause delays and cost overruns that are entirely preventable. This pipeline simulates that problem end to end:

- Three source systems, each with distinct field names, date formats, revision schemes, and data quality issues
- A STAGED layer that harmonises all three into a single canonical schema (ISO 19650 document IDs), detects data quality issues, and writes a full event log
- An ANALYTICAL layer that builds the live MDR register from STAGED outputs
- A Streamlit dashboard that gives Document Controllers a DQ remediation queue and Project Managers delivery visibility

Every edit is written as an immutable event — full audit trail, no overwriting.

---

## Tech stack

| Layer        | Tool                                        |
|--------------|---------------------------------------------|
| Language     | Python 3.14                                 |
| Data gen     | `faker`, `pandas`                           |
| Database     | Snowflake (STAGED and ANALYTICAL schemas)   |
| DB connector | `snowflake-connector-python`                |
| Dashboard    | Streamlit                                   |
| Config       | `python-dotenv`                             |
| Environment  | Windows 11, `.venv`                         |

---

## Architecture

```
SOURCE SYSTEMS              STAGED (harmonisation)           ANALYTICAL
windchill_source.csv  ──┐
sharepoint_source.csv ──┤── generate_staged_layer.py ──► staged_events.csv        ──┐
aveva_source.csv      ──┘                              ► staged_cross_reference.csv   │
                                                       ► staged_dq_flags.csv          │
                                                                          generate_mdr_layer.py
                                                                                       │
                                                                          mdr_requirements.csv
                                                                                       │
                                                                          load_to_snowflake.py
                                                                                       │
                                              ┌────────────────────────────────────────┤
                                              │  Snowflake: MULTISOURCE_CDE_MDR_PIPELINE database     │
                                              │  RAW.WINDCHILL_DOCUMENTS  (30 rows)    │
                                              │  RAW.SHAREPOINT_DOCUMENTS (20 rows)    │
                                              │  RAW.AVEVA_DOCUMENTS      (10 rows)    │
                                              │  STAGED.EVENTS            (~510 rows)  │
                                              │  ANALYTICAL.MDR_REQUIREMENTS (60 rows) │
                                              └────────────────────────────────────────┘
```

The RAW layer holds each source system's data in its native schema — field names, date formats, and revision schemes are preserved exactly as they arrived. The STAGED layer harmonises all three into a single canonical schema. The Streamlit dashboard reads from STAGED and ANALYTICAL. All user edits (PM updates, DQ remediations) are written locally as append-only event records — full audit trail always recoverable.

---

## Dashboard pages

| Page | Purpose |
|---|---|
| Overview | RAG status tiles, critical path panel, delivery trend, pipeline flags summary, watchlist strip |
| MDR | Full register with filters, column selector, inline edits (PM role), Excel export, saved views |
| My Watchlist | Per-doc cards for bookmarked documents |
| Document Detail | Structured metadata + STAGED lifecycle timeline |
| Source System Health | DC remediation queue, pipeline run report, resolution audit trail |

Three roles (no credentials required — demo-friendly): Read Only, Project Manager, Document Controller.

---

## Snowflake integration

- **Database:** `MULTISOURCE_CDE_MDR_PIPELINE`
- **Schemas:** `RAW`, `STAGED`, `ANALYTICAL`
- **RAW tables** — source-native schemas, one per system:
  - `RAW.WINDCHILL_DOCUMENTS` — `wc_*` field names, ISO 8601 dates, A/B/C revisions
  - `RAW.SHAREPOINT_DOCUMENTS` — `sp_*` field names, MM/DD/YYYY dates, 1.0/2.0 versions
  - `RAW.AVEVA_DOCUMENTS` — `aveva_*` field names, DD.MM.YYYY dates, numeric revisions
- **STAGED.EVENTS** — harmonised event log (~510 rows, canonical ISO 19650 IDs)
- **ANALYTICAL.MDR_REQUIREMENTS** — MDR register read by the dashboard (60 rows)
- Credentials are stored in `.env` (see `.env.example`) — never committed

---

## How to run

### 1. Install dependencies
```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure credentials
```powershell
copy .env.example .env
# Fill in your Snowflake credentials in .env
```

### 3. Generate data (run in order)
```powershell
cd data_generation
python generate_windchill_source.py
python generate_sharepoint_source.py
python generate_aveva_source.py
python generate_staged_layer.py
python generate_mdr_layer.py
python load_to_snowflake.py
```

### 4. Launch the dashboard
```powershell
# from project root
streamlit run dashboard/app.py
```

---

## Folder structure

```
multisource-cde-mdr-pipeline/
├── data_generation/       # Pipeline scripts and generated CSVs
├── dashboard/             # Streamlit app (app.py) + local audit files
├── config/                # Config helpers
├── sql/                   # SQL reference scripts
├── .env.example           # Credential template
└── requirements.txt
```
