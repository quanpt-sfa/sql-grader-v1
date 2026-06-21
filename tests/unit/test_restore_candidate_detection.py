from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dbcheck.cli.snapshot import (
    discover_restore_candidates,
    select_backup_candidate,
    validate_restore_candidates,
    run_snapshot,
)
from dbcheck.sqlserver.restore import validate_sqlserver_backup


def _touch(path: Path, text: str = "data") -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_discover_restore_candidates_is_extension_agnostic_and_skips_irrelevant(tmp_path):
    valid_bak = _touch(tmp_path / "student1.bak")
    no_ext = _touch(tmp_path / "student2")
    wrong_ext = _touch(tmp_path / "student3.txt")
    dat_ext = _touch(tmp_path / "student4.dat")
    _touch(tmp_path / "notes.zip")
    _touch(tmp_path / "~$lock.bak")
    _touch(tmp_path / ".DS_Store")
    (tmp_path / "empty.bak").write_text("", encoding="utf-8")

    candidates = discover_restore_candidates(tmp_path)

    assert candidates == [valid_bak, no_ext, wrong_ext, dat_ext]


def test_validate_sqlserver_backup_uses_restore_metadata_not_extension(tmp_path):
    backup_without_extension = _touch(tmp_path / "student1")
    db_conn = MagicMock()
    db_conn.execute_query.side_effect = [
        [{"BackupName": "ok"}],
        [
            {"LogicalName": "data", "PhysicalName": "x.mdf", "Type": "D"},
            {"LogicalName": "log", "PhysicalName": "x.ldf", "Type": "L"},
        ],
    ]

    ok, message = validate_sqlserver_backup(db_conn, backup_without_extension)

    assert ok is True
    assert message == ""
    assert db_conn.execute_query.call_args_list[0].args[0] == "RESTORE HEADERONLY FROM DISK = ?"
    assert db_conn.execute_query.call_args_list[1].args[0] == "RESTORE FILELISTONLY FROM DISK = ?"


def test_validate_sqlserver_backup_rejects_invalid_file_named_bak(tmp_path):
    fake_bak = _touch(tmp_path / "not_a_backup.bak")
    db_conn = MagicMock()
    db_conn.execute_query.side_effect = Exception("media family is incorrectly formed")

    ok, message = validate_sqlserver_backup(db_conn, fake_bak)

    assert ok is False
    assert "restore_metadata_failed" in message
    assert "media family" in message


def test_validate_restore_candidates_keeps_only_metadata_valid_files(tmp_path):
    invalid_bak = _touch(tmp_path / "invalid.bak")
    valid_txt = _touch(tmp_path / "valid.txt")

    def fake_validate(_conn, path):
        return (path == valid_txt, "" if path == valid_txt else "restore_metadata_failed")

    with patch("dbcheck.cli.snapshot.validate_sqlserver_backup", side_effect=fake_validate):
        valid = validate_restore_candidates(MagicMock(), [invalid_bak, valid_txt])

    assert [row["path"] for row in valid] == [valid_txt]


def test_select_backup_candidate_prefers_submission_id_name_match(tmp_path):
    older = _touch(tmp_path / "other.backup")
    newer = _touch(tmp_path / "student_12345.dat")
    candidates = [{"path": older}, {"path": newer}]

    selected = select_backup_candidate(candidates, "12345")

    assert selected["path"] == newer


def test_select_backup_candidate_prefers_most_recent_when_no_name_match(tmp_path):
    older = _touch(tmp_path / "first.dat")
    newer = _touch(tmp_path / "second.sqlbak")
    older_mtime = 1000
    newer_mtime = 2000
    import os
    os.utime(older, (older_mtime, older_mtime))
    os.utime(newer, (newer_mtime, newer_mtime))

    selected = select_backup_candidate([{"path": older}, {"path": newer}], "student")

    assert selected["path"] == newer


def test_select_backup_candidate_fails_on_ambiguous_multiple_valid_backups(tmp_path):
    first = _touch(tmp_path / "first.dat")
    second = _touch(tmp_path / "second.txt")
    import os
    os.utime(first, (1000, 1000))
    os.utime(second, (1000, 1000))

    with pytest.raises(ValueError, match="multiple_valid_backups_found"):
        select_backup_candidate([{"path": first}, {"path": second}], "student")


def test_snapshot_restores_valid_wrong_extension_and_rejects_invalid_bak(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("assignment:\n  name: Test\n  protected_answer_db: answer_db\n", encoding="utf-8")
    answer_bak = _touch(tmp_path / "answer.backup")
    submissions_dir = tmp_path / "subs"
    submissions_dir.mkdir()
    invalid_bak = _touch(submissions_dir / "bad_111.bak")
    valid_txt = _touch(submissions_dir / "student_222.txt")

    def fake_validate(_conn, path):
        return (Path(path).resolve() != invalid_bak.resolve(), "" if Path(path).resolve() != invalid_bak.resolve() else "not_a_sql_server_backup")

    with patch("dbcheck.cli.snapshot.SQLServerConnection", return_value=MagicMock()), \
         patch("dbcheck.cli.snapshot.validate_sqlserver_backup", side_effect=fake_validate), \
         patch("dbcheck.cli.snapshot.restore_database") as restore_mock, \
         patch("dbcheck.cli.snapshot.drop_database"), \
         patch("dbcheck.cli.snapshot._extract_full_snapshot", return_value=({}, [])):
        run_snapshot(SimpleNamespace(
            config=str(config_file),
            run_dir=str(tmp_path / "run"),
            answer_bak=str(answer_bak),
            answer_db=None,
            submissions=str(submissions_dir),
        ))

    restored_paths = [call.args[1].resolve() for call in restore_mock.call_args_list]
    assert valid_txt.resolve() in restored_paths
    assert invalid_bak.resolve() not in restored_paths

    manifest = (tmp_path / "run" / "manifest.csv").read_text(encoding="utf-8")
    assert "RESTORE_METADATA_FAILED" in manifest
    assert "student_222.txt" in manifest
