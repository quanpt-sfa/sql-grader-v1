import csv
from pathlib import Path
from typing import List, Dict, Any, Tuple
import pandas as pd
from dbcheck.config import AssignmentConfig, ViewConfig
from dbcheck.views.view_output_reader import read_view_output
from dbcheck.views.output_canonicalizer import canonicalize_view_output, resolve_view_columns
from dbcheck.views.result_comparator import compare_multisets
from dbcheck.utils.logging import get_logger

HEADERS = [
    "submission_id",
    "answer_view",
    "student_view",
    "status",
    "matched_columns",
    "missing_columns",
    "extra_columns",
    "row_count_answer",
    "row_count_student",
    "answer_minus_student_count",
    "student_minus_answer_count",
    "value_mismatch_count",
    "execution_error"
]

def run_view_testing(
    db_conn: Any,
    ans_db: str,
    stud_db: str,
    submission_id: str,
    config: AssignmentConfig,
    ans_views_snap: List[Dict[str, Any]],
    stud_views_snap: List[Dict[str, Any]],
    stud_cols_snap: List[Dict[str, Any]], # physical columns for resolving global aliases
    output_report_path: Path,
    diff_dir: Path
) -> List[Dict[str, Any]]:
    """Execute student views against answer views, compare output, save diffs, and generate CSV report."""
    logger = get_logger()
    logger.info(f"Running view behavior tests for submission '{submission_id}'...")
    
    # Map student views by canonical name to physical name
    # Can contain list of physical names in case of duplicates (VIEW_AMBIGUOUS)
    stud_view_map: Dict[str, List[str]] = {}
    for v in stud_views_snap:
        canon = v["view_name_canonical"]
        if canon:
            stud_view_map.setdefault(canon.lower(), []).append(v["view_name"])
            
    # Group student columns by table canonical (to use as global column alias dictionary)
    global_col_aliases = config.schema.columns_global if hasattr(config.schema, "columns_global") else {}
    
    # Load accepted column mappings from column_mapping_report.csv if it exists
    accepted_table_col_mappings = {}
    col_report_path = output_report_path.parent / "column_mapping_report.csv"
    if col_report_path.exists():
        try:
            with open(col_report_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row["match_status"] in ["COLUMN_MATCHED_EXACT", "COLUMN_MATCHED_ALIAS", "COLUMN_MATCHED_ABBREVIATION", "COLUMN_MATCHED_FUZZY_HIGH"]:
                        accepted_table_col_mappings[row["student_column"].lower()] = row["answer_column"]
        except Exception as e:
            logger.warning(f"Failed to read column_mapping_report.csv: {e}")
            
    results = []
    
    for view_cfg in config.views:
        ans_v_name = view_cfg.answer_view
        ans_v_name_l = ans_v_name.lower()
        
        # Determine student view matching this canonical name
        stud_v_name = ""
        status = ""
        execution_error = ""
        matched_cols = []
        missing_cols = []
        extra_cols = []
        metrics = {
            "row_count_answer": 0,
            "row_count_student": 0,
            "answer_minus_student_count": 0,
            "student_minus_answer_count": 0,
            "value_mismatch_count": 0
        }
        
        # 1. Resolve student view name
        candidates = stud_view_map.get(ans_v_name_l, [])
        if not candidates:
            # Check if there is an exact or fuzzy physical match that normalizer missed
            # But normally we check the snapshot map
            status = "VIEW_NOT_FOUND"
            logger.warning(f"View '{ans_v_name}' not found in student database.")
        elif len(candidates) > 1:
            status = "VIEW_AMBIGUOUS"
            stud_v_name = candidates[0] # use first as placeholder
            logger.warning(f"Ambiguous view mapping: multiple student views match canonical '{ans_v_name}': {candidates}")
        else:
            stud_v_name = candidates[0]
            
        # 2. Run views if resolved
        if status not in ["VIEW_NOT_FOUND", "VIEW_AMBIGUOUS"]:
            # Query answer view
            ans_df, ans_err = read_view_output(db_conn, ans_db, ans_v_name)
            # Query student view
            stud_df, stud_err = read_view_output(db_conn, stud_db, stud_v_name)
            
            if ans_err:
                status = "DATA_SEED_ERROR"
                execution_error = f"Answer view execution error: {ans_err}"
                logger.error(f"Answer view '{ans_v_name}' failed to run: {ans_err}")
            elif stud_err:
                status = "VIEW_EXECUTION_ERROR"
                execution_error = stud_err
                logger.warning(f"Student view '{stud_v_name}' failed to run: {stud_err}")
            else:
                # 3. Resolve columns and check schema
                try:
                    # Get student columns before renaming
                    phys_cols = list(stud_df.columns)
                    col_mapping = resolve_view_columns(phys_cols, view_cfg, accepted_table_col_mappings, config.schema.column_accept_threshold)
                    
                    # Columns successfully resolved
                    expected_canonicals = [c["canonical"] for c in view_cfg.columns]
                    for p, c in col_mapping.items():
                        matched_cols.append(c)
                        
                    for c in expected_canonicals:
                        if c not in matched_cols:
                            missing_cols.append(c)
                            
                    for p in phys_cols:
                        if p not in col_mapping:
                            extra_cols.append(p)
                            
                    if missing_cols or extra_cols:
                        status = "VIEW_OUTPUT_SCHEMA_MISMATCH"
                        logger.warning(f"Output schema mismatch for view '{stud_v_name}': missing={missing_cols}, extra={extra_cols}")
                except ValueError as val_err:
                    status = "VIEW_OUTPUT_MAPPING_AMBIGUOUS"
                    execution_error = str(val_err)
                    logger.warning(f"Column mapping ambiguity in view '{stud_v_name}': {val_err}")
                    
                # 4. Canonicalize outputs and compare
                if status not in ["VIEW_OUTPUT_SCHEMA_MISMATCH", "VIEW_OUTPUT_MAPPING_AMBIGUOUS"]:
                    try:
                        ans_canon = canonicalize_view_output(ans_df, view_cfg, {}, config.schema.column_accept_threshold)
                        stud_canon = canonicalize_view_output(stud_df, view_cfg, accepted_table_col_mappings, config.schema.column_accept_threshold)
                        
                        # Compare
                        ans_minus_stud, stud_minus_ans, metrics = compare_multisets(ans_canon, stud_canon)
                        
                        if metrics["answer_minus_student_count"] > 0 or metrics["student_minus_answer_count"] > 0:
                            status = "VALUE_MISMATCH"
                            
                            # Export differences to CSV
                            diff_dir.mkdir(parents=True, exist_ok=True)
                            ans_minus_file = diff_dir / f"{submission_id}_{ans_v_name}_answer_minus_student.csv"
                            stud_minus_file = diff_dir / f"{submission_id}_{ans_v_name}_student_minus_answer.csv"
                            
                            ans_minus_stud.to_csv(ans_minus_file, index=False, encoding="utf-8")
                            stud_minus_ans.to_csv(stud_minus_file, index=False, encoding="utf-8")
                            logger.info(f"Diff exported for view '{ans_v_name}' due to VALUE_MISMATCH.")
                        else:
                            status = "PASS"
                    except Exception as ex:
                        status = "VIEW_EXECUTION_ERROR"
                        execution_error = f"Canonicalization/Comparison error: {ex}"
                        logger.error(f"Error comparing view outputs for '{ans_v_name}': {ex}")

        # Record findings
        results.append({
            "submission_id": submission_id,
            "answer_view": ans_v_name,
            "student_view": stud_v_name,
            "status": status,
            "matched_columns": ";".join(matched_cols),
            "missing_columns": ";".join(missing_cols),
            "extra_columns": ";".join(extra_cols),
            "row_count_answer": metrics["row_count_answer"],
            "row_count_student": metrics["row_count_student"],
            "answer_minus_student_count": metrics["answer_minus_student_count"],
            "student_minus_answer_count": metrics["student_minus_answer_count"],
            "value_mismatch_count": metrics["value_mismatch_count"],
            "execution_error": execution_error
        })
        
    # Write report CSV
    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in results:
            writer.writerow(row)
            
    logger.info(f"View behavioral report saved to: {output_report_path}")
    return results
