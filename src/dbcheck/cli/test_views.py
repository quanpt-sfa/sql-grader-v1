import os
import csv
from pathlib import Path
from datetime import datetime
from dbcheck.config import load_config
from dbcheck.sqlserver.connection import SQLServerConnection
from dbcheck.sqlserver.restore import restore_database, drop_database, get_sql_data_dir
from dbcheck.sqlserver.test_data_loader import seed_database
from dbcheck.snapshot.reader import read_full_snapshot
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.views.view_reporter import run_view_testing
from dbcheck.utils.manifest import ManifestManager
from dbcheck.utils.summary import compile_summary
from dbcheck.utils.logging import get_logger

def create_copy_only_backup(db_conn: SQLServerConnection, src_db: str, run_id: str) -> Path:
    """Create a copy-only backup of the protected database as a fallback."""
    logger = get_logger()
    data_dir = get_sql_data_dir(db_conn)
    bak_file = data_dir / f"{src_db}_copy_only_{run_id}.bak"
    logger.info(f"Creating copy-only backup of protected DB '{src_db}' to '{bak_file}'...")
    sql = f"BACKUP DATABASE [{src_db}] TO DISK = ? WITH COPY_ONLY, FORMAT, INIT"
    db_conn.execute_non_query(sql, [str(bak_file)], autocommit=True)
    return bak_file

def get_db_counts(db_conn: SQLServerConnection, db_name: str) -> dict:
    """Get the counts of tables, views, and total row count across all tables in a database."""
    tables_sql = "SELECT COUNT(*) as cnt FROM sys.tables WHERE is_ms_shipped = 0"
    tables_cnt = db_conn.execute_query(tables_sql, db_name=db_name)[0]["cnt"]
    
    views_sql = "SELECT COUNT(*) as cnt FROM sys.views WHERE is_ms_shipped = 0"
    views_cnt = db_conn.execute_query(views_sql, db_name=db_name)[0]["cnt"]
    
    rows_sql = """
    SELECT SUM(p.rows) as cnt
    FROM sys.tables t
    JOIN sys.partitions p ON t.object_id = p.object_id
    WHERE t.is_ms_shipped = 0 AND p.index_id IN (0,1)
    """
    rows_res = db_conn.execute_query(rows_sql, db_name=db_name)
    rows_cnt = rows_res[0]["cnt"] if rows_res and rows_res[0]["cnt"] is not None else 0
    
    return {
        "tables_count": tables_cnt,
        "views_count": views_cnt,
        "row_count": rows_cnt
    }

def audit_transactions(db_conn: SQLServerConnection) -> None:
    logger = get_logger()
    sql = """
    SELECT 
        session_id, 
        open_transaction_count, 
        host_name, 
        login_name, 
        program_name
    FROM sys.dm_exec_sessions
    WHERE open_transaction_count > 0
    """
    try:
        rows = db_conn.execute_query(sql)
        if not rows:
            logger.info("Transaction audit check: No sessions found with open transactions.")
            return
            
        logger.warning(f"Transaction audit check: Found {len(rows)} sessions with open transactions:")
        for r in rows:
            logger.warning(
                f"Session ID: {r['session_id']}, Open Transactions: {r['open_transaction_count']}, "
                f"Host: {r['host_name']}, Login: {r['login_name']}, Program: {r['program_name']}"
            )
            
        ctx_sql = "SELECT HOST_NAME() as host, ORIGINAL_LOGIN() as login, @@SPID as spid"
        ctx_res = db_conn.execute_query(ctx_sql)
        current_host = ctx_res[0]["host"]
        current_login = ctx_res[0]["login"]
        current_spid = ctx_res[0]["spid"]
        
        local_offenders = []
        for r in rows:
            if r["session_id"] == current_spid:
                continue
            if (r["host_name"] == current_host and 
                r["login_name"] == current_login and 
                "python" in str(r["program_name"]).lower()):
                local_offenders.append(r)
                
        if local_offenders:
            raise AssertionError(
                f"TRANSACTION CLEANUP ERROR: Active Python SQL Server session(s) left with uncommitted/open transactions! "
                f"Offending sessions: {local_offenders}"
            )
    except Exception as e:
        if isinstance(e, AssertionError):
            raise e
        logger.error(f"Failed to query transaction audit DMV: {e}")

