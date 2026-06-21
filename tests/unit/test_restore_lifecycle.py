import csv
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dbcheck.cli.snapshot import run_snapshot
from dbcheck.cli.test_views import run_test_views
from dbcheck.config import AssignmentConfig
from dbcheck.snapshot.reader import read_full_snapshot
from dbcheck.snapshot.writer import write_full_snapshot


def _config(mode="compare_rewritten_sql_on_answer_db", fallback=False):
    return AssignmentConfig({
        "assignment": {"name": "Lifecycle", "protected_answer_db": "answer_db"},
        "schema": {
            "matching_threshold": 0.8,
            "aliases": {"tables": {}, "columns": {"global": {}, "by_table": {}}},
            "abbreviations": {},
        },
        "views": {
            "mode": "answer_snapshot",
            "execution_mode": mode,
            "sql_rewrite": {
                "restore_student_db_fallback": fallback,
                "restore_answer_once_per_run": True,
                "use_snapshot_view_definitions": True,
            },
            "expected": [
                {"answer_view": "Cau1", "expected_output": {"columns": []}},
            ],
        },
    })


def _write_manifest(run_dir, submissions):
    with open(run_dir / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "submission_id", "source_path", "status", "error_code",
            "error_message", "temp_database", "started_at", "finished_at"
        ])
        writer.writeheader()
        for sub_id, source_path in submissions:
            writer.writerow({
                "submission_id": sub_id,
                "source_path": str(source_path),
                "status": "OK",
            })


def _write_minimal_run(run_dir, submissions, include_view_definitions=True):
    write_full_snapshot(run_dir / "answer_snapshot", {
        "views": [{"submission_id": "answer", "view_name": "Cau1", "view_name_canonical": "Cau1"}],
        "view_columns": [],
        "view_definitions": [{
            "submission_id": "answer",
            "role": "answer",
            "view_schema": "dbo",
            "view_name": "Cau1",
            "view_name_canonical": "Cau1",
            "definition_found": True,
            "raw_definition": "CREATE VIEW Cau1 AS SELECT 1 AS x",
            "extract_status": "VIEW_SQL_EXTRACTED",
        }],
    })
    _write_manifest(run_dir, submissions)
    for sub_id, _source_path in submissions:
        sub_root = run_dir / "submissions" / sub_id
        reports = sub_root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "table_mapping_report.csv").write_text(
            "student_table,answer_table,match_status\n", encoding="utf-8"
        )
        (reports / "column_mapping_report.csv").write_text(
            "student_table,student_column,answer_column,match_status\n", encoding="utf-8"
        )
        snapshot = {
            "views": [{"submission_id": sub_id, "view_name": "Cau1", "view_name_canonical": "Cau1"}],
            "columns": [],
        }
        if include_view_definitions:
            snapshot["view_definitions"] = [{
                "submission_id": sub_id,
                "role": "student",
                "view_schema": "dbo",
                "view_name": "Cau1",
                "view_name_canonical": "Cau1",
                "definition_found": True,
                "raw_definition": "CREATE VIEW Cau1 AS SELECT 1 AS x",
                "extract_status": "VIEW_SQL_EXTRACTED",
            }]
        write_full_snapshot(sub_root / "snapshot", snapshot)


def test_snapshot_writer_reader_supports_view_definitions(tmp_path):
    snap_dir = tmp_path / "submissions" / "sub1" / "snapshot"
    write_full_snapshot(snap_dir, {
        "view_definitions": [{
            "submission_id": "sub1",
            "role": "student",
            "view_schema": "dbo",
            "view_name": "Cau1",
            "view_name_canonical": "Cau1",
            "definition_found": True,
            "raw_definition": "CREATE VIEW Cau1 AS SELECT 1 AS x",
            "extract_status": "VIEW_SQL_EXTRACTED",
        }],
    })

    snap = read_full_snapshot(snap_dir)
    assert snap["view_definitions"][0]["raw_definition"] == "CREATE VIEW Cau1 AS SELECT 1 AS x"
    raw_path = tmp_path / "submissions" / "sub1" / "view_sql" / "raw" / "dbo.Cau1.sql"
    assert raw_path.read_text(encoding="utf-8") == "CREATE VIEW Cau1 AS SELECT 1 AS x"

    empty_snap = read_full_snapshot(tmp_path / "missing_snapshot")
    assert empty_snap["view_definitions"] == []


