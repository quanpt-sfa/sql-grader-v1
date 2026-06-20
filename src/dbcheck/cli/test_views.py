"""
cli/test_views.py — Entry point for the `test-views` sub-command.

Supports:
  - compare_existing_data  (default): restore DBs, SELECT, no seeding.
  - compare_seeded_test_data (legacy): seed CSV test data before querying.

Guardrails:
  - --test-data is optional; only required in compare_seeded_test_data mode.
  - Protected DB audit is optional: if DB is unavailable, log PROTECTED_DB_AUDIT_SKIPPED.
  - On global command failure, summary sets view_test_status=COMMAND_ERROR and
    view_required_count = number of views in answer snapshot.
"""

import os
import csv
from pathlib import Path
from datetime import datetime

from dbcheck.config import load_config
from dbcheck.sqlserver.connection import SQLServerConnection
from dbcheck.sqlserver.restore import restore_database, drop_database, get_sql_data_dir
from dbcheck.snapshot.reader import read_full_snapshot
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.views.view_reporter import run_view_testing
from dbcheck.utils.manifest import ManifestManager
from dbcheck.utils.summary import compile_summary
from dbcheck.utils.logging import get_logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_copy_only_backup(db_conn: SQLServerConnection, src_db: str, run_id: str) -> Path:
    """Create a copy-only backup of the protected database as a fallback."""
    logger = get_logger()
    data_dir = get_sql_data_dir(db_conn)
    bak_file = data_dir / f"{src_db}_copy_only_{run_id}.bak"
    logger.info(f"Creating copy-only backup of protected DB '{src_db}' to '{bak_file}'...")
    sql = "BACKUP DATABASE [?] TO DISK = ? WITH COPY_ONLY, FORMAT, INIT"
    db_conn.execute_non_query(
        f"BACKUP DATABASE [{src_db}] TO DISK = ? WITH COPY_ONLY, FORMAT, INIT",
        [str(bak_file)],
        autocommit=True,
    )
    return bak_file


def _get_db_counts(db_conn: SQLServerConnection, db_name: str) -> dict:
    """Count tables, views, and total rows in a database (for audit)."""
    tables_cnt = db_conn.execute_query(
        "SELECT COUNT(*) as cnt FROM sys.tables WHERE is_ms_shipped = 0", db_name=db_name
    )[0]["cnt"]
    views_cnt = db_conn.execute_query(
        "SELECT COUNT(*) as cnt FROM sys.views WHERE is_ms_shipped = 0", db_name=db_name
    )[0]["cnt"]
    rows_res = db_conn.execute_query(
        """
        SELECT SUM(p.rows) as cnt
        FROM sys.tables t
        JOIN sys.partitions p ON t.object_id = p.object_id
        WHERE t.is_ms_shipped = 0 AND p.index_id IN (0,1)
        """,
        db_name=db_name,
    )
    rows_cnt = rows_res[0]["cnt"] if rows_res and rows_res[0]["cnt"] is not None else 0
    return {"tables_count": tables_cnt, "views_count": views_cnt, "row_count": rows_cnt}


def _try_protected_db_audit(
    db_conn: SQLServerConnection, protected_db: str, stage: str
) -> dict | None:
    """Attempt to audit the protected DB. Returns counts dict or None if unavailable."""
    logger = get_logger()
    try:
        counts = _get_db_counts(db_conn, protected_db)
        logger.info(f"Protected DB '{protected_db}' {stage} counts: {counts}")
        return counts
    except Exception as e:
        logger.warning(
            f"[PROTECTED_DB_AUDIT_SKIPPED] Cannot audit protected DB '{protected_db}' "
            f"at {stage}: {e}. Continuing."
        )
        return None


def _audit_transactions(db_conn: SQLServerConnection) -> None:
    logger = get_logger()
    sql = """
    SELECT session_id, open_transaction_count, host_name, login_name, program_name
    FROM sys.dm_exec_sessions
    WHERE open_transaction_count > 0
    """
    try:
        rows = db_conn.execute_query(sql)
        if not rows:
            logger.info("Transaction audit: No open transactions found.")
            return
        logger.warning(f"Transaction audit: {len(rows)} session(s) with open transactions.")
        ctx = db_conn.execute_query("SELECT @@SPID as spid, HOST_NAME() as host, ORIGINAL_LOGIN() as login")[0]
        local_offenders = [
            r for r in rows
            if r["session_id"] != ctx["spid"]
            and r["host_name"] == ctx["host"]
            and r["login_name"] == ctx["login"]
            and "python" in str(r.get("program_name", "")).lower()
        ]
        if local_offenders:
            raise AssertionError(
                f"TRANSACTION CLEANUP ERROR: Active Python session(s) left open transactions: {local_offenders}"
            )
    except AssertionError:
        raise
    except Exception as e:
        logger.error(f"Failed to query transaction audit DMV: {e}")