def run_test_views(args):
    logger = get_logger()
    
    # 1. Load config
    config = load_config(args.config)
    logger.info(f"Loaded config: {config.name}")
    
    # 2. Paths
    run_dir = Path(args.run_dir)
    manifest_path = run_dir / "manifest.csv"
    test_data_dir = Path(args.test_data)
    
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.csv not found in: {run_dir}. Please run snapshot first.")
    if not test_data_dir.exists():
        raise FileNotFoundError(f"Test data directory not found: {test_data_dir}")
        
    # 3. Connection to SQL Server
    db_conn = SQLServerConnection()
    
    # Pre-run Protected DB Safety check
    protected_db = config.protected_answer_db
    logger.info(f"Auditing safety of protected answer DB '{protected_db}' (pre-run)...")
    pre_counts = get_db_counts(db_conn, protected_db)
    logger.info(f"Protected DB pre-run counts: {pre_counts}")
    
    # 4. Read manifest entries
    manifest = ManifestManager(run_dir)
    submissions = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            submissions.append(row)
            
    # Locate answer backup file (preferred mode)
    answer_bak_path = None
    if args.answer_bak:
        answer_bak_path = Path(args.answer_bak)
    else:
        # Fallback to solution/dapan.bak convention in workspace
        workspace_dapan = Path("solution/dapan.bak")
        if workspace_dapan.exists():
            answer_bak_path = workspace_dapan
            logger.info(f"Detected convention answer backup file: {answer_bak_path}")
            
    # Process submissions
    ok_count = 0
    total_count = 0
    
    # Initialize NameNormalizer
    normalizer = NameNormalizer(config)
    
    # Read answer snapshot
    answer_snapshot_dir = run_dir / "answer_snapshot"
    if not answer_snapshot_dir.exists():
        raise FileNotFoundError(f"Answer snapshot folder missing at: {answer_snapshot_dir}. Please run snapshot command first.")
    ans_snap = read_full_snapshot(answer_snapshot_dir)
    
    for sub in submissions:
        sub_id = sub["submission_id"]
        status = sub["status"]
        
        if status != "OK":
            logger.info(f"Skipping view test for '{sub_id}' because snapshot status is '{status}'")
            continue
            
        total_count += 1
        bak_file = Path(sub["source_path"])
        student_snapshot_dir = run_dir / "submissions" / sub_id / "snapshot"
        report_path = run_dir / "submissions" / sub_id / "reports" / "view_test_report.csv"
        diff_dir = run_dir / "submissions" / sub_id / "reports" / "view_output_diff"
        
        # Read student snapshot
        stud_snap = read_full_snapshot(student_snapshot_dir)
        
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_stud_db = f"grade_tmp_{sub_id}_{run_id}"
        temp_answer_db = f"grade_tmp_answer_{run_id}"
        
        temp_bak_to_clean = None
        
        logger.info(f"Testing view behavior for student '{sub_id}'...")
        
        try:
            # 1. Restore answer DB (temp)
            if answer_bak_path and answer_bak_path.exists():
                restore_database(db_conn, answer_bak_path, temp_answer_db)
            else:
                # Fallback mode: backup and restore protected answer DB
                try:
                    temp_bak_to_clean = create_copy_only_backup(db_conn, config.protected_answer_db, run_id)
                    restore_database(db_conn, temp_bak_to_clean, temp_answer_db)
                except Exception as fb_err:
                    raise RuntimeError(f"Fallback answer DB copy failed (check permissions): {fb_err}")
                    
            # 2. Restore student DB (temp)
            restore_database(db_conn, bak_file, temp_stud_db)
            
            # 3. Seed both databases
            logger.info("Seeding temporary databases...")
            seed_defaults_path = run_dir / "submissions" / sub_id / "reports" / "seeding_synthetic_defaults.csv"
            seed_database(
                db_conn, temp_answer_db, test_data_dir,
                ans_snap["tables"], ans_snap["columns"], ans_snap["foreign_keys"], normalizer
            )
            seed_database(
                db_conn, temp_stud_db, test_data_dir,
                stud_snap["tables"], stud_snap["columns"], stud_snap["foreign_keys"], normalizer,
                synthetic_defaults_report_path=seed_defaults_path
            )
            
            # 4. Compare views
            run_view_testing(
                db_conn, temp_answer_db, temp_stud_db, sub_id, config,
                ans_snap["views"], stud_snap["views"], stud_snap["columns"],
                report_path, diff_dir
            )
            
            manifest.update(
                submission_id=sub_id,
                source_path=bak_file,
                status="OK",
                error_code="",
                error_message=""
            )
            ok_count += 1
            
        except Exception as e:
            err_msg = str(e)
            logger.error(f"View behavior testing failed for student '{sub_id}': {err_msg}")
            # Log failure in manifest as well
            manifest.update(
                submission_id=sub_id,
                source_path=bak_file,
                status="ERROR",
                error_code="VIEW_TEST_ERROR",
                error_message=err_msg
            )
            
        finally:
            # Drop temporary databases
            try:
                drop_database(db_conn, temp_stud_db)
            except Exception:
                pass
            try:
                drop_database(db_conn, temp_answer_db)
            except Exception:
                pass
            # Clean copy-only backup file if created
            if temp_bak_to_clean and temp_bak_to_clean.exists():
                try:
                    os.remove(temp_bak_to_clean)
                except Exception:
                    pass
                    
    logger.info(f"View testing complete. Successfully tested {ok_count}/{total_count} submissions.")
    
    # 5. Generate summary.csv
    compile_summary(run_dir)
    
    # Post-run Protected DB Safety check
    logger.info(f"Auditing safety of protected answer DB '{protected_db}' (post-run)...")
    post_counts = get_db_counts(db_conn, protected_db)
    logger.info(f"Protected DB post-run counts: {post_counts}")
    
    for k, pre_val in pre_counts.items():
        post_val = post_counts[k]
        if pre_val != post_val:
            raise AssertionError(
                f"SAFETY VIOLATION: Protected database '{protected_db}' object count changed! "
                f"Metric '{k}': pre-run={pre_val}, post-run={post_val}. "
                f"Verify that no write operations occurred on the protected answer DB."
            )
            
    # Audit transactions
    audit_transactions(db_conn)
