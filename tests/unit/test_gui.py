import sys
from pathlib import Path
from dbcheck.gui.app import (
    check_run_dir_nesting,
    validate_inputs,
    build_snapshot_command,
    build_compare_structure_command,
    build_test_views_command,
    build_export_results_command
)

def test_check_run_dir_nesting():
    # Regular valid paths
    assert not check_run_dir_nesting("runs/run_20260620_180000")
    assert not check_run_dir_nesting("my_runs/run_1")
    assert not check_run_dir_nesting("runs")
    assert not check_run_dir_nesting("")
    
    # Nested runs directory
    assert check_run_dir_nesting("runs/runs/run_20260620_180000")
    assert check_run_dir_nesting("runs/run_20260620_180000/runs")
    assert check_run_dir_nesting("runs/runs")
    
    # Adjacent duplicate directory segments of any kind
    assert check_run_dir_nesting("project/project/run_1")
    assert check_run_dir_nesting("runs/run_1/run_1")
    
    # Windows-style backslashes
    assert check_run_dir_nesting("runs\\runs\\run_1")
    assert not check_run_dir_nesting("runs\\run_1")


def test_validate_inputs(tmp_path):
    # Setup temporary files/folders to mock real paths
    cfg_file = tmp_path / "assignment.yaml"
    cfg_file.write_text("assignment:\n  name: Test")
    
    ans_bak = tmp_path / "dapan.bak"
    ans_bak.write_text("dummy bak")
    
    subs_dir = tmp_path / "exams"
    subs_dir.mkdir()
    
    td_dir = tmp_path / "test_data"
    td_dir.mkdir()
    
    run_dir = tmp_path / "runs" / "run_1"
    
    # 1. Full pipeline validation with all paths existing
    errors = validate_inputs(
        answer_bak=str(ans_bak),
        submissions=str(subs_dir),
        config=str(cfg_file),
        test_data=str(td_dir),
        run_dir=str(run_dir),
        command="full"
    )
    assert not errors, f"Expected no validation errors, got: {errors}"

    # 2. Validation fails when config file does not exist
    errors = validate_inputs(
        answer_bak=str(ans_bak),
        submissions=str(subs_dir),
        config="non_existent_config.yaml",
        test_data=str(td_dir),
        run_dir=str(run_dir),
        command="full"
    )
    assert any("Configuration file does not exist" in e for e in errors)

    # 3. Validation fails when run_dir is empty
    errors = validate_inputs(
        answer_bak=str(ans_bak),
        submissions=str(subs_dir),
        config=str(cfg_file),
        test_data=str(td_dir),
        run_dir="",
        command="full"
    )
    assert any("Run directory must not be empty" in e for e in errors)

    # 4. Snapshot validation fails when submissions or answer_bak are missing
    errors = validate_inputs(
        answer_bak="missing_ans.bak",
        submissions=str(subs_dir),
        config=str(cfg_file),
        test_data=str(td_dir),
        run_dir=str(run_dir),
        command="snapshot"
    )
    assert any("Answer backup file does not exist" in e for e in errors)

    errors = validate_inputs(
        answer_bak=str(ans_bak),
        submissions="missing_exams_folder",
        config=str(cfg_file),
        test_data=str(td_dir),
        run_dir=str(run_dir),
        command="snapshot"
    )
    assert any("Student submissions folder does not exist" in e for e in errors)

    # 5. Test-views validation fails when test data folder is missing
    errors = validate_inputs(
        answer_bak=str(ans_bak),
        submissions=str(subs_dir),
        config=str(cfg_file),
        test_data="missing_test_data",
        run_dir=str(run_dir),
        command="test-views"
    )
    assert any("Test data folder does not exist" in e for e in errors)

    # 6. Nested run directory validation warning
    errors = validate_inputs(
        answer_bak=str(ans_bak),
        submissions=str(subs_dir),
        config=str(cfg_file),
        test_data=str(td_dir),
        run_dir=str(tmp_path / "runs" / "runs" / "run_1"),
        command="full"
    )
    assert any("Run directory contains nested 'runs' segments" in e for e in errors)


def test_build_commands():
    # Verify command line array construction
    ans = "solution/dapan.bak"
    subs = "exams"
    run = "runs/run_test"
    cfg = "configs/assignment.yaml"
    td = "test_data"

    # Snapshot
    snap_cmd = build_snapshot_command(ans, subs, run, cfg)
    assert snap_cmd[0] == sys.executable
    assert "main.py" in snap_cmd[1]
    assert snap_cmd[2] == "snapshot"
    assert snap_cmd[snap_cmd.index("--answer-bak") + 1] == str(Path(ans))
    assert snap_cmd[snap_cmd.index("--submissions") + 1] == str(Path(subs))
    assert snap_cmd[snap_cmd.index("--run-dir") + 1] == str(Path(run))
    assert snap_cmd[snap_cmd.index("--config") + 1] == str(Path(cfg))

    # Compare Structure
    comp_cmd = build_compare_structure_command(run, cfg)
    assert comp_cmd[0] == sys.executable
    assert "main.py" in comp_cmd[1]
    assert comp_cmd[2] == "compare-structure"
    assert comp_cmd[comp_cmd.index("--run-dir") + 1] == str(Path(run))
    assert comp_cmd[comp_cmd.index("--config") + 1] == str(Path(cfg))

    # Test Views without optional answer backup
    view_cmd = build_test_views_command(run, td, cfg)
    assert view_cmd[0] == sys.executable
    assert "main.py" in view_cmd[1]
    assert view_cmd[2] == "test-views"
    assert view_cmd[view_cmd.index("--run-dir") + 1] == str(Path(run))
    assert view_cmd[view_cmd.index("--test-data") + 1] == str(Path(td))
    assert view_cmd[view_cmd.index("--config") + 1] == str(Path(cfg))
    assert "--answer-bak" not in view_cmd

    # Test Views with optional answer backup
    view_cmd_bak = build_test_views_command(run, td, cfg, ans)
    assert view_cmd_bak[view_cmd_bak.index("--answer-bak") + 1] == str(Path(ans))


def test_gui_additional_command_construction():
    ans = "solution/dapan.bak"
    subs = "exams"
    run = "runs/run_test"
    cfg = "configs/assignment.yaml"
    td = "test_data"

    # 1. compare_existing_data execution mode does not include --test-data
    view_cmd_exist = build_test_views_command(
        run_dir=run,
        test_data=td,
        config=cfg,
        answer_bak=ans,
        execution_mode="compare_existing_data"
    )
    assert "--test-data" not in view_cmd_exist
    assert view_cmd_exist[view_cmd_exist.index("--answer-bak") + 1] == str(Path(ans))

    # 2. compare_seeded_test_data execution mode includes --test-data
    view_cmd_seeded = build_test_views_command(
        run_dir=run,
        test_data=td,
        config=cfg,
        answer_bak=ans,
        execution_mode="compare_seeded_test_data"
    )
    assert "--test-data" in view_cmd_seeded
    assert view_cmd_seeded[view_cmd_seeded.index("--test-data") + 1] == str(Path(td))

    # 3. Full pipeline queue order is snapshot -> compare-structure -> test-views -> export-results
    snap = build_snapshot_command(ans, subs, run, cfg)
    comp = build_compare_structure_command(run, cfg)
    views = build_test_views_command(run, td, cfg, ans, "compare_existing_data")
    export = build_export_results_command(run, cfg)

    assert snap[2] == "snapshot"
    assert comp[2] == "compare-structure"
    assert views[2] == "test-views"
    assert export[2] == "export-results"
