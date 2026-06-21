import csv
import logging
from pathlib import Path
from typing import Dict, Any, List, Set
import pandas as pd

from dbcheck.utils.logging import get_logger

REVIEW_STATUSES = {
    "PK_REVIEW_REQUIRED", "FK_REVIEW_REQUIRED", "FK_IMPLIED_REVIEW_REQUIRED",
    "MAPPING_AMBIGUOUS", "VIEW_MAPPING_AMBIGUOUS", "VIEW_OUTPUT_SCHEMA_MISMATCH",
    "TYPE_WARNING", "IDENTIFIER_TYPE_WARNING", "COLUMN_UNMAPPED_STUDENT",
    "EXTRA_REVIEW", "SURROGATE_KEY_REVIEW", "VIEW_TEST_NOT_RUN",
    "TABLE_REVIEW_REQUIRED", "COLUMN_MATCHED_WEAK_ALIAS", "DUPLICATE_MAPPING_REVIEW",
    "FK_RELATIONSHIP_IMPLIED_REVIEW_REQUIRED", "FK_RELATIONSHIP_AMBIGUOUS", "FK_RELATIONSHIP_MAPPING_ERROR",
    "VIEW_OUTPUT_PARTIAL_MATCH", "VIEW_SQL_UNSAFE_REVIEW", "VIEW_SQL_REWRITE_AMBIGUOUS_COLUMN"
}

HARD_ERROR_STATUSES = {
    "MISSING", "PK_MISSING", "PK_INVALID", "FK_MISSING", "FK_WRONG_TARGET",
    "VIEW_NOT_FOUND", "VIEW_EXECUTION_ERROR", "VIEW_VALUE_MISMATCH",
    "VIEW_ROW_COUNT_MISMATCH", "ROW_COUNT_MISMATCH",
    "FK_RELATIONSHIP_MISSING", "FK_RELATIONSHIP_WRONG_PARENT", "FK_RELATIONSHIP_WRONG_CHILD",
    "FK_RELATIONSHIP_WRONG_CHILD_COLUMNS", "FK_RELATIONSHIP_WRONG_PARENT_COLUMNS",
    "VIEW_NO_MATCHING_OUTPUT", "VIEW_SQL_PARSE_ERROR", "VIEW_SQL_REWRITE_UNMAPPED_TABLE", "VIEW_SQL_REWRITE_UNMAPPED_COLUMN"
}

