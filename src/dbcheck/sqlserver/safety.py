import re
from pathlib import Path
from typing import List, Set
from dbcheck.utils.names import is_safe_db_name

SYSTEM_DBS: Set[str] = {"master", "tempdb", "model", "msdb"}

def get_protected_dbs(config_protected_db: str = None) -> Set[str]:
    """Get all protected databases including system and configured ones."""
    protected = set(SYSTEM_DBS)
    if config_protected_db:
        protected.add(config_protected_db.strip().lower())
    return protected

def is_db_protected(db_name: str, config_protected_db: str = None) -> bool:
    """Check if a database name matches any protected database."""
    if not db_name:
        return True
    name_lower = db_name.strip().lower()
    return name_lower in get_protected_dbs(config_protected_db)

def extract_submission_id(file_path: Path) -> str:
    """Extract a submission ID from the filename.
    
    Tries to find an 8-digit number (student ID). Fallback to sanitized filename stem.
    """
    stem = file_path.stem
    match = re.search(r'\d{8}', stem)
    if match:
        return match.group(0)
    
    # Fallback: clean the stem to keep only alphanumeric/underscores
    cleaned = re.sub(r'[^a-zA-Z0-9_]', '', stem)
    return cleaned if cleaned else "unknown"

def check_quarantine(file_path: Path, config_protected_db: str = None) -> tuple[bool, str]:
    """Check if a file should be quarantined due to safety risks.
    
    Returns (should_quarantine, reason).
    """
    stem = file_path.stem.lower()
    
    # 1. Check if the backup filename contains the protected DB name (e.g. 00000001)
    if config_protected_db:
        prot_db_lower = config_protected_db.strip().lower()
        if prot_db_lower in stem:
            return True, f"Filename contains protected database name '{config_protected_db}'"
            
    # 2. Check if the extracted submission ID resolves to a protected DB name
    sub_id = extract_submission_id(file_path)
    if is_db_protected(sub_id, config_protected_db):
        return True, f"Extracted submission ID '{sub_id}' matches a protected database name"
        
    # 3. Check for obvious SQL injection characters in filename
    if any(char in stem for char in (";", "--", "/*", "*/", "'", '"')):
        return True, "Filename contains potential SQL injection characters"
        
    return False, ""
