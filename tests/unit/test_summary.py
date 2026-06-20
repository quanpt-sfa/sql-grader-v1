import csv
import pytest
from pathlib import Path
from dbcheck.utils.summary import compile_summary

def test_compile_summary_various_statuses(tmp_path):
    run_dir = tmp_path
    manifest_path = run_dir / "manifest.csv"
    
    # 1. Write manifest
    # We will test two submissions:
    # - sub1: status="OK", has structure and view reports (some view failures)
    # - sub2: status="ERROR", skipped view reports
    # - sub3: status="OK", all views pass
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["submission_id", "status", "error_message"])
        writer.writeheader()
        writer.writerow({"submission_id": "sub1", "status": "OK", "error_message": ""})
        writer.writerow({"submission_id": "sub2", "status": "ERROR", "error_message": "Restore error"})
        writer.writerow({"submission_id": "sub3", "status": "OK", "error_message": ""})
        
    # Create sub directories
    (run_dir / "submissions" / "sub1" / "reports").mkdir(parents=True, exist_ok=True)
    (run_dir / "submissions" / "sub2" / "reports").mkdir(parents=True, exist_ok=True)
    (run_dir / "submissions" / "sub3" / "reports").mkdir(parents=True, exist_ok=True)
    
    # Write sub1 structure_report
    struct1_path = run_dir / "submissions" / "sub1" / "reports" / "structure_report.csv"
    with open(struct1_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["answer_table", "student_table", "status"])
        writer.writeheader()
        writer.writerow({"answer_table": "T1", "student_table": "T1", "status": "PK_MATCH_EXACT"})
        writer.writerow({"answer_table": "T2", "student_table": "T2", "status": "FK_MISSING"})
        
    # Write sub1 view_test_report
    view1_path = run_dir / "submissions" / "sub1" / "reports" / "view_test_report.csv"
    with open(view1_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["submission_id", "answer_view", "student_view", "status"])
        writer.writeheader()
        # Mix of pass, execution error, and mismatch
        writer.writerow({"submission_id": "sub1", "answer_view": "Cau1", "student_view": "Cau1", "status": "VIEW_PASS"})
        writer.writerow({"submission_id": "sub1", "answer_view": "Cau2", "student_view": "Cau2", "status": "VIEW_VALUE_MISMATCH"})
        writer.writerow({"submission_id": "sub1", "answer_view": "Cau3", "student_view": "", "status": "VIEW_EXECUTION_ERROR"})
        
    # Write sub3 view_test_report (all pass)
    view3_path = run_dir / "submissions" / "sub3" / "reports" / "view_test_report.csv"
    with open(view3_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["submission_id", "answer_view", "student_view", "status"])
        writer.writeheader()
        writer.writerow({"submission_id": "sub3", "answer_view": "Cau1", "student_view": "Cau1", "status": "VIEW_PASS"})
        writer.writerow({"submission_id": "sub3", "answer_view": "Cau2", "student_view": "Cau2", "status": "VIEW_PASS"})

    # Run compilation
    summary_path = compile_summary(run_dir)
    assert summary_path.exists()
    
    # Read summary back
    summary_rows = []
    with open(summary_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            summary_rows.append(row)
            
    assert len(summary_rows) == 3
    
    # Validate sub1
    s1 = summary_rows[0]
    assert s1["submission_id"] == "sub1"
    assert s1["manifest_status"] == "OK"
    assert int(s1["pk_exact_match_count"]) == 1
    assert int(s1["fk_missing_count"]) == 1
    assert int(s1["view_required_count"]) == 3
    assert int(s1["view_pass_count"]) == 1
    assert int(s1["view_value_mismatch_count"]) == 1
    assert int(s1["view_execution_error_count"]) == 1
    # Needs to be ERROR since VIEW_EXECUTION_ERROR is present
    assert s1["view_test_status"] == "ERROR"
    
    # Validate sub2
    s2 = summary_rows[1]
    assert s2["submission_id"] == "sub2"
    assert s2["manifest_status"] == "ERROR"
    assert s2["manifest_error"] == "Restore error"
    assert int(s2["view_required_count"]) == 0
    assert s2["view_test_status"] == ""
    
    # Validate sub3
    s3 = summary_rows[2]
    assert s3["submission_id"] == "sub3"
    assert s3["manifest_status"] == "OK"
    assert int(s3["view_required_count"]) == 2
    assert int(s3["view_pass_count"]) == 2
    assert s3["view_test_status"] == "OK"