def get_suggested_action(status: str, component: str) -> str:
    status = status.upper().strip()
    component = component.lower().strip()
    if status == "MISSING":
        if component == "table":
            return "Create the missing table in the student database."
        elif component == "column":
            return "Create the missing column in the student table."
        return "Create the missing object in the student schema."
    elif status == "PK_MISSING":
        return "Define a primary key constraint on the student table."
    elif status == "PK_INVALID":
        return "Correct the primary key columns to match the expected design."
    elif status in ("FK_MISSING", "FK_RELATIONSHIP_MISSING"):
        return "Define a foreign key constraint to link the tables."
    elif status in ("FK_WRONG_TARGET", "FK_RELATIONSHIP_WRONG_PARENT", "FK_RELATIONSHIP_WRONG_CHILD", "FK_RELATIONSHIP_WRONG_CHILD_COLUMNS", "FK_RELATIONSHIP_WRONG_PARENT_COLUMNS"):
        return "Correct the referenced table or columns of the foreign key constraint."
    elif status == "VIEW_NOT_FOUND":
        return "Create the required view with the correct name and output schema."
    elif status == "VIEW_EXECUTION_ERROR":
        return "Fix compilation or execution errors in the view query."
    elif status == "VIEW_VALUE_MISMATCH":
        return "Correct the data selection or join conditions in the view query."
    elif status == "VIEW_ROW_COUNT_MISMATCH":
        return "Verify the filter conditions or joins in the view query."
    elif status == "VIEW_OUTPUT_PARTIAL_MATCH":
        return "Adjust view SELECT logic to correct output value/row count differences."
    elif status == "VIEW_SQL_UNSAFE_REVIEW":
        return "Manually inspect the view query logic for any safety or SQL validation issues."
    elif status == "VIEW_SQL_REWRITE_AMBIGUOUS_COLUMN":
        return "Ensure view query columns are clearly alias-qualified when resolving tables."
    elif status == "VIEW_NO_MATCHING_OUTPUT":
        return "Correct student query logic so that execution yields output matching answer data."
    elif status == "VIEW_SQL_PARSE_ERROR":
        return "Correct the view syntax or CREATE/ALTER VIEW wrapper definition."
    elif status == "VIEW_SQL_REWRITE_UNMAPPED_TABLE":
        return "Verify that table mappings exist for all table references in the view."
    elif status == "VIEW_SQL_REWRITE_UNMAPPED_COLUMN":
        return "Verify that column mappings exist for all column references in the view."
    elif status == "ROW_COUNT_MISMATCH":
        return "Ensure the student database was restored and seeded with correct data."
    elif status == "PK_REVIEW_REQUIRED":
        return "Verify if the student's surrogate or natural key design is valid."
    elif status == "FK_REVIEW_REQUIRED":
        return "Verify if the student's relationship columns are correct."
    elif status in ("FK_IMPLIED_REVIEW_REQUIRED", "FK_RELATIONSHIP_IMPLIED_REVIEW_REQUIRED"):
        return "Verify if this implied relationship is correct and declare it if needed."
    elif status in ("MAPPING_AMBIGUOUS", "VIEW_MAPPING_AMBIGUOUS", "FK_RELATIONSHIP_AMBIGUOUS"):
        return "Resolve name mapping ambiguity in config aliases."
    elif status == "FK_RELATIONSHIP_MAPPING_ERROR":
        return "Verify the tables/columns used in this relationship."
    elif status == "VIEW_OUTPUT_SCHEMA_MISMATCH":
        return "Adjust the output columns or type casting in the view."
    elif status in ("TYPE_WARNING", "IDENTIFIER_TYPE_WARNING"):
        return "Check if data type differences could cause runtime errors or overflow."
    elif status in ("COLUMN_UNMAPPED_STUDENT", "EXTRA_REVIEW"):
        return "Inspect unmapped/extra student column to ensure no expected attributes are lost."
    elif status == "SURROGATE_KEY_REVIEW":
        return "Verify surrogate key column implementation."
    elif status == "VIEW_TEST_NOT_RUN":
        return "Execute view testing command to obtain correct view outputs."
    elif status == "TABLE_REVIEW_REQUIRED":
        return "Verify if the student's weak table alias mapping is correct."
    elif status == "COLUMN_MATCHED_WEAK_ALIAS":
        return "Verify if the column mapping is correct despite the weak table alias match."
    elif status == "DUPLICATE_MAPPING_REVIEW":
        return "Resolve duplicate column mapping (best match accepted, others demoted)."
    return "Review report details and make any necessary adjustments."

