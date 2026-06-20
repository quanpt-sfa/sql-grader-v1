# SQL Server Schema & View Checker

A lightweight, offline-first, deterministic SQL Server assignment checker. 

## Features
1. **Structure Snapshot**: Extracts a snapshot of the database structure (tables, columns, primary keys, foreign keys, views, view columns) into CSV files.
2. **Structure Comparison**: Compares a student database structure snapshot against an instructor's answer snapshot using mapping rules (exact, alias, and fuzzy matching).
3. **View Behavioral Test**: Seeds test data into copies of the answer and student databases, runs views, canonicalizes the outputs (types, sorting, columns), and performs multiset comparisons (catching duplicate rows).

---

## CLI Commands

### 1. Extract Snapshot
```powershell
dbcheck snapshot \
  --answer-db 00000001 \
  --submissions "D:\exam\baks" \
  --run-dir "D:\exam\runs\run001" \
  --config "configs\assignment.yaml"
```
Or if using an answer backup file:
```powershell
dbcheck snapshot \
  --answer-bak "D:\exam\dapan.bak" \
  --submissions "D:\exam\baks" \
  --run-dir "D:\exam\runs\run001" \
  --config "configs\assignment.yaml"
```

### 2. Compare Structure
```powershell
dbcheck compare-structure \
  --run-dir "D:\exam\runs\run001" \
  --config "configs\assignment.yaml"
```

### 3. Test Views
```powershell
dbcheck test-views \
  --run-dir "D:\exam\runs\run001" \
  --config "configs\assignment.yaml"
```
*(Note: `--test-data` is optional and only required when using compare_seeded_test_data mode)*

### 4. Export Results
```powershell
dbcheck export-results \
  --run-dir "D:\exam\runs\run001" \
  --config "configs\assignment.yaml" \
  --format xlsx
```

---

## Backend & Grading Semantics Upgrades

### 1. View Testing - Compare Existing Data
In `compare_existing_data` mode (default), the checker compares student view queries against answer view queries directly on the restored student and answer databases using their existing data. No seeding is performed.

### 2. Key Adequacy Grading
Supports natural key and surrogate key designs. If a student uses a surrogate key, it is accepted if business key evidence (presence and uniqueness) is found. Implied relationships and incorrect targets are flagged for manual review.

### 3. Suggested Status Recommendations
A final suggested status is resolved for each submission based on prioritized rules:
- `FAIL_RESTORE_OR_SNAPSHOT`: Database restore or introspection failed.
- `FAIL_STRUCTURE`: Hard structural failures exist (e.g. missing tables, invalid PK/FK).
- `FAIL_VIEW`: View behaviors mismatch.
- `FAIL_DATA`: Row count differences exist.
- `NEEDS_REVIEW`: Manual review recommended (e.g. implied FKs, warning statuses).
- `PASS_WITH_WARNINGS`: Student database passes but with warnings (e.g. extra columns).
- `PASS`: Complete pass.

### 4. Aggregated Results
- `summary.xlsx` / `summary.csv`: Centralized metric dashboard and recommendations.
- `review_queue.xlsx` / `review_queue.csv`: Filterable list of items requiring manual verification.
- `hard_errors.csv`: Critical failures.
- `student_feedback/<submission_id>.md`: Detailed student-facing markdown reports.

---

## GUI App & Pipeline Semantics

### 1. Launching the GUI
To run the interactive grader GUI:
```powershell
$env:PYTHONPATH="src"; python src/dbcheck/gui/app.py
```

### 2. Full Pipeline Workflow
When you click **Run Full Pipeline**, the GUI executes exactly this 4-step sequence:
1. **snapshot**: Restore and snapshot answer and student backups.
2. **compare-structure**: Match tables, columns, PKs, and FKs.
3. **test-views**: Query student views and compare outputs with answer views.
4. **export-results**: Aggregate and compile final spreadsheet reports.

The pipeline runs in a background thread to keep the GUI responsive. It will only halt on command-level failure (e.g., database connection issues or Python crash), not on per-submission grading errors.

### 3. Metric Dashboard & Report Refreshing
- **Metrics Dashboard & Reloading**: Centralized metrics (Suggested Status distribution, Manual Review counts, Hard Errors) are only computed and made available after `export-results` has completed successfully.
- **Premature N/A Protection**: Before the export-results stage completes, the dashboard shows `"Export results not generated yet. Run Export Results or Full Pipeline."` to prevent premature display of incomplete data.
- **Refresh Results**: You can click the **Refresh Results** button to reload the spreadsheet outputs (`summary.csv`, `review_queue.csv`, `hard_errors.csv`) manually from disk without running any commands.

### 4. Status Fields Explained
- **manifest_status**: Indicates whether the student's database backup was successfully restored and snapshotted. Statuses are `OK` or `ERROR`.
- **suggested_status**: The final grading recommendation resolved after structure and view behavioral testing (e.g., `PASS`, `NEEDS_REVIEW`, `FAIL_STRUCTURE`, `FAIL_VIEW`).

### 5. Output Paths
All compiled files (`summary.xlsx`, `summary.csv`, `review_queue.xlsx`, `review_queue.csv`, `hard_errors.csv`, and `execution.log`) are written directly to the active Run Directory (e.g., `runs/run_<timestamp>/`). Student feedback reports are located in `runs/run_<timestamp>/student_feedback/`.

