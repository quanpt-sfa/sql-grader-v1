import os
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
from dbcheck.config import load_config
from dbcheck.sqlserver.connection import SQLServerConnection
from dbcheck.sqlserver.restore import restore_database, drop_database, validate_sqlserver_backup
from dbcheck.sqlserver.safety import check_quarantine, extract_submission_id
from dbcheck.sqlserver.introspection import (
    get_tables, get_columns, get_primary_keys, get_foreign_keys, get_views, get_view_columns,
    get_unique_constraints, get_view_definitions
)
from dbcheck.snapshot.writer import write_full_snapshot
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.utils.manifest import ManifestManager
from dbcheck.utils.logging import get_logger

IRRELEVANT_SUFFIXES = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz",
    ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt",
    ".pdf", ".png", ".jpg", ".jpeg", ".gif",
    ".tmp", ".temp", ".lnk",
}

BACKUP_HINT_SUFFIXES = {".bak", ".backup", ".sqlbak", ".dat", ".txt", ""}

def _skip_restore_candidate_reason(path: Path) -> str:
    name = path.name
    suffix = path.suffix.lower()
    if not path.is_file():
        return "not_regular_file"
    if name.startswith("."):
        return "hidden_metadata_file"
    if name.startswith("~$"):
        return "office_lock_file"
    if suffix in IRRELEVANT_SUFFIXES:
        return f"irrelevant_extension:{suffix or '<none>'}"
    try:
        if path.stat().st_size == 0:
            return "empty_file"
    except OSError as e:
        return f"stat_failed:{e}"
    return ""

def discover_restore_candidates(submissions_dir: Path) -> List[Path]:
    """Find regular files worth validating as possible SQL Server backups."""
    logger = get_logger()
    candidates: List[Path] = []
    seen = set()
    discovered = sorted((p for p in submissions_dir.iterdir()), key=lambda p: p.name.lower())
    logger.info(
        "Discovered submission files: "
        + ", ".join(p.name for p in discovered if p.is_file())
    )
    for path in discovered:
        reason = _skip_restore_candidate_reason(path)
        if reason:
            logger.info(f"Skipping restore candidate '{path.name}': {reason}")
            continue
        resolved = path.resolve()
        if resolved in seen:
            logger.info(f"Skipping restore candidate '{path.name}': duplicate_path")
            continue
        seen.add(resolved)
        logger.info(f"Testing restore candidate '{path.name}' as possible SQL Server backup")
        candidates.append(path)
    return candidates

def select_backup_candidate(valid_candidates: List[Dict[str, Any]], submission_id: str) -> Dict[str, Any]:
    """Select one valid backup deterministically for a submission id."""
    if len(valid_candidates) == 1:
        return valid_candidates[0]
    normalized_sub = "".join(ch.lower() for ch in submission_id if ch.isalnum())
    name_matches = [
        c for c in valid_candidates
        if normalized_sub and normalized_sub in "".join(ch.lower() for ch in c["path"].stem if ch.isalnum())
    ]
    pool = name_matches or valid_candidates
    pool = sorted(
        pool,
        key=lambda c: (c["path"].stat().st_mtime, c["path"].name.lower()),
        reverse=True,
    )
    top = pool[0]
    if len(pool) > 1:
        top_mtime = top["path"].stat().st_mtime
        tied = [c for c in pool if c["path"].stat().st_mtime == top_mtime]
        if len(tied) > 1 and not name_matches:
            names = ", ".join(str(c["path"]) for c in valid_candidates)
            raise ValueError(f"multiple_valid_backups_found: {names}")
    return top

def validate_restore_candidates(db_conn: SQLServerConnection, candidates: List[Path]) -> List[Dict[str, Any]]:
    logger = get_logger()
    valid = []
    for path in candidates:
        ok, message = validate_sqlserver_backup(db_conn, path)
        if ok:
            logger.info(f"Validation succeeded for SQL Server backup candidate '{path}'")
            valid.append({"path": path, "message": ""})
        else:
            logger.warning(f"Validation failed for restore candidate '{path}': {message}")
    return valid

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
        
    candidate_files = discover_restore_candidates(submissions_dir)
    valid_candidates = validate_restore_candidates(db_conn, candidate_files)
    valid_paths = {c["path"].resolve() for c in valid_candidates}

    for candidate in candidate_files:
        if candidate.resolve() in valid_paths:
            continue
        if candidate.suffix.lower() in BACKUP_HINT_SUFFIXES:
            sub_id = extract_submission_id(candidate)
            logger.warning(
                f"Candidate '{candidate.name}' looked like a possible backup but failed SQL Server validation"
            )
            manifest.update(
                submission_id=sub_id,
                source_path=candidate,
                status="ERROR",
                error_code="RESTORE_METADATA_FAILED",
                error_message="not_a_sql_server_backup_or_restore_metadata_failed",
                started_at=datetime.now(),
                finished_at=datetime.now()
            )

    if not valid_candidates:
        logger.warning(f"No valid SQL Server backup files found in: {submissions_dir}")
        return

    grouped_candidates: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in valid_candidates:
        sub_id = extract_submission_id(candidate["path"])
        grouped_candidates.setdefault(sub_id, []).append(candidate)

    selected_backups = []
    for sub_id, group in grouped_candidates.items():
        try:
            selected = select_backup_candidate(group, sub_id)
            logger.info(f"Chosen backup file for submission '{sub_id}': {selected['path']}")
            selected_backups.append((sub_id, selected["path"]))
        except Exception as e:
            logger.error(f"Could not choose backup for submission '{sub_id}': {e}")
            manifest.update(
                submission_id=sub_id,
                source_path=group[0]["path"],
                status="ERROR",
                error_code="MULTIPLE_VALID_BACKUPS_FOUND",
                error_message=str(e),
                started_at=datetime.now(),
                finished_at=datetime.now()
            )

    if not selected_backups:
        logger.warning(f"No unambiguous SQL Server backup files selected from: {submissions_dir}")
        return

    logger.info(f"Found {len(selected_backups)} SQL Server backup file(s) to process.")
    
    for sub_id, bak_file in selected_backups:
        started_at = datetime.now()
        
        # Safety Check: Quarantine
        is_quarantine, reason = check_quarantine(bak_file, config.protected_answer_db)
        if is_quarantine:
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
