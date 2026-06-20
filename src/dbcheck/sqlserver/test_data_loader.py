import csv
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from dbcheck.sqlserver.connection import SQLServerConnection
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.utils.logging import get_logger

def get_seeding_order(tables: List[str], fks: List[Dict[str, Any]]) -> List[str]:
    """Calculate the deletion order using topological sort (child tables first, parent tables last).
    
    Insert order will be the reverse of this order.
    """
    adj = {t: set() for t in tables}
    in_degree = {t: 0 for t in tables}
    
    for fk in fks:
        parent = fk["parent_table_canonical"]
        ref = fk["referenced_table_canonical"]
        if parent == ref or parent not in adj or ref not in adj:
            continue
        # parent -> ref: parent references ref, so parent must be deleted BEFORE ref
        if ref not in adj[parent]:
            adj[parent].add(ref)
            in_degree[ref] += 1
            
    # Kahn's algorithm
    queue = [t for t in tables if in_degree[t] == 0]
    delete_order = []
    
    while queue:
        u = queue.pop(0)
        delete_order.append(u)
        for v in adj[u]:
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)
                
    # Handle cyclic fallbacks if any
    if len(delete_order) < len(tables):
        remaining = set(tables) - set(delete_order)
        delete_order.extend(list(remaining))
        
    return delete_order

def load_csv_data(csv_path: Path) -> List[Dict[str, Any]]:
    """Load rows from a CSV file into list of dicts."""
    rows = []
    if not csv_path.exists():
        return rows
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows

def parse_val(val: Any) -> Any:
    """Parse cell value: map empty/NULL strings to None (SQL NULL)."""
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.upper() == "NULL":
        return None
    return s

def get_column_default(col_type: str) -> Any:
    """Return a standard dummy value for non-nullable SQL types."""
    col_type_l = col_type.lower()
    if col_type_l == "uniqueidentifier":
        return "00000000-0000-0000-0000-000000000000"
    if col_type_l == "bit":
        return 0
    if col_type_l in ("int", "bigint", "smallint", "tinyint", "decimal", "numeric", "float", "real", "money", "smallmoney"):
        return 0
    if col_type_l in ("date", "datetime", "datetime2", "smalldatetime", "time", "datetimeoffset"):
        return "1900-01-01"
    return ""