def export_results(run_dir: Path, config: Any, output_format: str = "xlsx") -> None:
    logger = get_logger()
    manifest_path = run_dir / "manifest.csv"
    summary_path = run_dir / "summary.csv"
    
    if not manifest_path.exists():
        logger.error(f"manifest.csv is missing in: {run_dir}")
        return

    # 1. Read manifest
    submissions: List[Dict[str, Any]] = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            submissions.append(row)

    # 2. Read answer tables/views snap
    answer_tables_rc: Dict[str, int] = {}
    ans_tables_path = run_dir / "answer_snapshot" / "tables.csv"
    if ans_tables_path.exists():
        try:
            with open(ans_tables_path, "r", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    t_name = r.get("table_name")
                    rc = r.get("row_count")
                    if t_name and rc is not None:
                        answer_tables_rc[t_name] = int(rc)
        except Exception as e:
            logger.warning(f"Could not read answer tables snapshot: {e}")

    expected_views: List[str] = []
    ans_views_path = run_dir / "answer_snapshot" / "views.csv"
    if ans_views_path.exists():
        try:
            with open(ans_views_path, "r", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    v_name = r.get("view_name") or r.get("object_name")
                    if v_name:
                        expected_views.append(v_name)
        except Exception as e:
            logger.warning(f"Could not read answer views snapshot: {e}")

    # Aggregated lists
    all_review_queue: List[Dict[str, Any]] = []
    all_hard_errors: List[Dict[str, Any]] = []
    all_row_counts: List[Dict[str, Any]] = []
    all_pk_adequacy: List[Dict[str, Any]] = []
    all_fk_relationships: List[Dict[str, Any]] = []
    all_views: List[Dict[str, Any]] = []
    all_column_mapping_issues: List[Dict[str, Any]] = []

    # Map of sub_id to suggested metrics
    sub_metrics: Dict[str, Dict[str, Any]] = {}

    # 3. Process each submission
    for sub in submissions:
        sub_id = sub["submission_id"]
        manifest_status = sub["status"]
        
        hard_error_count = 0
        manual_review_count = 0
        warning_count = 0
        
        sub_review_items: List[Dict[str, Any]] = []
        sub_hard_errors: List[Dict[str, Any]] = []

        sub_dir = run_dir / "submissions" / sub_id
        reports_dir = sub_dir / "reports"
        snapshot_dir = sub_dir / "snapshot"

        if manifest_status != "OK":
            # Database restore/snapshot failed
            item = {
                "submission_id": sub_id,
                "source_report": "manifest.csv",
                "component": "database",
                "answer_object": "",
                "student_object": "",
                "status": "FAIL_RESTORE_OR_SNAPSHOT",
                "severity": "high",
                "message": f"Database restore or structure snapshot extraction failed: {sub.get('error_message', '')}",
                "evidence": sub.get("error_message", ""),
                "suggested_action": "Check SQL Server connection string, database file availability, or backup format."
            }
            sub_hard_errors.append(item)
            all_hard_errors.append(item)
            hard_error_count += 1
        else:
            # A. Read structure_report.csv
            struct_report = reports_dir / "structure_report.csv"
            if struct_report.exists():
                try:
                    with open(struct_report, "r", encoding="utf-8") as sf:
                        for row in csv.DictReader(sf):
                            status = row["status"]
                            severity = row.get("severity", "info")
                            comp = row["component"]
                            ans_obj = row.get("answer_object", "")
                            stud_obj = row.get("student_object", "")
                            msg = row.get("message", "")
                            ev = row.get("evidence", "")

                            item = {
                                "submission_id": sub_id,
                                "source_report": "structure_report.csv",
                                "component": comp,
                                "answer_object": ans_obj,
                                "student_object": stud_obj,
                                "status": status,
                                "severity": severity,
                                "message": msg,
                                "evidence": ev,
                                "suggested_action": get_suggested_action(status, comp)
                            }

                            if status in HARD_ERROR_STATUSES:
                                sub_hard_errors.append(item)
                                all_hard_errors.append(item)
                                hard_error_count += 1
                            elif status in REVIEW_STATUSES:
                                sub_review_items.append(item)
                                all_review_queue.append(item)
                                manual_review_count += 1
                            elif severity.lower() == "warning" or status == "EXTRA":
                                warning_count += 1
                except Exception as e:
                    logger.warning(f"Error reading structure_report for student '{sub_id}': {e}")

            # B. Read key_adequacy_report.csv
            key_report = reports_dir / "key_adequacy_report.csv"
            if key_report.exists():
                try:
                    with open(key_report, "r", encoding="utf-8") as kf:
                        for row in csv.DictReader(kf):
                            # Append to PK Adequacy Sheet
                            all_pk_adequacy.append({**row, "submission_id": sub_id})

                            status = row["key_status"]
                            severity = row.get("key_severity", "info")
                            table_name = row.get("table_name", "")
                            reason = row.get("key_reason", "")
                            ans_pk = row.get("answer_pk_columns", "")
                            stud_pk = row.get("student_pk_columns", "")
                            ans_bk = row.get("answer_business_key_columns", "")
                            stud_bk = row.get("student_business_key_columns", "")
                            ev = f"Ans PK: {ans_pk}, Stud PK: {stud_pk}, Ans BK: {ans_bk}, Stud BK: {stud_bk}"

                            item = {
                                "submission_id": sub_id,
                                "source_report": "key_adequacy_report.csv",
                                "component": "primary_key",
                                "answer_object": table_name,
                                "student_object": row.get("student_table", ""),
                                "status": status,
                                "severity": severity,
                                "message": reason,
                                "evidence": ev,
                                "suggested_action": get_suggested_action(status, "primary_key")
                            }

                            if status in HARD_ERROR_STATUSES:
                                sub_hard_errors.append(item)
                                all_hard_errors.append(item)
                                hard_error_count += 1
                            elif status in REVIEW_STATUSES:
                                sub_review_items.append(item)
                                all_review_queue.append(item)
                                manual_review_count += 1
                            elif severity.lower() == "warning":
                                warning_count += 1
                except Exception as e:
                    logger.warning(f"Error reading key_adequacy_report for student '{sub_id}': {e}")

            # C. Read fk_relationship_report.csv
            fk_report = reports_dir / "fk_relationship_report.csv"
            if fk_report.exists():
                try:
                    with open(fk_report, "r", encoding="utf-8") as ff:
                        for row in csv.DictReader(ff):
                            all_fk_relationships.append({**row, "submission_id": sub_id})

                            status = row["fk_status"]
                            severity = row.get("fk_severity", "info")
                            ans_child = row.get("answer_child_table", "")
                            ans_parent = row.get("answer_parent_table", "")
                            ans_cols = row.get("answer_child_columns", "")
                            stud_cols = row.get("student_child_columns", "")
                            ev = f"FK Name: {row.get('fk_name','')}, Ans cols: {ans_cols}, Stud cols: {stud_cols}"

                            item = {
                                "submission_id": sub_id,
                                "source_report": "fk_relationship_report.csv",
                                "component": "foreign_key",
                                "answer_object": f"{ans_child} -> {ans_parent} ({ans_cols})",
                                "student_object": f"{row.get('student_child_table','')} -> {row.get('student_parent_table','')} ({stud_cols})",
                                "status": status,
                                "severity": severity,
                                "message": row.get("fk_reason", ""),
                                "evidence": ev,
                                "suggested_action": get_suggested_action(status, "foreign_key")
                            }

                            if status in HARD_ERROR_STATUSES:
                                sub_hard_errors.append(item)
                                all_hard_errors.append(item)
                                hard_error_count += 1
                            elif status in REVIEW_STATUSES:
                                sub_review_items.append(item)
                                all_review_queue.append(item)
                                manual_review_count += 1
                            elif severity.lower() == "warning":
                                warning_count += 1
                except Exception as e:
                    logger.warning(f"Error reading fk_relationship_report for student '{sub_id}': {e}")

            # D. Read view_test_report.csv or handle missing views
            view_report_file = reports_dir / "view_test_report.csv"
            if view_report_file.exists():
                try:
                    with open(view_report_file, "r", encoding="utf-8") as vf:
                        for row in csv.DictReader(vf):
                            all_views.append({**row, "submission_id": sub_id})

                            status = row["status"]
                            ans_view = row.get("answer_view", "")
                            stud_view = row.get("student_view", "")
                            exec_err = row.get("execution_error", "")
                            ev = (
                                f"Ans row count: {row.get('row_count_answer','0')}, "
                                f"Stud row count: {row.get('row_count_student','0')}, "
                                f"Val mismatch: {row.get('value_mismatch_count','0')}"
                            )
                            if exec_err:
                                ev += f", Exec error: {exec_err}"

                            # Determine severity
                            severity = "high" if status in HARD_ERROR_STATUSES else "warning" if status in REVIEW_STATUSES else "info"

                            item = {
                                "submission_id": sub_id,
                                "source_report": "view_test_report.csv",
                                "component": "view",
                                "answer_object": ans_view,
                                "student_object": stud_view,
                                "status": status,
                                "severity": severity,
                                "message": f"View status '{status}' for '{ans_view}'.",
                                "evidence": ev,
                                "suggested_action": get_suggested_action(status, "view")
                            }

                            if status in HARD_ERROR_STATUSES:
                                sub_hard_errors.append(item)
                                all_hard_errors.append(item)
                                hard_error_count += 1
                            elif status in REVIEW_STATUSES:
                                sub_review_items.append(item)
                                all_review_queue.append(item)
                                manual_review_count += 1
                            elif status not in ("VIEW_PASS", "PASS"):
                                warning_count += 1
                except Exception as e:
                    logger.warning(f"Error reading view_test_report for student '{sub_id}': {e}")
            elif expected_views:
                # View testing has not run, add VIEW_TEST_NOT_RUN
                for eview in expected_views:
                    item = {
                        "submission_id": sub_id,
                        "source_report": "view_test_report.csv",
                        "component": "view",
                        "answer_object": eview,
                        "student_object": "",
                        "status": "VIEW_TEST_NOT_RUN",
                        "severity": "warning",
                        "message": f"View testing has not been run for view '{eview}'.",
                        "evidence": "view_test_report.csv is missing.",
                        "suggested_action": get_suggested_action("VIEW_TEST_NOT_RUN", "view")
                    }
                    sub_review_items.append(item)
                    all_review_queue.append(item)
                    manual_review_count += 1
                    all_views.append({
                        "submission_id": sub_id,
                        "answer_view": eview,
                        "student_view": "",
                        "status": "VIEW_TEST_NOT_RUN",
                        "matched_columns": "",
                        "missing_columns": "",
                        "extra_columns": "",
                        "row_count_answer": "0",
                        "row_count_student": "0",
                        "answer_minus_student_count": "0",
                        "student_minus_answer_count": "0",
                        "value_mismatch_count": "0",
                        "execution_error": ""
                    })

            # E. Read column_mapping_report.csv
            col_report = reports_dir / "column_mapping_report.csv"
            if col_report.exists():
                try:
                    with open(col_report, "r", encoding="utf-8") as cf:
                        for row in csv.DictReader(cf):
                            status = row.get("match_status", "")
                            review_req = row.get("review_required", "").lower() in ("true", "1")
                            
                            is_issue = review_req or status not in (
                                "COLUMN_MATCHED_EXACT", "COLUMN_MATCHED_ALIAS",
                                "COLUMN_MATCHED_ABBREVIATION", "COLUMN_MATCHED_FUZZY_HIGH",
                                "COLUMN_MATCHED_FUZZY_LOW", "COLUMN_MATCHED_FUZZY",
                                "SURROGATE_KEY_ACCEPTED", "SURROGATE_KEY_IGNORED"
                            )

                            if is_issue:
                                all_column_mapping_issues.append({**row, "submission_id": sub_id})
                except Exception as e:
                    logger.warning(f"Error reading column_mapping_report for student '{sub_id}': {e}")

            # F. Gather Row Counts
            table_map_file = reports_dir / "table_mapping_report.csv"
            student_tables_file = snapshot_dir / "tables.csv"
            if table_map_file.exists() and student_tables_file.exists():
                try:
                    student_tables_rc = {}
                    with open(student_tables_file, "r", encoding="utf-8") as f:
                        for r in csv.DictReader(f):
                            tab = r.get("table_name")
                            rc = r.get("row_count")
                            if tab and rc is not None:
                                student_tables_rc[tab] = int(rc)

                    accepted_table_statuses = {
                        "TABLE_MATCHED_EXACT", "TABLE_MATCHED_ALIAS",
                        "TABLE_MATCHED_ABBREVIATION", "TABLE_MATCHED_FUZZY_HIGH",
                        "TABLE_MATCHED_FUZZY_LOW"
                    }

                    with open(table_map_file, "r", encoding="utf-8") as f:
                        for row in csv.DictReader(f):
                            status = row["match_status"]
                            if status in accepted_table_statuses:
                                ans_t = row["answer_table"]
                                stud_t = row["student_table"]
                                ans_rc = answer_tables_rc.get(ans_t, 0)
                                stud_rc = student_tables_rc.get(stud_t, 0)
                                diff = stud_rc - ans_rc
                                all_row_counts.append({
                                    "submission_id": sub_id,
                                    "table_name": ans_t,
                                    "student_table": stud_t,
                                    "answer_row_count": ans_rc,
                                    "student_row_count": stud_rc,
                                    "difference": diff,
                                    "status": "PASS" if diff == 0 else "ROW_COUNT_MISMATCH"
                                })
                except Exception as e:
                    logger.warning(f"Error parsing row counts for student '{sub_id}': {e}")

        # 4. Resolve Suggested Status per priority
        # FAIL_RESTORE_OR_SNAPSHOT > FAIL_STRUCTURE > FAIL_VIEW > FAIL_DATA > NEEDS_REVIEW > PASS_WITH_WARNINGS > PASS
        if manifest_status != "OK":
            suggested_status = "FAIL_RESTORE_OR_SNAPSHOT"
        elif any(item["status"] in ("MISSING", "PK_MISSING", "PK_INVALID", "FK_MISSING", "FK_WRONG_TARGET") for item in sub_hard_errors):
            suggested_status = "FAIL_STRUCTURE"
        elif any(item["status"] in ("VIEW_NOT_FOUND", "VIEW_EXECUTION_ERROR", "VIEW_VALUE_MISMATCH", "VIEW_ROW_COUNT_MISMATCH") for item in sub_hard_errors):
            suggested_status = "FAIL_VIEW"
        elif any(item["status"] == "ROW_COUNT_MISMATCH" for item in sub_hard_errors):
            suggested_status = "FAIL_DATA"
        elif manual_review_count > 0:
            suggested_status = "NEEDS_REVIEW"
        elif warning_count > 0:
            suggested_status = "PASS_WITH_WARNINGS"
        else:
            suggested_status = "PASS"

        sub_metrics[sub_id] = {
            "hard_error_count": hard_error_count,
            "manual_review_count": manual_review_count,
            "warning_count": warning_count,
            "suggested_status": suggested_status,
            "hard_errors_list": sub_hard_errors,
            "review_queue_list": sub_review_items
        }

    # 5. Load and update summary.csv / Summary Sheet
    final_summary_rows: List[Dict[str, Any]] = []
    summary_headers: List[str] = []
    if summary_path.exists():
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                summary_headers = list(reader.fieldnames or [])
                for row in reader:
                    sub_id = row["submission_id"]
                    metrics = sub_metrics.get(sub_id, {
                        "hard_error_count": 0,
                        "manual_review_count": 0,
                        "warning_count": 0,
                        "suggested_status": "FAIL_RESTORE_OR_SNAPSHOT" if row.get("manifest_status") != "OK" else "PASS"
                    })
                    # Insert new columns
                    row["hard_error_count"] = metrics["hard_error_count"]
                    row["manual_review_count"] = metrics["manual_review_count"]
                    row["warning_count"] = metrics["warning_count"]
                    row["suggested_status"] = metrics["suggested_status"]
                    final_summary_rows.append(row)
        except Exception as e:
            logger.error(f"Failed to read existing summary.csv: {e}")

    # Add new headers to summary
    for nh in ["hard_error_count", "manual_review_count", "warning_count", "suggested_status"]:
        if nh not in summary_headers:
            summary_headers.append(nh)

    # Save revised summary.csv
    if final_summary_rows:
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=summary_headers)
            writer.writeheader()
            for r in final_summary_rows:
                writer.writerow(r)

    # 6. Generate individual student feedback markdown reports
    feedback_dir = run_dir / "student_feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    
    for sub in submissions:
        sub_id = sub["submission_id"]
        metrics = sub_metrics.get(sub_id, {})
        s_status = metrics.get("suggested_status", "PASS")
        hard_list = metrics.get("hard_errors_list", [])
        review_list = metrics.get("review_queue_list", [])
        
        # Build metrics lookup
        sum_row = next((r for r in final_summary_rows if r["submission_id"] == sub_id), {})
        
        md_content = []
        md_content.append(f"# Student Grading Feedback — Submission {sub_id}\n")
        md_content.append(f"**Suggested Status:** `{s_status}`\n")
        md_content.append("## Overall Metrics")
        
        if sum_row:
            md_content.append(
                f"- Database Import Status: `{sum_row.get('manifest_status','OK')}`\n"
                f"- Total Hard Errors: **{sum_row.get('hard_error_count','0')}**\n"
                f"- Total Manual Review Items: **{sum_row.get('manual_review_count','0')}**\n"
                f"- Total Warnings: **{sum_row.get('warning_count','0')}**\n"
                f"- Structural Passes: {sum_row.get('struct_pass_count','0')}\n"
                f"- Structural Missing: {sum_row.get('struct_missing_count','0')}\n"
                f"- Structural Extra: {sum_row.get('struct_extra_count','0')}\n"
                f"- Views Required: {sum_row.get('view_required_count','0')}\n"
                f"- Views Passing: {sum_row.get('view_pass_count','0')}\n"
                f"- Views Missing: {sum_row.get('view_missing_count','0')}\n"
            )
        else:
            md_content.append("- No metric summary available.")
            
        md_content.append("\n---\n")

        # Hard errors section
        md_content.append("## ❌ Hard Errors (Clear Grading Failures)")
        if hard_list:
            md_content.append("| Component | Object | Status | Message | Suggested Action |")
            md_content.append("| :--- | :--- | :--- | :--- | :--- |")
            for item in hard_list:
                obj = item["student_object"] or item["answer_object"]
                md_content.append(
                    f"| {item['component']} | `{obj}` | `{item['status']}` | {item['message']} | {item['suggested_action']} |"
                )
        else:
            md_content.append("*No hard errors detected. Outstanding!*")
        
        md_content.append("\n---\n")

        # Review queue section
        md_content.append("## ⚠️ Items Requiring Manual Review")
        if review_list:
            md_content.append("| Component | Object | Status | Message | Suggested Action |")
            md_content.append("| :--- | :--- | :--- | :--- | :--- |")
            for item in review_list:
                obj = item["student_object"] or item["answer_object"]
                md_content.append(
                    f"| {item['component']} | `{obj}` | `{item['status']}` | {item['message']} | {item['suggested_action']} |"
                )
        else:
            md_content.append("*No items requiring manual review.*")

        md_content.append("\n---\n")
        md_content.append("*Note: This is an automatically compiled feedback report. Please consult your instructor if manual review actions are listed.*")
        
        feedback_file = feedback_dir / f"{sub_id}.md"
        with open(feedback_file, "w", encoding="utf-8") as md_f:
            md_f.write("\n".join(md_content))

    # 7. Write aggregate CSV files
    # review_queue.csv
    review_queue_path = run_dir / "review_queue.csv"
    pd.DataFrame(all_review_queue).to_csv(review_queue_path, index=False)
    
    # hard_errors.csv
    hard_errors_path = run_dir / "hard_errors.csv"
    pd.DataFrame(all_hard_errors).to_csv(hard_errors_path, index=False)

    # 8. Write aggregate Excel sheets (summary.xlsx, review_queue.xlsx)
    # summary.xlsx sheets:
    # Summary, Review Queue, Hard Errors, Row Counts, PK Adequacy, FK Relationships, Views, Column Mapping Issues
    summary_xlsx_path = run_dir / "summary.xlsx"
    with pd.ExcelWriter(summary_xlsx_path, engine="openpyxl") as writer:
        pd.DataFrame(final_summary_rows).to_excel(writer, sheet_name="Summary", index=False)
        pd.DataFrame(all_review_queue).to_excel(writer, sheet_name="Review Queue", index=False)
        pd.DataFrame(all_hard_errors).to_excel(writer, sheet_name="Hard Errors", index=False)
        pd.DataFrame(all_row_counts).to_excel(writer, sheet_name="Row Counts", index=False)
        pd.DataFrame(all_pk_adequacy).to_excel(writer, sheet_name="PK Adequacy", index=False)
        pd.DataFrame(all_fk_relationships).to_excel(writer, sheet_name="FK Relationships", index=False)
        pd.DataFrame(all_views).to_excel(writer, sheet_name="Views", index=False)
        pd.DataFrame(all_column_mapping_issues).to_excel(writer, sheet_name="Column Mapping Issues", index=False)

    # review_queue.xlsx containing a single 'Review Queue' sheet
    review_queue_xlsx_path = run_dir / "review_queue.xlsx"
    with pd.ExcelWriter(review_queue_xlsx_path, engine="openpyxl") as writer:
        pd.DataFrame(all_review_queue).to_excel(writer, sheet_name="Review Queue", index=False)

    logger.info(f"Successfully generated aggregated Excel summaries and feedback reports under: {run_dir}")
