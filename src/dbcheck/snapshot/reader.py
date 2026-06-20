import csv
from pathlib import Path
from typing import List, Dict, Any
from dbcheck.utils.logging import get_logger

INT_FIELDS = {
    "object_id", "column_count", "row_count",
    "ordinal_position", "max_length", "precision", "scale", "is_nullable", "is_identity",
    "key_ordinal"
}

def read_snapshot_csv(input_dir: Path, file_key: str) -> List[Dict[str, Any]]:
    """Read snapshot CSV file for a key (like 'tables')."""
    file_path = input_dir / f"{file_key}.csv"
    logger = get_logger()
    
    if not file_path.exists():
        logger.warning(f"Snapshot file not found: {file_path}")
        return []
        
    rows = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_dict = dict(row)
                for k, v in row_dict.items():
                    if k in INT_FIELDS and v is not None and v != "":
                        try:
                            row_dict[k] = int(v)
                        except ValueError:
                            pass
                rows.append(row_dict)
    except Exception as e:
        logger.error(f"Failed to read snapshot file '{file_path}': {e}")
        
    return rows

def read_full_snapshot(input_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Read all snapshot files from a directory."""
    keys = ["tables", "columns", "primary_keys", "foreign_keys", "views", "view_columns"]
    return {key: read_snapshot_csv(input_dir, key) for key in keys}
