import sys
import json
import csv
from pathlib import Path
import pytest
from dbcheck.config import load_config
from dbcheck.cli.score_results import run_score_results
from dbcheck.gui.app import (
    validate_inputs,
    build_score_results_command
)

class DummyArgs:
    def __init__(self, run_dir, config, rubric=None, overrides=None):
        self.run_dir = run_dir
        self.config = config
        self.rubric = rubric
        self.overrides = overrides

def test_rubric_resolves_and_copies_and_hashes(tmp_path):
    # Create config file with scoring section pointing to a mock rubric file
    rubric_src = tmp_path / "my_rubric.csv"
    with open(rubric_src, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["section", "component", "scope", "object_name", "total_points", "scoring_mode", "include_statuses", "partial_policy", "notes"])
        writer.writerow(["A.1", "tables", "all", "", "1.0", "proportional", "TABLE_MATCHED_EXACT", "review_pending", "Test"])
        writer.writerow(["B", "views", "Cau1", "", "9.0", "weighted_subchecks", "VIEW_PASS", "partial_view", "Test"])
        
    cfg_file = tmp_path / "assignment.yaml"
    cfg_file.write_text(f"""
assignment:
  name: Test Exam
  protected_answer_db: "00000001"
schema:
  excluded_tables: []
views:
  mode: answer_snapshot
  execution_mode: compare_existing_data
  expected: []
scoring:
  rubric_path: "{rubric_src.as_posix()}"
  copy_rubric_to_run: true
  run_rubric_filename: "rubric_used.csv"
  manual_overrides_filename: "manual_overrides.csv"
""")

    # Setup run folder with manifest and answer snapshot
    run_dir = tmp_path / "runs" / "run_1"
    run_dir.mkdir(parents=True)
    
    ans_snap = run_dir / "answer_snapshot"
    ans_snap.mkdir()
    (ans_snap / "tables.csv").write_text("table_name\nHangHoa\n")
    (ans_snap / "columns.csv").write_text("table_name,column_name\nHangHoa,MaHangHoa\n")
    (ans_snap / "views.csv").write_text("view_name\n")
    
    manifest = run_dir / "manifest.csv"
    manifest.write_text("submission_id,status\n123,OK\n")
    
    reports_dir = run_dir / "submissions" / "123" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "table_mapping_report.csv").write_text("answer_table,match_status\nHangHoa,TABLE_MATCHED_EXACT\n")
    (reports_dir / "column_mapping_report.csv").write_text("answer_table,answer_column,match_status\nHangHoa,MaHangHoa,COLUMN_MATCHED_EXACT\n")
    (reports_dir / "structure_report.csv").write_text("component,answer_object,status\ntable,HangHoa,PASS\n")
    
    # Run CLI scoring without explicit --rubric
    args = DummyArgs(run_dir=str(run_dir), config=str(cfg_file), rubric=None, overrides=None)
    run_score_results(args)
    
    # Verify rubric_used.csv is created in run_dir
    rubric_used = run_dir / "rubric_used.csv"
    assert rubric_used.exists()
    
    # Verify rubric_used.sha256 is created and contains correct hash
    sha_file = run_dir / "rubric_used.sha256"
    assert sha_file.exists()
    sha_val = sha_file.read_text(encoding="utf-8").strip()
    assert len(sha_val) == 64
    
    # Verify scoring_metadata.json is created and contains the hash
    meta_file = run_dir / "scoring_metadata.json"
    assert meta_file.exists()
    with open(meta_file, "r", encoding="utf-8") as f:
        meta = json.load(f)
        assert meta["rubric_sha256"] == sha_val
        assert meta["rubric_source_path"] == str(rubric_src)
        assert "scored_at" in meta
        
    # Verify grading_detail.csv exists
    assert (run_dir / "grading_detail.csv").exists()
    assert (run_dir / "grading_summary.csv").exists()
    assert (run_dir / "grading_summary.xlsx").exists()


def test_rubric_overrides_applied(tmp_path):
    rubric_src = tmp_path / "my_rubric.csv"
    with open(rubric_src, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["section", "component", "scope", "object_name", "total_points", "scoring_mode", "include_statuses", "partial_policy", "notes"])
        writer.writerow(["A.1", "tables", "all", "", "1.0", "proportional", "TABLE_MATCHED_EXACT", "review_pending", "Test"])
        writer.writerow(["C", "manual", "PartC", "", "9.0", "manual", "", "", "Manual override component"])
        
    cfg_file = tmp_path / "assignment.yaml"
    cfg_file.write_text(f"""
assignment:
  name: Test Exam
  protected_answer_db: "00000001"
schema:
  excluded_tables: []
views:
  mode: answer_snapshot
  execution_mode: compare_existing_data
  expected: []
scoring:
  rubric_path: "{rubric_src.as_posix()}"
""")

    run_dir = tmp_path / "runs" / "run_2"
    run_dir.mkdir(parents=True)
    ans_snap = run_dir / "answer_snapshot"
    ans_snap.mkdir()
    (ans_snap / "tables.csv").write_text("table_name\nHangHoa\n")
    (ans_snap / "columns.csv").write_text("table_name,column_name\nHangHoa,MaHangHoa\n")
    
    manifest = run_dir / "manifest.csv"
    manifest.write_text("submission_id,status\n123,OK\n")
    
    reports_dir = run_dir / "submissions" / "123" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "table_mapping_report.csv").write_text("answer_table,match_status\nHangHoa,TABLE_MATCHED_EXACT\n")
    (reports_dir / "structure_report.csv").write_text("component,answer_object,status\ntable,HangHoa,PASS\n")

    # Write manual override CSV
    overrides_csv = run_dir / "manual_overrides.csv"
    with open(overrides_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["submission_id", "section", "component", "answer_object", "override_points", "override_status", "reviewer_note"])
        # Override the manual grading part with 7.5 points
        writer.writerow(["123", "C", "manual", "PartC", "7.5", "PASS", "Overridden score"])

    # Run scoring
    args = DummyArgs(run_dir=str(run_dir), config=str(cfg_file), rubric=None, overrides=None)
    run_score_results(args)

    # Read summary and check final score
    summary_path = run_dir / "grading_summary.csv"
    assert summary_path.exists()
    with open(summary_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        assert len(rows) == 1
        # 1.0 (tables) + 7.5 (override) = 8.5
        assert float(rows[0]["final_score"]) == 8.5
        assert float(rows[0]["auto_score"]) == 1.0


def test_gui_build_command_and_validate(tmp_path):
    cfg_file = tmp_path / "assignment.yaml"
    cfg_file.write_text("""
assignment:
  name: Test Exam
schema:
  excluded_tables: []
scoring:
  rubric_path: "configs/rubrics/purchase_payment_ca3_rubric.csv"
""")
    
    # 1. build_score_results_command omits --rubric because config defines it
    cmd = build_score_results_command(str(tmp_path / "runs" / "run_3"), str(cfg_file))
    assert "--rubric" not in cmd
    
    # 2. validate_inputs does not require rubric file or fail for score-results
    errors = validate_inputs(
        answer_bak="",
        submissions="",
        config=str(cfg_file),
        test_data="",
        run_dir=str(tmp_path / "runs" / "run_3"),
        command="score-results",
        execution_mode="compare_existing_data"
    )
    assert not errors
