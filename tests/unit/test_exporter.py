import csv
import pytest
from pathlib import Path
from types import SimpleNamespace
from dbcheck.utils.exporter import export_results, REVIEW_STATUSES, HARD_ERROR_STATUSES

@pytest.fixture
def mock_run_dir(tmp_path):
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()
    
    # 1. Create manifest.csv
    manifest_file = run_dir / "manifest.csv"
    with open(manifest_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["submission_id", "status", "error_message"])
        writer.writerow(["student1", "OK", ""])
        writer.writerow(["student2", "ERROR", "Restore failed"])
        writer.writerow(["student3", "OK", ""])
        
    # 2. Create summary.csv
    summary_file = run_dir / "summary.csv"
    with open(summary_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["submission_id", "manifest_status", "struct_pass_count", "struct_missing_count", "view_required_count"])
        writer.writerow(["student1", "OK", "10", "2", "2"])
        writer.writerow(["student2", "ERROR", "0", "0", "0"])
        writer.writerow(["student3", "OK", "12", "0", "2"])

    # 3. Create answer snapshot structure
    ans_snap_dir = run_dir / "answer_snapshot"
    ans_snap_dir.mkdir()
    
    ans_tables = ans_snap_dir / "tables.csv"
    with open(ans_tables, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["table_name", "row_count"])
        writer.writerow(["HangHoa", "25"])
        writer.writerow(["LoaiTien", "3"])
        
    ans_views = ans_snap_dir / "views.csv"
    with open(ans_views, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["view_name"])
        writer.writerow(["Cau1"])
        writer.writerow(["Cau2"])
        
    # 4. Create reports for student1 (fails structure check)
    sub1_dir = run_dir / "submissions" / "student1"
    sub1_reports = sub1_dir / "reports"
    sub1_reports.mkdir(parents=True)
    sub1_snap = sub1_dir / "snapshot"
    sub1_snap.mkdir(parents=True)
    
    # Tables snapshot for student1
    with open(sub1_snap / "tables.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["table_name", "row_count"])
        writer.writerow(["01.HangTonKho", "25"])
        writer.writerow(["02.Tien", "3"])
        
    # Table mapping for student1
    with open(sub1_reports / "table_mapping_report.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["answer_table", "student_table", "match_status"])
        writer.writerow(["HangHoa", "01.HangTonKho", "TABLE_MATCHED_ALIAS"])
        writer.writerow(["LoaiTien", "02.Tien", "TABLE_MATCHED_ALIAS"])
        
    # structure_report for student1 (PK_MISSING -> FAIL_STRUCTURE)
    with open(sub1_reports / "structure_report.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["component", "answer_object", "student_object", "status", "severity", "message", "evidence"])
        writer.writerow(["primary_key", "HangHoa", "", "PK_MISSING", "high", "Missing primary key", ""])
        
    # view_test_report for student1
    with open(sub1_reports / "view_test_report.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["answer_view", "student_view", "status", "row_count_answer", "row_count_student", "value_mismatch_count"])
        writer.writerow(["Cau1", "Cau1", "VIEW_PASS", "10", "10", "0"])
        writer.writerow(["Cau2", "Cau2", "VIEW_PASS", "5", "5", "0"])

    # 5. Create reports for student3 (fails view check, but has ROW_COUNT_MISMATCH warnings too)
    sub3_dir = run_dir / "submissions" / "student3"
    sub3_reports = sub3_dir / "reports"
    sub3_reports.mkdir(parents=True)
    sub3_snap = sub3_dir / "snapshot"
    sub3_snap.mkdir(parents=True)
    
    with open(sub3_snap / "tables.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["table_name", "row_count"])
        writer.writerow(["01.HangTonKho", "24"]) # causes ROW_COUNT_MISMATCH (FAIL_DATA)
        writer.writerow(["02.Tien", "3"])
        
    with open(sub3_reports / "table_mapping_report.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["answer_table", "student_table", "match_status"])
        writer.writerow(["HangHoa", "01.HangTonKho", "TABLE_MATCHED_ALIAS"])
        writer.writerow(["LoaiTien", "02.Tien", "TABLE_MATCHED_ALIAS"])
        
    with open(sub3_reports / "structure_report.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["component", "answer_object", "student_object", "status", "severity", "message", "evidence"])
        writer.writerow(["table", "HangHoa", "01.HangTonKho", "ROW_COUNT_MISMATCH", "warning", "Row count mismatch", ""])
        
    # We do NOT create view_test_report.csv for student3. This should trigger VIEW_TEST_NOT_RUN (NEEDS_REVIEW).

    return run_dir

def test_export_results_suggested_status_and_outputs(mock_run_dir):
    config = SimpleNamespace(name="Grading Test Assignment")
    
    # Run the exporter
    export_results(mock_run_dir, config)
    
    # 1. Verify files exist
    summary_csv = mock_run_dir / "summary.csv"
    summary_xlsx = mock_run_dir / "summary.xlsx"
    review_queue_csv = mock_run_dir / "review_queue.csv"
    review_queue_xlsx = mock_run_dir / "review_queue.xlsx"
    hard_errors_csv = mock_run_dir / "hard_errors.csv"
    
    assert summary_csv.exists()
    assert summary_xlsx.exists()
    assert review_queue_csv.exists()
    assert review_queue_xlsx.exists()
    assert hard_errors_csv.exists()
    
    # 2. Verify summary.csv contents and suggested statuses
    with open(summary_csv, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        
    assert len(rows) == 3
    
    # student1: PK_MISSING exists -> FAIL_STRUCTURE
    s1_row = next(r for r in rows if r["submission_id"] == "student1")
    assert s1_row["suggested_status"] == "FAIL_STRUCTURE"
    assert int(s1_row["hard_error_count"]) == 1
    assert int(s1_row["manual_review_count"]) == 0
    
    # student2: manifest status ERROR -> FAIL_RESTORE_OR_SNAPSHOT
    s2_row = next(r for r in rows if r["submission_id"] == "student2")
    assert s2_row["suggested_status"] == "FAIL_RESTORE_OR_SNAPSHOT"
    assert int(s2_row["hard_error_count"]) == 1
    assert int(s2_row["manual_review_count"]) == 0
    
    # student3: ROW_COUNT_MISMATCH exists (FAIL_DATA) & view_test_report is missing -> VIEW_TEST_NOT_RUN (NEEDS_REVIEW)
    # Priority: FAIL_DATA > NEEDS_REVIEW. Let's check if FAIL_DATA wins
    s3_row = next(r for r in rows if r["submission_id"] == "student3")
    assert s3_row["suggested_status"] == "FAIL_DATA"
    assert int(s3_row["hard_error_count"]) == 1 # ROW_COUNT_MISMATCH is a hard error
    assert int(s3_row["manual_review_count"]) == 2 # 2 missing views -> VIEW_TEST_NOT_RUN
    
    # 3. Verify student feedback markdown files
    fb_s1 = mock_run_dir / "student_feedback" / "student1.md"
    fb_s2 = mock_run_dir / "student_feedback" / "student2.md"
    fb_s3 = mock_run_dir / "student_feedback" / "student3.md"
    
    assert fb_s1.exists()
    assert fb_s2.exists()
    assert fb_s3.exists()
    
    content_s1 = fb_s1.read_text(encoding="utf-8")
    assert "FAIL_STRUCTURE" in content_s1
    assert "PK_MISSING" in content_s1
    
    content_s3 = fb_s3.read_text(encoding="utf-8")
    assert "FAIL_DATA" in content_s3
    assert "VIEW_TEST_NOT_RUN" in content_s3
    
    # 4. Verify review_queue.csv has the VIEW_TEST_NOT_RUN entries for student3
    with open(review_queue_csv, "r", encoding="utf-8") as f:
        rq_rows = list(csv.DictReader(f))
    assert len(rq_rows) == 2
    for r in rq_rows:
        assert r["submission_id"] == "student3"
        assert r["status"] == "VIEW_TEST_NOT_RUN"
        
    # 5. Verify hard_errors.csv has entries for student1, student2, student3
    with open(hard_errors_csv, "r", encoding="utf-8") as f:
        he_rows = list(csv.DictReader(f))
    # s1: PK_MISSING, s2: FAIL_RESTORE_OR_SNAPSHOT, s3: ROW_COUNT_MISMATCH
    assert len(he_rows) == 3
    assert any(r["submission_id"] == "student1" and r["status"] == "PK_MISSING" for r in he_rows)
    assert any(r["submission_id"] == "student2" and r["status"] == "FAIL_RESTORE_OR_SNAPSHOT" for r in he_rows)
    assert any(r["submission_id"] == "student3" and r["status"] == "ROW_COUNT_MISMATCH" for r in he_rows)
