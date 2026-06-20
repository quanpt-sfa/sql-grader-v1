import csv
from pathlib import Path
from typing import List, Dict, Any

# Define standard headers for each file to ensure stability and uniform layouts
HEADERS = {
    "tables": [
        "database_role",
        "submission_id",
        "schema_name",
        "table_name",
        "table_name_canonical",
        "object_id",
        "column_count",
        "row_count"
    ],
    "columns": [
        "submission_id",
        "schema_name",
        "table_name",
        "table_name_canonical",
        "column_name",
        "column_name_canonical",
        "ordinal_position",
        "data_type",
        "max_length",
        "precision",
        "scale",
        "is_nullable",
        "is_identity",
        "default_definition"
    ],
    "primary_keys": [
        "submission_id",
        "table_name_canonical",
        "constraint_name",
        "column_name_canonical",
        "key_ordinal"
    ],
    "foreign_keys": [
        "submission_id",
        "fk_name",
        "parent_table_canonical",
        "parent_column_canonical",
        "referenced_table_canonical",
        "referenced_column_canonical",
        "delete_rule",
        "update_rule"
    ],
    "views": [
        "submission_id",
        "view_name",
        "view_name_canonical",
        "definition_normalized",
        "definition_hash",
        "execution_status",
        "row_count",
        "column_count"
    ],
    "view_columns": [
        "submission_id",
        "view_name_canonical",
        "ordinal_position",
        "column_name",
        "column_name_canonical",
        "data_type"
    ]
}

def write_snapshot_csv(output_dir: Path, file_key: str, rows: List[Dict[str, Any]]) -> Path:
    """Write snapshot data key (like 'tables') to a CSV file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{file_key}.csv"
    
    headers = HEADERS.get(file_key)
    if not headers:
        raise ValueError(f"Unknown snapshot file key: {file_key}")
        
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        for row in rows:
            # Keep only the fields in headers and handle missing fields gracefully
            cleaned_row = {k: row.get(k, "") for k in headers}
            writer.writerow(cleaned_row)
            
    return file_path

def write_full_snapshot(output_dir: Path, snapshot_data: Dict[str, List[Dict[str, Any]]]) -> None:
    """Write all snapshot data pieces to their respective files."""
    for key in HEADERS.keys():
        rows = snapshot_data.get(key, [])
        write_snapshot_csv(output_dir, key, rows)