def _count_answer_snapshot_views(answer_snapshot_dir: Path) -> int:
    """Return the number of views in answer_snapshot/views.csv."""
    views_csv = answer_snapshot_dir / "views.csv"
    if not views_csv.exists():
        return 0
    try:
        with open(views_csv, "r", encoding="utf-8") as f:
            return sum(1 for _ in csv.DictReader(f))
    except Exception:
        return 0


def _write_command_error_summary(
    run_dir: Path,
    submissions: list,
    answer_view_count: int,
    error_message: str,
) -> None:
    """Write a summary.csv with view_test_status=COMMAND_ERROR for all submissions."""
    logger = get_logger()
    summary_path = run_dir / "summary.csv"
    from dbcheck.utils.summary import SUMMARY_HEADERS
    rows = []
    for sub in submissions:
        row = {h: "" for h in SUMMARY_HEADERS}
        row["submission_id"] = sub["submission_id"]
        row["manifest_status"] = sub.get("status", "")
        row["manifest_error"] = sub.get("error_message", "")
        row["view_required_count"] = answer_view_count
        row["view_test_status"] = "COMMAND_ERROR"
        rows.append(row)
    try:
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SUMMARY_HEADERS)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
        logger.info(f"COMMAND_ERROR summary written to: {summary_path}")
    except Exception as e:
        logger.error(f"Failed to write COMMAND_ERROR summary: {e}")


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

