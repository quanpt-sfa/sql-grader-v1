import os
import sys
from pathlib import Path
from datetime import datetime
from dbcheck.config import load_config
from dbcheck.sqlserver.connection import SQLServerConnection
from dbcheck.sqlserver.restore import restore_database, drop_database
from dbcheck.sqlserver.safety import check_quarantine, extract_submission_id
from dbcheck.sqlserver.introspection import (
    get_tables, get_columns, get_primary_keys, get_foreign_keys, get_views, get_view_columns,
    get_unique_constraints, get_view_definitions
)
from dbcheck.snapshot.writer import write_full_snapshot
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.utils.manifest import ManifestManager
from dbcheck.utils.logging import get_logger

def _extract_full_snapshot(db_conn, db_name, submission_id, role, normalizer):
    logger = get_logger()
    snapshot = {}
    extraction_errors = []
    extractors = [
        ("tables", lambda: get_tables(db_conn, db_name, submission_id, role, normalizer)),
        ("columns", lambda: get_columns(db_conn, db_name, submission_id, normalizer)),
        ("primary_keys", lambda: get_primary_keys(db_conn, db_name, submission_id, normalizer)),
        ("foreign_keys", lambda: get_foreign_keys(db_conn, db_name, submission_id, normalizer)),
        ("views", lambda: get_views(db_conn, db_name, submission_id, normalizer)),
        ("view_columns", lambda: get_view_columns(db_conn, db_name, submission_id, normalizer)),
        ("unique_constraints", lambda: get_unique_constraints(db_conn, db_name, submission_id, normalizer)),
        ("view_definitions", lambda: get_view_definitions(db_conn, db_name, submission_id, role, normalizer)),
    ]
    for key, extractor in extractors:
        try:
            snapshot[key] = extractor()
        except Exception as e:
            message = f"{key}: {e}"
            logger.error(f"[{submission_id}] Snapshot extraction failed for {message}")
            extraction_errors.append(message)
            snapshot[key] = []
    return snapshot, extraction_errors

