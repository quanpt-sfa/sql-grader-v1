import csv
import pytest
from pathlib import Path
from dbcheck.config import load_config, AssignmentConfig
from dbcheck.utils.scoring import (
    load_rubric, load_overrides, get_answer_atomic_items,
    score_submission, get_submission_statuses
)

@pytest.fixture
def mock_config_file(tmp_path):
    config_content = """
assignment:
  name: "Test Assignment"
  protected_answer_db: "00000001"
schema:
  key_grading:
    mode: adequacy
    allow_surrogate_keys: true
    allow_natural_keys: true
views:
  mode: answer_snapshot
  execution_mode: compare_existing_data
  expected:
    - answer_view: "Cau1"
      order_sensitive: false
    - answer_view: "Cau2"
      order_sensitive: true
"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(config_content, encoding="utf-8")
    return cfg_path

def test_scoring_denominator_ignores_extra_student_items(tmp_path, mock_config_file):
    run_dir = tmp_path
    config = load_config(str(mock_config_file))
    
    # 1. Create answer snapshot
    snap_dir = run_dir / "answer_snapshot"
    snap_dir.mkdir(parents=True)
    
    # Expected tables: T1, T2
    with open(snap_dir / "tables.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["table_name"])
        w.writerow(["T1"])
        w.writerow(["T2"])
        
    # Expected columns: T1.C1, T1.C2
    with open(snap_dir / "columns.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["table_name", "column_name"])
        w.writerow(["T1", "C1"])
        w.writerow(["T1", "C2"])
        
    # Query ground truth expected items
    answer_items = get_answer_atomic_items(run_dir, config)
    assert len(answer_items["tables"]) == 2
    assert len(answer_items["columns"]) == 2
    
    # 2. Rubric
    rubric = [
        {
            "section": "A", "component": "tables", "scope": "all", "object_name": "",
            "total_points": 2.0, "scoring_mode": "proportional",
            "include_statuses": "TABLE_MATCHED_EXACT|TABLE_MATCHED_ALIAS",
            "partial_policy": "review_pending", "notes": ""
        },
        {
            "section": "B", "component": "columns", "scope": "all", "object_name": "",
            "total_points": 4.0, "scoring_mode": "proportional",
            "include_statuses": "COLUMN_MATCHED_EXACT|COLUMN_MATCHED_ALIAS",
            "partial_policy": "review_pending", "notes": ""
        }
    ]
    
    # 3. Student reports (T1, T2 matched, but they have extra student table T3 and column T3.CX)
    reports_dir = run_dir / "submissions" / "sub1" / "reports"
    reports_dir.mkdir(parents=True)
    
    # Table mapping report: Table T3 is extra, Table T1 and T2 matched exact
    with open(reports_dir / "table_mapping_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["answer_table", "student_table", "match_status"])
        w.writerow(["T1", "T1", "TABLE_MATCHED_EXACT"])
        w.writerow(["T2", "T2", "TABLE_MATCHED_EXACT"])
        # In a real environment, extra tables might just be reported or omitted from mapping,
        # but let's make sure the denominator is 2, not 3.
        
    # Column mapping report
    with open(reports_dir / "column_mapping_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["answer_table", "answer_column", "student_table", "student_column", "match_status"])
        w.writerow(["T1", "C1", "T1", "C1", "COLUMN_MATCHED_EXACT"])
        w.writerow(["T1", "C2", "T1", "C2", "COLUMN_MATCHED_EXACT"])
        
    details, total_score, rev_count, err_count = score_submission(
        "sub1", "OK", run_dir, config, rubric, [], answer_items
    )
    
    # Assertions
    # T1, T2 matched out of 2 expected tables -> 2/2 -> 2.0 points
    # T1.C1, T1.C2 matched out of 2 expected columns -> 2/2 -> 4.0 points
    # Total score should be 6.0
    assert total_score == 6.0
    assert details[0]["points_possible"] == 2.0
    assert details[0]["original_points_awarded"] == 2.0
    assert details[1]["points_possible"] == 4.0
    assert details[1]["original_points_awarded"] == 4.0

def test_scoring_missing_or_failing_view_gets_zero(tmp_path, mock_config_file):
    run_dir = tmp_path
    config = load_config(str(mock_config_file))
    
    # Views: Cau1, Cau2
    answer_items = {
        "tables": [], "columns": [], "primary_keys": [], "foreign_keys": [], "row_counts": [],
        "views": ["Cau1", "Cau2"]
    }
    
    rubric = [
        {
            "section": "F", "component": "views", "scope": "Cau1", "object_name": "",
            "total_points": 2.0, "scoring_mode": "weighted_subchecks",
            "include_statuses": "VIEW_PASS",
            "partial_policy": "partial_view", "notes": ""
        },
        {
            "section": "F", "component": "views", "scope": "Cau2", "object_name": "",
            "total_points": 3.0, "scoring_mode": "weighted_subchecks",
            "include_statuses": "VIEW_PASS",
            "partial_policy": "partial_view", "notes": ""
        }
    ]
    
    reports_dir = run_dir / "submissions" / "sub1" / "reports"
    reports_dir.mkdir(parents=True)
    
    # view_test_report: Cau1 is VIEW_NOT_FOUND, Cau2 is VIEW_EXECUTION_ERROR
    with open(reports_dir / "view_test_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["answer_view", "student_view", "status", "missing_columns", "row_count_answer", "row_count_student", "value_mismatch_count"])
        w.writerow(["Cau1", "", "VIEW_NOT_FOUND", "", "0", "0", "0"])
        w.writerow(["Cau2", "Cau2", "VIEW_EXECUTION_ERROR", "", "10", "0", "0"])
        
    details, total_score, rev_count, err_count = score_submission(
        "sub1", "OK", run_dir, config, rubric, [], answer_items
    )
    
    assert total_score == 0.0
    assert details[0]["points_possible"] == 2.0
    assert details[0]["original_points_awarded"] == 0.0
    assert details[1]["points_possible"] == 3.0
    assert details[1]["original_points_awarded"] == 0.0

def test_scoring_partial_view_weighted_subchecks(tmp_path, mock_config_file):
    run_dir = tmp_path
    config = load_config(str(mock_config_file))
    
    answer_items = {
        "tables": [], "columns": [], "primary_keys": [], "foreign_keys": [], "row_counts": [],
        "views": ["Cau1", "Cau2"]
    }
    
    rubric = [
        # Cau1 order-insensitive: order weight redistributed (exists=2/9, schema=2/9, row_count=2/9, value_match=3/9)
        {
            "section": "F", "component": "views", "scope": "Cau1", "object_name": "",
            "total_points": 9.0, "scoring_mode": "weighted_subchecks",
            "include_statuses": "VIEW_PASS",
            "partial_policy": "partial_view", "notes": ""
        },
        # Cau2 order-sensitive: standard weights (exists=0.2, schema=0.2, row_count=0.2, value_match=0.3, order_match=0.1)
        {
            "section": "F", "component": "views", "scope": "Cau2", "object_name": "",
            "total_points": 10.0, "scoring_mode": "weighted_subchecks",
            "include_statuses": "VIEW_PASS",
            "partial_policy": "partial_view", "notes": ""
        }
    ]
    
    reports_dir = run_dir / "submissions" / "sub1" / "reports"
    reports_dir.mkdir(parents=True)
    
    with open(reports_dir / "view_test_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["answer_view", "student_view", "status", "missing_columns", "row_count_answer", "row_count_student", "value_mismatch_count"])
        # Cau1: exists, schema correct, row_count correct, but value_mismatch = 5 (fails value_match, order_match is ignored)
        # Passed subchecks: exists (2/9), schema (2/9), row_count (2/9) -> 6/9 of 9.0 points = 6.0
        w.writerow(["Cau1", "Cau1", "VIEW_VALUE_MISMATCH", "", "10", "10", "5"])
        # Cau2: exists, schema correct, row_count correct, value_mismatch=0, but status is VIEW_ORDER_MISMATCH
        # Passed: exists (0.2), schema (0.2), row_count (0.2), value_match (0.3). Failed: order_match (0.1)
        # Score = 0.9 * 10.0 = 9.0
        w.writerow(["Cau2", "Cau2", "VIEW_ORDER_MISMATCH", "", "10", "10", "0"])
        
    details, total_score, rev_count, err_count = score_submission(
        "sub1", "OK", run_dir, config, rubric, [], answer_items
    )
    
    assert details[0]["original_points_awarded"] == pytest.approx(6.0)
    assert details[1]["original_points_awarded"] == pytest.approx(9.0)
    assert total_score == pytest.approx(15.0)

def test_scoring_review_pending_vs_warning_pass_policy(tmp_path, mock_config_file):
    run_dir = tmp_path
    config = load_config(str(mock_config_file))
    
    answer_items = {
        "tables": ["T1", "T2"], "columns": [], "primary_keys": [], "foreign_keys": [], "row_counts": [], "views": []
    }
    
    rubric = [
        # review_pending: status in REVIEW_STATUSES gives 0 score, flags review_required
        {
            "section": "A", "component": "tables", "scope": "T1", "object_name": "",
            "total_points": 2.0, "scoring_mode": "proportional",
            "include_statuses": "TABLE_MATCHED_EXACT",
            "partial_policy": "review_pending", "notes": ""
        },
        # warning_pass: status in REVIEW_STATUSES gives full/proportional score, flags review_required
        {
            "section": "A", "component": "tables", "scope": "T2", "object_name": "",
            "total_points": 2.0, "scoring_mode": "proportional",
            "include_statuses": "TABLE_MATCHED_EXACT",
            "partial_policy": "warning_pass", "notes": ""
        }
    ]
    
    reports_dir = run_dir / "submissions" / "sub1" / "reports"
    reports_dir.mkdir(parents=True)
    
    with open(reports_dir / "table_mapping_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["answer_table", "student_table", "match_status"])
        w.writerow(["T1", "T1_Alias", "TABLE_REVIEW_REQUIRED"])
        w.writerow(["T2", "T2_Alias", "TABLE_REVIEW_REQUIRED"])
        
    details, total_score, rev_count, err_count = score_submission(
        "sub1", "OK", run_dir, config, rubric, [], answer_items
    )
    
    # T1: review_pending -> 0 points, review_required=True
    assert details[0]["final_points_awarded"] == 0.0
    assert details[0]["review_required"] is True
    
    # T2: warning_pass -> 2.0 points, review_required=True
    assert details[1]["final_points_awarded"] == 2.0
    assert details[1]["review_required"] is True
    
    assert total_score == 2.0
    assert rev_count == 2

def test_scoring_manual_override(tmp_path, mock_config_file):
    run_dir = tmp_path
    config = load_config(str(mock_config_file))
    
    answer_items = {
        "tables": ["T1"], "columns": [], "primary_keys": [], "foreign_keys": [], "row_counts": [], "views": []
    }
    
    rubric = [
        {
            "section": "A", "component": "tables", "scope": "T1", "object_name": "",
            "total_points": 2.0, "scoring_mode": "proportional",
            "include_statuses": "TABLE_MATCHED_EXACT",
            "partial_policy": "review_pending", "notes": ""
        }
    ]
    
    reports_dir = run_dir / "submissions" / "sub1" / "reports"
    reports_dir.mkdir(parents=True)
    
    # Table is missing, original score is 0.0
    with open(reports_dir / "table_mapping_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["answer_table", "student_table", "match_status"])
        w.writerow(["T1", "", "MISSING"])
        
    # Manual overrides: give student 1.5 points for table T1
    overrides = [
        {
            "submission_id": "sub1",
            "section": "A",
            "component": "tables",
            "answer_object": "T1",
            "override_points": 1.5,
            "override_status": "OVERRIDDEN_PASS",
            "reviewer_note": "Fuzzy match was acceptable manually"
        }
    ]
    
    details, total_score, rev_count, err_count = score_submission(
        "sub1", "OK", run_dir, config, rubric, overrides, answer_items
    )
    
    # Original points must be preserved as 0.0
    assert details[0]["original_points_awarded"] == 0.0
    # Final points awarded must be overwritten to 1.5
    assert details[0]["final_points_awarded"] == 1.5
    assert details[0]["override_applied"] is True
    assert details[0]["reviewer_note"] == "Fuzzy match was acceptable manually"
    assert details[0]["status"] == "OVERRIDDEN_PASS"
    assert total_score == 1.5


def test_rubric_csv_is_source_of_truth_without_manual_part_c(tmp_path, mock_config_file):
    run_dir = tmp_path
    config = load_config(str(mock_config_file))

    answer_items = {
        "tables": ["T1"],
        "columns": ["T1.C1"],
        "primary_keys": ["T1"],
        "foreign_keys": ["T1|T2|C1|C2"],
        "row_counts": ["T1"],
        "views": [
            "vw_Cau1_NhaCungCap_ThuDuc_Den20240630",
            "vw_Cau2_Top10_HangHoa_MuaNhieu_Den20240630",
            "vw_Cau3_CongNo_NhaCungCap_CuoiQ1_2024",
        ],
    }

    rubric = [
        {"section": "A.1", "component": "tables", "scope": "all", "object_name": "", "total_points": 2.0, "scoring_mode": "proportional", "include_statuses": "TABLE_MATCHED_EXACT", "partial_policy": "review_pending", "notes": ""},
        {"section": "A.1", "component": "columns", "scope": "all", "object_name": "", "total_points": 2.0, "scoring_mode": "proportional", "include_statuses": "COLUMN_MATCHED_EXACT", "partial_policy": "review_pending", "notes": ""},
        {"section": "A.1", "component": "primary_keys", "scope": "all", "object_name": "", "total_points": 1.0, "scoring_mode": "proportional", "include_statuses": "PK_MATCH_EXACT", "partial_policy": "review_pending", "notes": ""},
        {"section": "A.2", "component": "foreign_keys", "scope": "all", "object_name": "", "total_points": 1.0, "scoring_mode": "proportional", "include_statuses": "FK_RELATIONSHIP_MATCH", "partial_policy": "review_pending", "notes": ""},
        {"section": "A.3", "component": "row_counts", "scope": "all", "object_name": "", "total_points": 1.0, "scoring_mode": "proportional", "include_statuses": "PASS", "partial_policy": "review_pending", "notes": ""},
        {"section": "B", "component": "views", "scope": "Cau1", "object_name": "", "total_points": 1.0, "scoring_mode": "weighted_subchecks", "include_statuses": "VIEW_PASS|VIEW_OUTPUT_MATCH", "partial_policy": "partial_view", "notes": ""},
        {"section": "B", "component": "views", "scope": "Cau2", "object_name": "", "total_points": 1.0, "scoring_mode": "weighted_subchecks", "include_statuses": "VIEW_PASS|VIEW_OUTPUT_MATCH", "partial_policy": "partial_view", "notes": ""},
        {"section": "B", "component": "views", "scope": "Cau3", "object_name": "", "total_points": 1.0, "scoring_mode": "weighted_subchecks", "include_statuses": "VIEW_PASS|VIEW_OUTPUT_MATCH", "partial_policy": "partial_view", "notes": ""},
    ]
    assert sum(row["total_points"] for row in rubric) == 10.0

    reports_dir = run_dir / "submissions" / "sub1" / "reports"
    reports_dir.mkdir(parents=True)

    with open(reports_dir / "table_mapping_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["answer_table", "student_table", "match_status"])
        w.writerow(["T1", "T1", "TABLE_MATCHED_EXACT"])

    with open(reports_dir / "column_mapping_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["answer_table", "answer_column", "student_table", "student_column", "match_status"])
        w.writerow(["T1", "C1", "T1", "C1", "COLUMN_MATCHED_EXACT"])

    with open(reports_dir / "key_adequacy_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["table_name", "key_status"])
        w.writerow(["T1", "PK_MATCH_EXACT"])

    with open(reports_dir / "fk_relationship_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["answer_relationship_signature", "fk_status"])
        w.writerow(["T1|T2|C1|C2", "FK_RELATIONSHIP_MATCH"])

    with open(reports_dir / "structure_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["component", "answer_object", "status"])

    with open(reports_dir / "view_test_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["answer_view", "student_view", "status", "missing_columns", "row_count_answer", "row_count_student", "value_mismatch_count"])
        w.writerow(["vw_Cau1_NhaCungCap_ThuDuc_Den20240630", "StudCau1", "VIEW_OUTPUT_MATCH", "", "1", "1", "0"])
        w.writerow(["vw_Cau2_Top10_HangHoa_MuaNhieu_Den20240630", "StudCau2", "VIEW_PASS", "", "1", "1", "0"])
        w.writerow(["vw_Cau3_CongNo_NhaCungCap_CuoiQ1_2024", "StudCau3", "VIEW_PASS", "", "1", "1", "0"])

    details, total_score, _rev_count, _err_count = score_submission(
        "sub1", "OK", run_dir, config, rubric, [], answer_items
    )

    assert len(details) == len(rubric)
    assert all(d["component"] != "manual" for d in details)
    assert all(d["answer_object"] != "PartC" for d in details)
    assert sum(d["points_possible"] for d in details) == 10.0
    assert total_score == pytest.approx(10.0)
    cau1 = next(d for d in details if d["component"] == "views" and d["answer_object"] == "Cau1")
    assert cau1["status"] == "VIEW_OUTPUT_MATCH"
    assert cau1["original_points_awarded"] == 1.0


def _pk_rubric_row():
    return [{
        "section": "A.1",
        "component": "primary_keys",
        "scope": "all",
        "object_name": "",
        "total_points": 1.0,
        "scoring_mode": "proportional",
        "include_statuses": "PK_MATCH_EXACT|PK_MATCH_ALIAS_EQUIVALENT|PK_SURROGATE_ACCEPTED|PK_NATURAL_ACCEPTED",
        "partial_policy": "review_pending",
        "notes": "",
    }]


def _write_key_adequacy_report(run_dir, sub_id, rows):
    reports_dir = run_dir / "submissions" / sub_id / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    with open(reports_dir / "key_adequacy_report.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["table_name", "key_status"])
        writer.writeheader()
        for table_name, key_status in rows:
            writer.writerow({"table_name": table_name, "key_status": key_status})


def test_primary_key_scoring_uses_key_adequacy_report_denominator(tmp_path, mock_config_file):
    config = load_config(str(mock_config_file))
    rows = [
        ("T1", "PK_MATCH_EXACT"),
        ("T2", "PK_MATCH_ALIAS_EQUIVALENT"),
        ("T3", "PK_SURROGATE_ACCEPTED"),
        ("T4", "PK_NATURAL_ACCEPTED"),
        ("T5", "PK_MATCH_EXACT"),
        ("T6", "PK_MATCH_ALIAS_EQUIVALENT"),
        ("T7", "PK_MISSING"),
        ("T8", "PK_MISSING"),
    ]
    _write_key_adequacy_report(tmp_path, "sub1", rows)

    details, total_score, rev_count, _err_count = score_submission(
        "sub1", "OK", tmp_path, config, _pk_rubric_row(), [], {"primary_keys": []}
    )

    assert total_score == pytest.approx(6 / 8)
    assert details[0]["status"] != "NO_ITEMS"
    assert details[0]["message"] == "Passed 6/8 items"
    assert details[0]["review_required"] is False
    assert rev_count == 0


def test_primary_key_review_required_sets_review_pending(tmp_path, mock_config_file):
    config = load_config(str(mock_config_file))
    _write_key_adequacy_report(tmp_path, "sub1", [
        ("T1", "PK_MATCH_EXACT"),
        ("T2", "PK_REVIEW_REQUIRED"),
    ])

    details, total_score, rev_count, _err_count = score_submission(
        "sub1", "OK", tmp_path, config, _pk_rubric_row(), [], {"primary_keys": []}
    )

    assert total_score == pytest.approx(0.5)
    assert details[0]["review_required"] is True
    assert rev_count == 1


def test_primary_key_no_items_only_when_no_report_or_snapshot_source(tmp_path, mock_config_file):
    config = load_config(str(mock_config_file))
    (tmp_path / "submissions" / "sub1" / "reports").mkdir(parents=True, exist_ok=True)

    details, total_score, _rev_count, _err_count = score_submission(
        "sub1", "OK", tmp_path, config, _pk_rubric_row(), [], {"primary_keys": []}
    )

    assert total_score == 0.0
    assert details[0]["status"] == "NO_ITEMS"


def test_primary_key_answer_snapshot_fallback_produces_expected_items(tmp_path, mock_config_file):
    config = load_config(str(mock_config_file))
    snap_dir = tmp_path / "answer_snapshot"
    snap_dir.mkdir(parents=True)
    with open(snap_dir / "primary_keys.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["table_name", "column_name"])
        writer.writeheader()
        writer.writerow({"table_name": "T1", "column_name": "Id"})
        writer.writerow({"table_name": "T2", "column_name": "Id"})
    answer_items = get_answer_atomic_items(tmp_path, config)

    reports_dir = tmp_path / "submissions" / "sub1" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    details, total_score, _rev_count, _err_count = score_submission(
        "sub1", "OK", tmp_path, config, _pk_rubric_row(), [], answer_items
    )

    assert answer_items["primary_keys"] == ["T1", "T2"]
    assert total_score == 0.0
    assert details[0]["status"] == "T1:MISSING,T2:MISSING"
    assert details[0]["message"] == "Passed 0/2 items"


def test_scoring_handles_blank_view_numeric_fields(tmp_path, mock_config_file):
    run_dir = tmp_path
    config = load_config(str(mock_config_file))
    answer_items = {
        "tables": [], "columns": [], "primary_keys": [], "foreign_keys": [], "row_counts": [],
        "views": ["Cau1"]
    }
    rubric = [{
        "section": "B", "component": "views", "scope": "Cau1", "object_name": "",
        "total_points": 1.0, "scoring_mode": "weighted_subchecks",
        "include_statuses": "VIEW_PASS|VIEW_OUTPUT_MATCH",
        "partial_policy": "partial_view", "notes": ""
    }]

    reports_dir = run_dir / "submissions" / "sub1" / "reports"
    reports_dir.mkdir(parents=True)
    with open(reports_dir / "view_test_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "answer_view", "student_view", "status", "missing_columns",
            "row_count_answer", "row_count_student", "answer_minus_student_count",
            "student_minus_answer_count", "value_mismatch_count", "schema_score",
            "row_count_score", "value_score", "order_score", "total_match_score",
        ])
        w.writerow(["Cau1", "", "VIEW_SQL_DEFINITION_MISSING", "", "", "", "", "", "", "", "", "", "", ""])

    details, total_score, _rev_count, _err_count = score_submission(
        "sub1", "OK", run_dir, config, rubric, [], answer_items
    )

    assert total_score == 0.0
    assert details[0]["status"] == "VIEW_SQL_DEFINITION_MISSING"