def run_test_views(args):
    logger = get_logger()

    # 1. Load config
    config = load_config(args.config)
    logger.info(f"Loaded config: {config.name}")
    execution_mode = getattr(config, "execution_mode", "compare_existing_data")
    logger.info(f"View execution mode: {execution_mode}")

    # 2. Paths
    run_dir = Path(args.run_dir)
    manifest_path = run_dir / "manifest.csv"
    answer_snapshot_dir = run_dir / "answer_snapshot"

    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.csv not found in: {run_dir}. Run snapshot first.")
    if not answer_snapshot_dir.exists():
        raise FileNotFoundError(f"Answer snapshot missing at: {answer_snapshot_dir}. Run snapshot first.")

    # test_data only required for seeded mode
    test_data_dir: Path | None = None
    if execution_mode == "compare_seeded_test_data":
        if not getattr(args, "test_data", None):
            raise ValueError(
                "--test-data is required when execution_mode is compare_seeded_test_data."
            )
        test_data_dir = Path(args.test_data)
        if not test_data_dir.exists():
            raise FileNotFoundError(f"Test data directory not found: {test_data_dir}")
    elif getattr(args, "test_data", None):
        logger.info(
            f"--test-data provided but execution_mode={execution_mode}; test data will not be used."
        )

    # 3. Read answer snapshot view count (needed for COMMAND_ERROR fallback)
    answer_view_count = _count_answer_snapshot_views(answer_snapshot_dir)

    # 4. Read manifest
    submissions = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            submissions.append(row)

    # 5. DB connection
    db_conn = SQLServerConnection()

    # 6. Optional protected DB pre-run audit
    protected_db = config.protected_answer_db
    pre_counts = _try_protected_db_audit(db_conn, protected_db, "pre-run")

    # 7. Locate answer backup
    answer_bak_path: Path | None = None
    if getattr(args, "answer_bak", None):
        answer_bak_path = Path(args.answer_bak)
    else:
        convention_bak = Path("solution/dapan.bak")
        if convention_bak.exists():
            answer_bak_path = convention_bak
            logger.info(f"Using convention answer backup: {answer_bak_path}")

    # 8. Read full answer snapshot
    ans_snap = read_full_snapshot(answer_snapshot_dir)
    normalizer = NameNormalizer(config)

    # --- Global try block to catch any top-level errors ---
    try:
        ok_count = 0
        total_count = 0
        manifest = ManifestManager(run_dir)

        for sub in submissions:
            sub_id = sub["submission_id"]
            status = sub["status"]

            if status != "OK":
                logger.info(f"Skipping view test for '{sub_id}' (snapshot status={status})")
                continue

            total_count += 1
            bak_file = Path(sub["source_path"])
            student_snapshot_dir = run_dir / "submissions" / sub_id / "snapshot"
            report_path = run_dir / "submissions" / sub_id / "reports" / "view_test_report.csv"
            diff_dir = run_dir / "submissions" / sub_id / "reports"

            stud_snap = read_full_snapshot(student_snapshot_dir)

            run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            temp_stud_db = f"grade_tmp_{sub_id}_{run_id}"
            temp_answer_db = f"grade_tmp_answer_{run_id}"
            temp_bak_to_clean: Path | None = None

            logger.info(f"Testing view behavior for student '{sub_id}'...")

            try:
                # Restore answer DB
                if answer_bak_path and answer_bak_path.exists():
                    restore_database(db_conn, answer_bak_path, temp_answer_db)
                else:
                    try:
                        temp_bak_to_clean = _create_copy_only_backup(db_conn, protected_db, run_id)
                        restore_database(db_conn, temp_bak_to_clean, temp_answer_db)
                    except Exception as fb_err:
                        raise RuntimeError(f"Fallback answer DB copy failed: {fb_err}")

                # Restore student DB
                restore_database(db_conn, bak_file, temp_stud_db)

                # Seed only in seeded mode
                if execution_mode == "compare_seeded_test_data":
                    from dbcheck.sqlserver.test_data_loader import seed_database
                    logger.info("Seeding temporary databases...")
                    seed_defaults_path = (
                        run_dir / "submissions" / sub_id / "reports" / "seeding_synthetic_defaults.csv"
                    )
                    seed_database(
                        db_conn, temp_answer_db, test_data_dir,
                        ans_snap["tables"], ans_snap["columns"], ans_snap["foreign_keys"], normalizer,
                    )
                    seed_database(
                        db_conn, temp_stud_db, test_data_dir,
                        stud_snap["tables"], stud_snap["columns"], stud_snap["foreign_keys"], normalizer,
                        synthetic_defaults_report_path=seed_defaults_path,
                    )
                else:
                    logger.info("compare_existing_data mode: skipping seeding.")

                # Compare views
                run_view_testing(
                    db_conn, temp_answer_db, temp_stud_db, sub_id, config,
                    ans_snap["views"], stud_snap["views"], stud_snap["columns"],
                    report_path, diff_dir,
                    ans_view_cols_snap=ans_snap.get("view_columns", []),
                )

                manifest.update(
                    submission_id=sub_id,
                    source_path=bak_file,
                    status="OK",
                    error_code="",
                    error_message="",
                )
                ok_count += 1

            except Exception as e:
                err_msg = str(e)
                logger.error(f"View testing failed for '{sub_id}': {err_msg}")
                manifest.update(
                    submission_id=sub_id,
                    source_path=bak_file,
                    status="ERROR",
                    error_code="VIEW_TEST_ERROR",
                    error_message=err_msg,
                )

            finally:
                for db in (temp_stud_db, temp_answer_db):
                    try:
                        drop_database(db_conn, db)
                    except Exception:
                        pass
                if temp_bak_to_clean and temp_bak_to_clean.exists():
                    try:
                        os.remove(temp_bak_to_clean)
                    except Exception:
                        pass

        logger.info(f"View testing complete: {ok_count}/{total_count} submissions OK.")
        compile_summary(run_dir)

    except Exception as global_err:
        # Global command failure: write COMMAND_ERROR summary
        logger.error(f"Global view-testing command error: {global_err}")
        _write_command_error_summary(run_dir, submissions, answer_view_count, str(global_err))
        raise

    # 9. Optional protected DB post-run audit
    post_counts = _try_protected_db_audit(db_conn, protected_db, "post-run")
    if pre_counts is not None and post_counts is not None:
        for k, pre_val in pre_counts.items():
            post_val = post_counts[k]
            if pre_val != post_val:
                raise AssertionError(
                    f"SAFETY VIOLATION: Protected DB '{protected_db}' changed! "
                    f"Metric '{k}': pre={pre_val}, post={post_val}."
                )

    _audit_transactions(db_conn)