def run_snapshot(args):
    logger = get_logger()
    
    # 1. Load config
    config = load_config(args.config)
    logger.info(f"Loaded config: {config.name}")
    
    # 2. Setup run directory and paths
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = ManifestManager(run_dir)
    
    # Generate run ID
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 3. Connection to SQL Server
    db_conn = SQLServerConnection()
    
    # 4. Extract Answer Snapshot
    answer_snap_dir = run_dir / "answer_snapshot"
    temp_answer_db = None
    
    try:
        # Determine source of answer database
        if args.answer_bak:
            ans_bak_path = Path(args.answer_bak)
            if not ans_bak_path.exists():
                raise FileNotFoundError(f"Answer backup file not found: {ans_bak_path}")
            
            # Temporary answer DB name
            temp_answer_db = f"grade_tmp_answer_{run_id}"
            logger.info(f"Restoring answer backup as temporary DB '{temp_answer_db}'...")
            restore_database(db_conn, ans_bak_path, temp_answer_db)
            answer_db_name = temp_answer_db
        else:
            answer_db_name = args.answer_db
            logger.info(f"Using existing answer DB '{answer_db_name}'...")
            
            # Verify DB existence
            exists_sql = "SELECT database_id FROM sys.databases WHERE name = ?"
            if not db_conn.execute_query(exists_sql, [answer_db_name]):
                raise ValueError(f"Answer database '{answer_db_name}' does not exist on the server")
                
        # Instantiating the normalizer (uses configuration alias mappings)
        normalizer = NameNormalizer(config)
        
        # Extract schemas of answer database
        logger.info(f"Extracting schema and view definitions for answer")
        answer_snap, answer_errors = _extract_full_snapshot(
            db_conn, answer_db_name, "answer", "answer", normalizer
        )
        if answer_errors:
            logger.error(f"Answer snapshot had extraction errors: {' | '.join(answer_errors)}")
        
        logger.info("Writing snapshot artifacts for answer")
        write_full_snapshot(answer_snap_dir, answer_snap)
        logger.info(f"Answer snapshot written to: {answer_snap_dir}")
        
    finally:
        # Clean up temporary answer database
        if temp_answer_db:
            logger.info(f"Dropping temporary answer database {temp_answer_db}")
            try:
                drop_database(db_conn, temp_answer_db)
            except Exception as e:
                logger.error(f"Failed to drop temporary answer database '{temp_answer_db}': {e}")
                
    # 5. Process submissions
    submissions_dir = Path(args.submissions)
    if not submissions_dir.exists():
        raise FileNotFoundError(f"Submissions folder not found: {submissions_dir}")
        
    bak_files = []
    seen = set()
    for p in submissions_dir.iterdir():
        if p.is_file() and p.suffix.lower() == ".bak":
            resolved = p.resolve()
            if resolved not in seen:
                seen.add(resolved)
                bak_files.append(p)
                
    if not bak_files:
        logger.warning(f"No backup files (.bak) found in: {submissions_dir}")
        return
        
    logger.info(f"Found {len(bak_files)} backup files to process.")
    
    for bak_file in bak_files:
        started_at = datetime.now()
        
        # Safety Check: Quarantine
        is_quarantine, reason = check_quarantine(bak_file, config.protected_answer_db)
        if is_quarantine:
            sub_id = extract_submission_id(bak_file)
            logger.warning(f"QUARANTINED backup file '{bak_file.name}': {reason}")
            manifest.update(
                submission_id=sub_id,
                source_path=bak_file,
                status="QUARANTINED",
                error_code="SAFETY_VIOLATION",
                error_message=reason,
                started_at=started_at,
                finished_at=datetime.now()
            )
            continue
            
        sub_id = extract_submission_id(bak_file)
        temp_stud_db = f"grade_tmp_{sub_id}_{run_id}"
        
        # Log start in manifest
        manifest.update(
            submission_id=sub_id,
            source_path=bak_file,
            status="RUNNING",
            temp_database=temp_stud_db,
            started_at=started_at
        )
        
        try:
            # Restore student database
            logger.info(f"Restoring student database {sub_id}")
            restore_database(db_conn, bak_file, temp_stud_db)
            
            # Extract schemas
            logger.info(f"Extracting schema and view definitions for {sub_id}")
            student_snap_dir = run_dir / "submissions" / sub_id / "snapshot"
            student_snap, extraction_errors = _extract_full_snapshot(
                db_conn, temp_stud_db, sub_id, "student", normalizer
            )
            
            logger.info(f"Writing snapshot artifacts for {sub_id}")
            write_full_snapshot(student_snap_dir, student_snap)

            if extraction_errors:
                manifest.update(
                    submission_id=sub_id,
                    source_path=bak_file,
                    status="ERROR",
                    error_code="SNAPSHOT_EXTRACTION_PARTIAL",
                    error_message=" | ".join(extraction_errors),
                    finished_at=datetime.now()
                )
                logger.error(f"Processed submission '{sub_id}' with extraction errors")
            else:
                manifest.update(
                    submission_id=sub_id,
                    source_path=bak_file,
                    status="OK",
                    finished_at=datetime.now()
                )
                logger.info(f"Successfully processed submission '{sub_id}'")
            
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Error processing submission '{sub_id}': {err_msg}")
                
            manifest.update(
                submission_id=sub_id,
                source_path=bak_file,
                status="ERROR",
                error_code="RESTORE_OR_INTROS_ERROR",
                error_message=err_msg,
                finished_at=datetime.now()
            )
        finally:
            logger.info(f"Dropping temporary student database {temp_stud_db}")
            try:
                drop_database(db_conn, temp_stud_db)
            except Exception:
                pass
            
    logger.info("Snapshot extraction completed for all submissions.")
