# SQL Server Grader GUI Usage Guide

This guide explains how to use the graphical user interface (GUI) to run snapshot extraction, schema comparisons, and view tests for SQL Server student assignments.

---

## Launching the GUI

To launch the application, run the launcher script from the root of the repository:

```bash
python run_gui.py
```

This will open the main Tkinter window containing all settings, logs, and preview tables.

---

## Required Inputs

1. **Answer Backup (.bak)**:
   - Path to the backup file of the instructor's solution database.
   - Example: `solution/dapan.bak`
   - Use the **Browse...** button to select.

2. **Submissions Folder**:
   - The folder containing student backup files (each ending in `.bak`).
   - Example: `exams/`

3. **Config (.yaml)**:
   - The YAML configuration file containing assignment details, table-scoped/global aliases, and expected view outputs.
   - Example: `configs/assignment.yaml`

4. **Test Data Folder**:
   - Folder containing seed CSV files to populate temporary test databases.
   - Example: `test_data/`

5. **Run Directory**:
   - The folder where snapshots, logs, and report CSV files will be saved for this grading run.
   - Default: `runs/run_YYYYMMDD_HHMMSS` (generated dynamically).
   - You can edit this field manually or generate a fresh timestamp name by clicking the **🔄 Refresh Name** button.

---

## SQL Server Settings

Configure these options to match your local SQL Server instance:

* **Server Name**: The name or IP address of your SQL Server instance (default is `.`, representing the local default instance).
* **Driver**: Select the best driver from the dropdown. If `pyodbc` cannot query the driver catalog, the dropdown remains editable so you can type it manually (e.g. `ODBC Driver 17 for SQL Server`).
* **Authentication**: 
  - **Windows Authentication**: Uses current Windows user credentials (default).
  - **SQL Server Authentication**: Enables username and password input fields.
* **Username / Password**: Credentials for SQL Server login (e.g. `sa`). Passwords are masked with `*` in the UI and never printed to the logs.
* **Trust Server Certificate**: Check this (adds `TrustServerCertificate=yes;Encrypt=no`) if your local SQL Server instance does not have a trusted SSL certificate installed (common for local Developer/Express instances).

---

## Recommended Workflow

### 1. Run Full Pipeline
Click **Run Full Pipeline** to execute the entire grading pipeline sequentially:
- **Snapshot Extraction**: Restores the answer backup and all student backups to temporary databases, extracts their schemas, and outputs snapshot JSONs to `runs/<run_id>/submissions/<student_id>/snapshot/`.
- **Compare Structure**: Evaluates table and column mappings, normalizes names, applies role guards, and flags schema deviations.
- **Test Views**: Seeds databases with test data, executes views, and compares output results.

If any stage of the pipeline fails, the process halts immediately (fail-fast behavior).

### 2. Inspect Results
Once completed, the **Grading Summary Preview** table will automatically load the compiled results from `summary.csv`.
- Columns display metrics like `manifest_status`, missing/extra table counts, and view pass rates.
- The columns adapt dynamically to whatever data exists in `summary.csv`.

### 3. Review Mapping Reports
If a student has a status like `TABLE_AMBIGUOUS` or missing fields, you can select the student's row in the preview table and click **Open Mapping Reports**.
- This opens the reports folder containing `table_mapping_report.csv` and `column_mapping_report.csv`.
- Look for the `suggested_alias_entry` column in those CSVs. This gives you the exact YAML lines to copy into `configs/assignment.yaml` to resolve naming discrepancies.

### 4. Rerun Specific Steps
After editing `configs/assignment.yaml` to add missing aliases:
- Click **2. Compare Structure** to rerun name matching and update mapping reports.
- Click **3. Test Views** to re-evaluate view queries and generate updated results.
- You do **not** need to recreate snapshots, saving significant database restore time!

---

## Actions & Controls

- **Stop Current Process**: Safely terminates the running CLI command and cancels all remaining queued steps in the pipeline.
- **Open Run Folder**: Opens the current run directory in Windows Explorer.
- **Open Summary CSV**: Opens the aggregated `summary.csv` file using the default system editor (e.g. Excel).
- **Open Mapping Reports**: Opens the selected student's reports folder in Windows Explorer.
