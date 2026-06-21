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
        "parent_table",
        "parent_column",
        "referenced_table",
        "referenced_column",
        "parent_table_canonical",
        "parent_column_canonical",
        "referenced_table_canonical",
        "referenced_column_canonical",
        "delete_rule",
        "update_rule",
        "constraint_column_id"
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
    ],
    "unique_constraints": [
        "submission_id",
        "table_name_canonical",
        "constraint_name",
        "column_name_canonical",
        "key_ordinal"
    ],
    "view_definitions": [
        "submission_id",
        "role",
        "view_schema",
        "view_name",
        "view_name_canonical",
        "definition_found",
        "raw_definition",
        "raw_definition_path",
        "extract_status",
        "extract_error"
    ]
}

def _safe_sql_file_name(view_schema: str, view_name: str) -> str:
    full_name = f"{view_schema}.{view_name}" if view_schema else view_name
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in full_name)

def write_view_sql_files(output_dir: Path, rows: List[Dict[str, Any]]) -> None:
    sql_base_dir = output_dir if output_dir.name == "answer_snapshot" else output_dir.parent
    raw_dir = sql_base_dir / "view_sql" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        raw_definition = row.get("raw_definition") or ""
        if not raw_definition:
            row["raw_definition_path"] = row.get("raw_definition_path", "")
            continue
        safe_name = _safe_sql_file_name(str(row.get("view_schema", "")), str(row.get("view_name", "")))
        raw_path = raw_dir / f"{safe_name}.sql"
        raw_path.write_text(raw_definition, encoding="utf-8")
        row["raw_definition_path"] = str(raw_path)

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
    if "view_definitions" in snapshot_data:
        write_view_sql_files(output_dir, snapshot_data.get("view_definitions", []))
    for key in HEADERS.keys():
        rows = snapshot_data.get(key, [])
        write_snapshot_csv(output_dir, key, rows)
