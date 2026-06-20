from pathlib import Path
from typing import List, Dict, Any
from dbcheck.sqlserver.connection import SQLServerConnection
from dbcheck.utils.logging import get_logger

def get_sql_data_dir(db_conn: SQLServerConnection) -> Path:
    """Find the directory of the master database files to use as default data directory."""
    sql = "SELECT physical_name FROM sys.master_files WHERE database_id = 1 AND file_id = 1"
    rows = db_conn.execute_query(sql)
    if rows:
        master_file = Path(rows[0]['physical_name'])
        return master_file.parent
    # Fallback default
    return Path(r"C:\Program Files\Microsoft SQL Server\MSSQL16.MC22\MSSQL\DATA")

def get_logical_files(db_conn: SQLServerConnection, backup_path: Path) -> List[Dict[str, Any]]:
    """Execute RESTORE FILELISTONLY and parse logical file details by Type."""
    backup_path = backup_path.resolve()
    sql = f"RESTORE FILELISTONLY FROM DISK = ?"
    # RESTORE FILELISTONLY returns metadata rows
    rows = db_conn.execute_query(sql, [str(backup_path)])
    
    # Standardize dictionary keys to lowercase
    logical_files = []
    for r in rows:
        normalized_row = {k.lower(): v for k, v in r.items()}
        logical_files.append({
            "logical_name": normalized_row.get("logicalname"),
            "physical_name": normalized_row.get("physicalname"),
            "type": normalized_row.get("type") # 'D' for data, 'L' for log, etc.
        })
    return logical_files

def restore_database(db_conn: SQLServerConnection, backup_path: Path, target_db_name: str) -> None:
    """Restore a database backup file under a temporary name with moved physical files."""
    logger = get_logger()
    backup_path = backup_path.resolve()
    logger.info(f"Restoring backup '{backup_path.name}' as '{target_db_name}'...")
    
    # 1. Get logical files
    logical_files = get_logical_files(db_conn, backup_path)
    if not logical_files:
        raise ValueError(f"No logical files found in backup: {backup_path}")
        
    # 2. Get target data directory
    data_dir = get_sql_data_dir(db_conn)
    
    # 3. Build MOVE clauses
    move_clauses = []
    for lf in logical_files:
        logical_name = lf["logical_name"]
        file_type = lf["type"].upper()
        
        # Determine appropriate extension
        if file_type == "D":
            ext = ".mdf" if len(move_clauses) == 0 else ".ndf"
        elif file_type == "L":
            ext = ".ldf"
        else:
            ext = f"_{file_type.lower()}.dat"
            
        new_physical_name = data_dir / f"{target_db_name}_{logical_name}{ext}"
        move_clauses.append(f"MOVE '{logical_name}' TO '{new_physical_name}'")
        
    move_str = ", ".join(move_clauses)
    
    # 4. Run restore query (must run in autocommit mode)
    restore_sql = f"""
    RESTORE DATABASE [{target_db_name}]
    FROM DISK = ?
    WITH {move_str}, REPLACE, RECOVERY;
    """
    
    db_conn.execute_non_query(restore_sql, [str(backup_path)], autocommit=True)
    logger.info(f"Successfully restored database '{target_db_name}'")

def drop_database(db_conn: SQLServerConnection, target_db_name: str) -> None:
    """Safely drop a database after severing all active connections."""
    logger = get_logger()
    logger.info(f"Dropping database '{target_db_name}'...")
    try:
        # Check if database exists first
        check_sql = "SELECT database_id FROM sys.databases WHERE name = ?"
        exists = db_conn.execute_query(check_sql, [target_db_name])
        if not exists:
            logger.info(f"Database '{target_db_name}' does not exist, skipping drop.")
            return
            
        # Sever connections and drop
        drop_sql = f"""
        ALTER DATABASE [{target_db_name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
        DROP DATABASE [{target_db_name}];
        """
        db_conn.execute_non_query(drop_sql, autocommit=True)
        logger.info(f"Successfully dropped database '{target_db_name}'")
    except Exception as e:
        logger.warning(f"Error while dropping database '{target_db_name}': {e}. Physical files might need manual cleanup.")
        # Try a direct drop as fallback
        try:
            db_conn.execute_non_query(f"DROP DATABASE [{target_db_name}]", autocommit=True)
        except Exception:
            pass