def test_snapshot_restores_student_once_and_drops_after_extraction(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("assignment:\n  name: Test\n  protected_answer_db: answer_db\n", encoding="utf-8")
    answer_bak = tmp_path / "answer.bak"
    answer_bak.write_text("", encoding="utf-8")
    submissions_dir = tmp_path / "subs"
    submissions_dir.mkdir()
    student_bak = submissions_dir / "student1.bak"
    student_bak.write_text("backup bytes", encoding="utf-8")

    events = []

    def mark(name):
        def _inner(*args, **kwargs):
            events.append(name)
            return []
        return _inner

    def fake_restore(_conn, _bak, db_name):
        events.append(f"restore:{db_name}")

    def fake_drop(_conn, db_name):
        events.append(f"drop:{db_name}")

    with patch("dbcheck.cli.snapshot.SQLServerConnection", return_value=MagicMock()), \
         patch("dbcheck.cli.snapshot.restore_database", side_effect=fake_restore) as restore_mock, \
         patch("dbcheck.cli.snapshot.validate_sqlserver_backup", return_value=(True, "")), \
         patch("dbcheck.cli.snapshot.drop_database", side_effect=fake_drop), \
         patch("dbcheck.cli.snapshot.get_tables", side_effect=mark("tables")), \
         patch("dbcheck.cli.snapshot.get_columns", side_effect=mark("columns")), \
         patch("dbcheck.cli.snapshot.get_primary_keys", side_effect=mark("primary_keys")), \
         patch("dbcheck.cli.snapshot.get_foreign_keys", side_effect=mark("foreign_keys")), \
         patch("dbcheck.cli.snapshot.get_views", side_effect=mark("views")), \
         patch("dbcheck.cli.snapshot.get_view_columns", side_effect=mark("view_columns")), \
         patch("dbcheck.cli.snapshot.get_unique_constraints", side_effect=mark("unique_constraints")), \
         patch("dbcheck.cli.snapshot.get_view_definitions", side_effect=mark("view_definitions")):
        run_snapshot(SimpleNamespace(
            config=str(config_file),
            run_dir=str(tmp_path / "run"),
            answer_bak=str(answer_bak),
            answer_db=None,
            submissions=str(submissions_dir),
        ))

    student_restore_calls = [
        call for call in restore_mock.call_args_list
        if call.args[1] == student_bak
    ]
    assert len(student_restore_calls) == 1
    student_drop_index = max(i for i, event in enumerate(events) if event.startswith("drop:grade_tmp_student1_"))
    assert student_drop_index > max(events.index(name) for name in [
        "tables", "columns", "primary_keys", "foreign_keys",
        "views", "view_columns", "unique_constraints", "view_definitions",
    ])


def test_mapped_sql_restores_answer_once_and_skips_student_restore(tmp_path):
    answer_bak = tmp_path / "answer.bak"
    answer_bak.write_text("", encoding="utf-8")
    sub1_bak = tmp_path / "s1.bak"
    sub2_bak = tmp_path / "s2.bak"
    sub1_bak.write_text("", encoding="utf-8")
    sub2_bak.write_text("", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_minimal_run(run_dir, [("s1", sub1_bak), ("s2", sub2_bak)])

    config = _config()
    captured_defs = []

    def fake_run_view_testing(*args, **kwargs):
        captured_defs.append(kwargs["student_view_definitions"])
        return []

    with patch("dbcheck.cli.test_views.load_config", return_value=config), \
         patch("dbcheck.cli.test_views.SQLServerConnection", return_value=MagicMock()), \
         patch("dbcheck.cli.test_views.restore_database") as restore_mock, \
         patch("dbcheck.cli.test_views.drop_database") as drop_mock, \
         patch("dbcheck.cli.test_views.run_view_testing", side_effect=fake_run_view_testing), \
         patch("dbcheck.cli.test_views.compile_summary"), \
         patch("dbcheck.cli.test_views._try_protected_db_audit", return_value=None), \
         patch("dbcheck.cli.test_views._audit_transactions"):
        run_test_views(SimpleNamespace(
            config="config.yaml",
            run_dir=str(run_dir),
            answer_bak=str(answer_bak),
            test_data=None,
        ))

    assert restore_mock.call_count == 1
    assert restore_mock.call_args.args[1] == answer_bak
    assert drop_mock.call_count == 1
    assert captured_defs and len(captured_defs) == 2
    assert all(defs[0]["view_name"] == "Cau1" for defs in captured_defs)


def test_missing_view_definitions_without_fallback_does_not_restore_student(tmp_path):
    answer_bak = tmp_path / "answer.bak"
    answer_bak.write_text("", encoding="utf-8")
    sub_bak = tmp_path / "s1.bak"
    sub_bak.write_text("", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_minimal_run(run_dir, [("s1", sub_bak)], include_view_definitions=False)

    with patch("dbcheck.cli.test_views.load_config", return_value=_config(fallback=False)), \
         patch("dbcheck.cli.test_views.SQLServerConnection", return_value=MagicMock()), \
         patch("dbcheck.cli.test_views.restore_database") as restore_mock, \
         patch("dbcheck.cli.test_views.drop_database"), \
         patch("dbcheck.cli.test_views.compile_summary"), \
         patch("dbcheck.cli.test_views._try_protected_db_audit", return_value=None), \
         patch("dbcheck.cli.test_views._audit_transactions"):
        run_test_views(SimpleNamespace(
            config="config.yaml",
            run_dir=str(run_dir),
            answer_bak=str(answer_bak),
            test_data=None,
        ))

    assert restore_mock.call_count == 1
    assert restore_mock.call_args.args[1] == answer_bak
    report = run_dir / "submissions" / "s1" / "reports" / "view_test_report.csv"
    assert "VIEW_SQL_DEFINITION_MISSING" in report.read_text(encoding="utf-8")


def test_legacy_modes_still_restore_student_per_submission(tmp_path):
    answer_bak = tmp_path / "answer.bak"
    answer_bak.write_text("", encoding="utf-8")
    sub_bak = tmp_path / "s1.bak"
    sub_bak.write_text("", encoding="utf-8")

    for mode in ("compare_existing_data", "compare_seeded_test_data"):
        run_dir = tmp_path / mode
        run_dir.mkdir()
        _write_minimal_run(run_dir, [("s1", sub_bak)])
        test_data = tmp_path / "test_data"
        test_data.mkdir(exist_ok=True)

        with patch("dbcheck.cli.test_views.load_config", return_value=_config(mode=mode)), \
             patch("dbcheck.cli.test_views.SQLServerConnection", return_value=MagicMock()), \
             patch("dbcheck.cli.test_views.restore_database") as restore_mock, \
             patch("dbcheck.cli.test_views.drop_database"), \
             patch("dbcheck.cli.test_views.run_view_testing", return_value=[]), \
             patch("dbcheck.cli.test_views.compile_summary"), \
             patch("dbcheck.cli.test_views._try_protected_db_audit", return_value=None), \
             patch("dbcheck.cli.test_views._audit_transactions"), \
             patch("dbcheck.sqlserver.test_data_loader.seed_database"):
            run_test_views(SimpleNamespace(
                config="config.yaml",
                run_dir=str(run_dir),
                answer_bak=str(answer_bak),
                test_data=str(test_data) if mode == "compare_seeded_test_data" else None,
            ))

        restored_sources = [call.args[1] for call in restore_mock.call_args_list]
        assert sub_bak in restored_sources