def seed_database(
    db_conn: SQLServerConnection,
    db_name: str,
    test_data_dir: Path,
    tables_snap: List[Dict[str, Any]],
    columns_snap: List[Dict[str, Any]],
    fks_snap: List[Dict[str, Any]],
    normalizer: NameNormalizer,
    synthetic_defaults_report_path: Optional[Path] = None
) -> None:
    """Seed test data into a database using mapped physical tables/columns."""
    logger = get_logger()
    logger.info(f"Seeding database '{db_name}'...")
    
    synthetic_defaults = []
    
    # 1. Find all CSV files in test data folder
    csv_files = list(test_data_dir.glob("*.csv"))
    if not csv_files:
        logger.warning(f"No CSV test data files found in: {test_data_dir}")
        return
        
    # Map CSV name (canonical table) to path using normalizer
    csv_map = {}
    for f in csv_files:
        stem = f.stem
        try:
            t_canon = normalizer.get_canonical_table(stem)
            csv_map[t_canon.lower()] = f
        except ValueError:
            csv_map[stem.lower()] = f
    
    # Check which tables are accepted
    accepted_statuses = {"TABLE_MATCHED_EXACT", "TABLE_MATCHED_ALIAS", "TABLE_MATCHED_ABBREVIATION", "TABLE_MATCHED_FUZZY_HIGH"}
    accepted_phys_tables = set()
    for t in tables_snap:
        # If answer DB role is specified or it is temporary answer DB, all are accepted
        if t.get("database_role") == "answer" or db_name.startswith("grade_tmp_answer_"):
            accepted_phys_tables.add(t["table_name"])
        else:
            map_res = normalizer.map_table(t["table_name"])
            if map_res["match_status"] in accepted_statuses:
                accepted_phys_tables.add(t["table_name"])
                
    # 2. Extract canonical tables list (only for accepted physical tables)
    canon_tables = []
    for t in tables_snap:
        if t["table_name"] in accepted_phys_tables:
            canon_tables.append(t["table_name_canonical"])
    
    # Calculate deletion/insertion orders
    delete_order = get_seeding_order(canon_tables, fks_snap)
    insert_order = delete_order[::-1]
    
    # Create column schema lookups
    # Key: (table_canonical, col_canonical) -> col_metadata
    col_meta = {}
    for col in columns_snap:
        t_c = col["table_name_canonical"]
        c_c = col["column_name_canonical"]
        if t_c and c_c:
            col_meta[(t_c.lower(), c_c.lower())] = col
            
    # Resolve physical tables and columns in the target database
    # Key: canonical_table_lower -> (physical_table, physical_cols_dict)
    resolved_schema = {}
    
    # We query physical tables of target DB
    logger.info("Resolving physical table and column mappings in target database...")
    for t_canon in canon_tables:
        t_canon_l = t_canon.lower()
        # Find physical table name matching this canonical name
        phys_table = None
        for t in tables_snap:
            if t["table_name_canonical"].lower() == t_canon_l:
                phys_table = t["table_name"]
                break
                
        if not phys_table:
            logger.warning(f"Canonical table '{t_canon}' has no mapped physical table in target database '{db_name}'. Skipping.")
            continue
            
        # Get physical columns
        phys_cols = {}
        for col in columns_snap:
            if col["table_name_canonical"].lower() == t_canon_l:
                c_canon = col["column_name_canonical"]
                if c_canon:
                    phys_cols[c_canon.lower()] = col["column_name"]
                    
        resolved_schema[t_canon_l] = (phys_table, phys_cols)

    # 3. Step 1: DELETE existing rows in reverse dependency order
    logger.info("Clearing existing data (DELETE) in reverse FK dependency order...")
    # Wrap deletion and seeding in a single connection session
    conn = db_conn.get_connection(db_name, autocommit=False)
    cursor = conn.cursor()
    try:
        for t_canon in delete_order:
            t_canon_l = t_canon.lower()
            if t_canon_l not in resolved_schema:
                continue
            phys_table, _ = resolved_schema[t_canon_l]
            logger.debug(f"Deleting from physical table: {phys_table}")
            cursor.execute(f"DELETE FROM [{phys_table}]")
            
        # 4. Step 2: INSERT test data in insertion order
        logger.info("Inserting test data in topological order...")
        for t_canon in insert_order:
            t_canon_l = t_canon.lower()
            if t_canon_l not in csv_map:
                logger.debug(f"No test data CSV found for canonical table '{t_canon}', skipping insert.")
                continue
                
            if t_canon_l not in resolved_schema:
                continue
                
            phys_table, phys_cols = resolved_schema[t_canon_l]
            csv_path = csv_map[t_canon_l]
            rows = load_csv_data(csv_path)
            if not rows:
                continue
                
            logger.info(f"Seeding {len(rows)} rows into '{phys_table}'...")
            
            # Find required columns for this table that are NOT in the CSV and need default values
            required_defaults = {}
            csv_cols_l = {k.lower() for k in rows[0].keys()}
            
            for col in columns_snap:
                if col["table_name_canonical"].lower() == t_canon_l:
                    c_canon = col["column_name_canonical"]
                    if not c_canon:
                        continue
                    c_canon_l = c_canon.lower()
                    
                    if (int(col["is_nullable"]) == 0 and 
                        int(col["is_identity"]) == 0 and 
                        not col["default_definition"] and
                        c_canon_l not in csv_cols_l and
                        c_canon_l in phys_cols):
                        
                        phys_col_name = phys_cols[c_canon_l]
                        
                        # Try to resolve valid foreign key default first
                        resolved_fk_val = None
                        for fk in fks_snap:
                            if (fk["parent_table_canonical"].lower() == t_canon_l and 
                                fk["parent_column_canonical"].lower() == c_canon_l):
                                ref_table_canon_l = fk["referenced_table_canonical"].lower()
                                if ref_table_canon_l in resolved_schema:
                                    ref_phys_table, ref_phys_cols = resolved_schema[ref_table_canon_l]
                                    ref_col_canon_l = fk["referenced_column_canonical"].lower()
                                    if ref_col_canon_l in ref_phys_cols:
                                        ref_phys_col = ref_phys_cols[ref_col_canon_l]
                                        try:
                                            # Query one valid value from already seeded parent table
                                            q = f"SELECT TOP 1 [{ref_phys_col}] FROM [{ref_phys_table}]"
                                            cursor.execute(q)
                                            rows_val = cursor.fetchall()
                                            if rows_val and rows_val[0][0] is not None:
                                                resolved_fk_val = rows_val[0][0]
                                                logger.warning(f"Resolved FK default for extra column '{phys_col_name}' referencing '{ref_phys_table}.{ref_phys_col}': {resolved_fk_val}")
                                                synthetic_defaults.append({
                                                    "table_name": phys_table,
                                                    "column_name": phys_col_name,
                                                    "referenced_table": ref_phys_table,
                                                    "referenced_column": ref_phys_col,
                                                    "resolved_value": str(resolved_fk_val)
                                                })
                                        except Exception as e:
                                            logger.debug(f"Failed to query FK default from {ref_phys_table}: {e}")
                                break
                                
                        if resolved_fk_val is not None:
                            required_defaults[phys_col_name] = resolved_fk_val
                        else:
                            required_defaults[phys_col_name] = get_column_default(col["data_type"])
            
            # Identify if there is an identity column we are inserting into
            has_identity = False
            for k in rows[0].keys():
                try:
                    k_canon = normalizer.get_canonical_column(k, t_canon)
                    col_canon_l = k_canon.lower()
                except ValueError:
                    col_canon_l = k.lower()
                meta = col_meta.get((t_canon_l, col_canon_l))
                if meta and int(meta.get("is_identity") or 0) == 1:
                    has_identity = True
                    break
                    
            if has_identity:
                cursor.execute(f"SET IDENTITY_INSERT [{phys_table}] ON")
                
            # Perform inserts
            for row in rows:
                insert_cols = []
                values = []
                for k, v in row.items():
                    try:
                        k_canon = normalizer.get_canonical_column(k, t_canon)
                        k_l = k_canon.lower()
                    except ValueError:
                        k_l = k.lower()
                    if k_l in phys_cols:
                        insert_cols.append(phys_cols[k_l])
                        values.append(parse_val(v))
                        
                # Append required default values
                for req_phys_col, def_val in required_defaults.items():
                    if req_phys_col not in insert_cols:
                        insert_cols.append(req_phys_col)
                        values.append(def_val)
                        
                if not insert_cols:
                    continue
                    
                cols_str = ", ".join(f"[{c}]" for c in insert_cols)
                placeholders = ", ".join("?" for _ in insert_cols)
                insert_sql = f"INSERT INTO [{phys_table}] ({cols_str}) VALUES ({placeholders})"
                logger.info(f"Executing seeding query: {insert_sql} with values {values}")
                cursor.execute(insert_sql, values)
                
            if has_identity:
                cursor.execute(f"SET IDENTITY_INSERT [{phys_table}] OFF")
                
        conn.commit()
        logger.info(f"Successfully seeded database '{db_name}'")
        if synthetic_defaults_report_path and synthetic_defaults:
            synthetic_defaults_report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(synthetic_defaults_report_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["table_name", "column_name", "referenced_table", "referenced_column", "resolved_value"])
                writer.writeheader()
                for row in synthetic_defaults:
                    writer.writerow(row)
            logger.warning(f"Logged {len(synthetic_defaults)} synthetic FK defaults to: {synthetic_defaults_report_path}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Seeding failed for database '{db_name}': {e}")
        raise e
    finally:
        cursor.close()
        conn.close()
