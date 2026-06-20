import csv
from pathlib import Path
from typing import Dict, Any
from dbcheck.utils.logging import get_logger

HEADERS = [
    "submission_id",
    "manifest_status",
    "manifest_error",
    # Structure metrics
    "struct_pass_count",
    "struct_missing_count",
    "struct_extra_count",
    "struct_type_mismatch_count",
    "struct_ambiguous_count",
    # View metrics
    "view_pass_count",
    "view_not_found_count",
    "view_val_mismatch_count",
    "view_schema_mismatch_count",
    "view_exec_error_count",
    "view_ambiguous_count"
]

def compile_summary(run_dir: Path) -> Path:
    """Read manifest, structure reports, and view reports, then write a unified summary.csv."""
    logger = get_logger()
    manifest_path = run_dir / "manifest.csv"
    summary_path = run_dir / "summary.csv"
    
    if not manifest_path.exists():
        logger.warning(f"Cannot generate summary, manifest.csv missing in: {run_dir}")
        return summary_path
        
    submissions = []
    
    # 1. Read manifest entries
    with open(manifest_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            submissions.append(row)
            
    summary_rows = []
    
    for sub in submissions:
        sub_id = sub["submission_id"]
        status = sub["status"]
        err_msg = sub["error_message"]
        
        row = {h: 0 for h in HEADERS}
        row["submission_id"] = sub_id
        row["manifest_status"] = status
        row["manifest_error"] = err_msg
        
        sub_dir = run_dir / "submissions" / sub_id
        struct_report = sub_dir / "reports" / "structure_report.csv"
        view_report = sub_dir / "reports" / "view_test_report.csv"
        
        # 2. Extract structure stats
        if struct_report.exists():
            try:
                with open(struct_report, "r", encoding="utf-8") as sf:
                    s_reader = csv.DictReader(sf)
                    for s_row in s_reader:
                        s_status = s_row["status"].upper()
                        if s_status == "PASS":
                            row["struct_pass_count"] += 1
                        elif s_status == "MISSING":
                            row["struct_missing_count"] += 1
                        elif s_status == "EXTRA":
                            row["struct_extra_count"] += 1
                        elif s_status == "TYPE_MISMATCH":
                            row["struct_type_mismatch_count"] += 1
                        elif "AMBIGUOUS" in s_status:
                            row["struct_ambiguous_count"] += 1
            except Exception as e:
                logger.warning(f"Error reading structure report for '{sub_id}': {e}")
                
        # 3. Extract view stats
        if view_report.exists():
            try:
                with open(view_report, "r", encoding="utf-8") as vf:
                    v_reader = csv.DictReader(vf)
                    for v_row in v_reader:
                        v_status = v_row["status"].upper()
                        if v_status == "PASS":
                            row["view_pass_count"] += 1
                        elif v_status == "VIEW_NOT_FOUND":
                            row["view_not_found_count"] += 1
                        elif v_status == "VALUE_MISMATCH":
                            row["view_val_mismatch_count"] += 1
                        elif v_status == "OUTPUT_SCHEMA_MISMATCH":
                            row["view_schema_mismatch_count"] += 1
                        elif v_status in ["VIEW_EXECUTION_ERROR", "DATA_SEED_ERROR"]:
                            row["view_exec_error_count"] += 1
                        elif "AMBIGUOUS" in v_status:
                            row["view_ambiguous_count"] += 1
            except Exception as e:
                logger.warning(f"Error reading view report for '{sub_id}': {e}")
                
        summary_rows.append(row)
        
    # Write summary.csv
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in summary_rows:
            writer.writerow(row)
            
    logger.info(f"Global summary compiled and saved to: {summary_path}")
    return summary_path
