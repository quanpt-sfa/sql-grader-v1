from typing import List, Dict, Any
from dbcheck.sqlserver.connection import SQLServerConnection
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.utils.hashing import compute_hash
from dbcheck.utils.logging import get_logger

def get_tables(db_conn: SQLServerConnection, db_name: str, submission_id: str, database_role: str, normalizer: NameNormalizer) -> List[Dict[str, Any]]:
    logger = get_logger()
    sql = """
    SELECT 
        SCHEMA_NAME(t.schema_id) as schema_name,
        t.name as table_name,
        t.object_id,
        (SELECT COUNT(*) FROM sys.columns c WHERE c.object_id = t.object_id) as column_count,
        SUM(p.rows) as row_count
    FROM sys.tables t
    JOIN sys.partitions p ON t.object_id = p.object_id
    WHERE t.is_ms_shipped = 0 AND p.index_id IN (0,1)
    GROUP BY t.schema_id, t.name, t.object_id;
    """
    rows = db_conn.execute_query(sql, db_name=db_name)
    tables = []
    for r in rows:
        table_name = r["table_name"]
        try:
            table_canon = normalizer.get_canonical_table(table_name)
        except ValueError as e:
            logger.warning(f"[{submission_id}] Table mapping ambiguity: {e}")
            table_canon = "AMBIGUOUS_TABLE"

        tables.append({
            "database_role": database_role,
            "submission_id": submission_id,
            "schema_name": r["schema_name"],
            "table_name": table_name,
            "table_name_canonical": table_canon,
            "object_id": r["object_id"],
            "column_count": r["column_count"],
            "row_count": r["row_count"]
        })
    return tables

def get_columns(db_conn: SQLServerConnection, db_name: str, submission_id: str, normalizer: NameNormalizer) -> List[Dict[str, Any]]:
    logger = get_logger()
    sql = """
    SELECT 
        SCHEMA_NAME(t.schema_id) as schema_name,
        t.name as table_name,
        c.name as column_name,
        c.column_id as ordinal_position,
        TYPE_NAME(c.user_type_id) as data_type,
        c.max_length,
        c.precision,
        c.scale,
        c.is_nullable,
        c.is_identity,
        OBJECT_DEFINITION(c.default_object_id) as default_definition
    FROM sys.columns c
    JOIN sys.tables t ON c.object_id = t.object_id
    WHERE t.is_ms_shipped = 0;
    """
    rows = db_conn.execute_query(sql, db_name=db_name)
    columns = []
    for r in rows:
        table_name = r["table_name"]
        column_name = r["column_name"]
        
        try:
            table_canon = normalizer.get_canonical_table(table_name)
        except ValueError:
            table_canon = "AMBIGUOUS_TABLE"
            
        try:
            column_canon = normalizer.get_canonical_column(column_name, table_canon)
        except ValueError as e:
            logger.warning(f"[{submission_id}] Column mapping ambiguity on {table_name}.{column_name}: {e}")
            column_canon = "AMBIGUOUS_COLUMN"

        columns.append({
            "submission_id": submission_id,
            "schema_name": r["schema_name"],
            "table_name": table_name,
            "table_name_canonical": table_canon,
            "column_name": column_name,
            "column_name_canonical": column_canon,
            "ordinal_position": r["ordinal_position"],
            "data_type": r["data_type"],
            "max_length": r["max_length"],
            "precision": r["precision"],
            "scale": r["scale"],
            "is_nullable": int(r["is_nullable"]),
            "is_identity": int(r["is_identity"]),
            "default_definition": r["default_definition"] or ""
        })
    return columns

def get_primary_keys(db_conn: SQLServerConnection, db_name: str, submission_id: str, normalizer: NameNormalizer) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        t.name as table_name,
        k.name as constraint_name,
        c.name as column_name,
        ic.key_ordinal
    FROM sys.key_constraints k
    JOIN sys.tables t ON k.parent_object_id = t.object_id
    JOIN sys.indexes i ON k.parent_object_id = i.object_id AND k.unique_index_id = i.index_id
    JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
    JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
    WHERE k.type = 'PK' AND t.is_ms_shipped = 0;
    """
    rows = db_conn.execute_query(sql, db_name=db_name)
    pks = []
    for r in rows:
        table_name = r["table_name"]
        column_name = r["column_name"]
        
        try:
            table_canon = normalizer.get_canonical_table(table_name)
        except ValueError:
            table_canon = "AMBIGUOUS_TABLE"
            
        try:
            column_canon = normalizer.get_canonical_column(column_name, table_canon)
        except ValueError:
            column_canon = "AMBIGUOUS_COLUMN"

        pks.append({
            "submission_id": submission_id,
            "table_name_canonical": table_canon,
            "constraint_name": r["constraint_name"],
            "column_name_canonical": column_canon,
            "key_ordinal": r["key_ordinal"]
        })
    return pks

def get_foreign_keys(db_conn: SQLServerConnection, db_name: str, submission_id: str, normalizer: NameNormalizer) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        fk.name as fk_name,
        tp.name as parent_table,
        cp.name as parent_column,
        tr.name as referenced_table,
        cr.name as referenced_column,
        fk.delete_referential_action_desc as delete_rule,
        fk.update_referential_action_desc as update_rule,
        fkc.constraint_column_id as constraint_column_id
    FROM sys.foreign_keys fk
    JOIN sys.tables tp ON fk.parent_object_id = tp.object_id
    JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
    JOIN sys.columns cp ON fkc.parent_object_id = cp.object_id AND fkc.parent_column_id = cp.column_id
    JOIN sys.tables tr ON fk.referenced_object_id = tr.object_id
    JOIN sys.columns cr ON fkc.referenced_object_id = cr.object_id AND fkc.referenced_column_id = cr.column_id
    WHERE tp.is_ms_shipped = 0;
    """
    rows = db_conn.execute_query(sql, db_name=db_name)
    fks = []
    for r in rows:
        p_table = r["parent_table"]
        p_column = r["parent_column"]
        r_table = r["referenced_table"]
        r_column = r["referenced_column"]
        
        try: p_table_canon = normalizer.get_canonical_table(p_table)
        except ValueError: p_table_canon = "AMBIGUOUS_TABLE"
        
        try: p_column_canon = normalizer.get_canonical_column(p_column, p_table_canon)
        except ValueError: p_column_canon = "AMBIGUOUS_COLUMN"
        
        try: r_table_canon = normalizer.get_canonical_table(r_table)
        except ValueError: r_table_canon = "AMBIGUOUS_TABLE"
        
        try: r_column_canon = normalizer.get_canonical_column(r_column, r_table_canon)
        except ValueError: r_column_canon = "AMBIGUOUS_COLUMN"

        fks.append({
            "submission_id": submission_id,
            "fk_name": r["fk_name"],
            "parent_table": p_table,
            "parent_column": p_column,
            "referenced_table": r_table,
            "referenced_column": r_column,
            "parent_table_canonical": p_table_canon,
            "parent_column_canonical": p_column_canon,
            "referenced_table_canonical": r_table_canon,
            "referenced_column_canonical": r_column_canon,
            "delete_rule": r["delete_rule"],
            "update_rule": r["update_rule"],
            "constraint_column_id": int(r["constraint_column_id"]) if r.get("constraint_column_id") is not None else None
        })
    return fks

def get_views(db_conn: SQLServerConnection, db_name: str, submission_id: str, normalizer: NameNormalizer) -> List[Dict[str, Any]]:
    logger = get_logger()
    sql = """
    SELECT 
        v.name as view_name,
        OBJECT_DEFINITION(v.object_id) as definition,
        (SELECT COUNT(*) FROM sys.columns c WHERE c.object_id = v.object_id) as col_count
    FROM sys.views v
    WHERE v.is_ms_shipped = 0;
    """
    rows = db_conn.execute_query(sql, db_name=db_name)
    views = []
    for r in rows:
        view_name = r["view_name"]
        definition = r["definition"] or ""
        col_count = r["col_count"]
        
        # Canonicalize view name (views are mapped just like tables, or using their own names if not specified)
        try:
            view_canon = normalizer.get_canonical_table(view_name)
        except ValueError:
            view_canon = "AMBIGUOUS_VIEW"
            
        # Try to dry-run the view to check execution status and row count
        execution_status = "SUCCESS"
        row_count = 0
        try:
            # Query count
            count_rows = db_conn.execute_query(f"SELECT COUNT(*) as cnt FROM [{view_name}]", db_name=db_name)
            if count_rows:
                row_count = count_rows[0]["cnt"]
        except Exception as e:
            execution_status = "ERROR"
            row_count = -1
            logger.warning(f"[{submission_id}] View '{view_name}' execution test failed: {e}")
            
        # Compute hash and normalized definition
        normalized_def = " ".join(definition.strip().split())
        def_hash = compute_hash(definition)

        views.append({
            "submission_id": submission_id,
            "view_name": view_name,
            "view_name_canonical": view_canon,
            "definition_normalized": normalized_def,
            "definition_hash": def_hash,
            "execution_status": execution_status,
            "row_count": row_count,
            "column_count": col_count
        })
    return views

def get_view_columns(db_conn: SQLServerConnection, db_name: str, submission_id: str, normalizer: NameNormalizer) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        v.name as view_name,
        c.column_id as ordinal_position,
        c.name as column_name,
        TYPE_NAME(c.user_type_id) as data_type
    FROM sys.columns c
    JOIN sys.views v ON c.object_id = v.object_id
    WHERE v.is_ms_shipped = 0;
    """
    rows = db_conn.execute_query(sql, db_name=db_name)
    view_cols = []
    for r in rows:
        view_name = r["view_name"]
        column_name = r["column_name"]
        
        try:
            view_canon = normalizer.get_canonical_table(view_name)
        except ValueError:
            view_canon = "AMBIGUOUS_VIEW"
            
        try:
            column_canon = normalizer.get_canonical_column(column_name, view_canon)
        except ValueError:
            column_canon = "AMBIGUOUS_COLUMN"

        view_cols.append({
            "submission_id": submission_id,
            "view_name_canonical": view_canon,
            "ordinal_position": r["ordinal_position"],
            "column_name": column_name,
            "column_name_canonical": column_canon,
            "data_type": r["data_type"]
        })
    return view_cols


def get_unique_constraints(db_conn: SQLServerConnection, db_name: str, submission_id: str, normalizer: NameNormalizer) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        t.name as table_name,
        i.name as index_name,
        c.name as column_name,
        ic.key_ordinal
    FROM sys.indexes i
    JOIN sys.tables t ON i.object_id = t.object_id
    JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
    JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
    WHERE i.is_unique = 1 AND t.is_ms_shipped = 0;
    """
    rows = db_conn.execute_query(sql, db_name=db_name)
    uniques = []
    for r in rows:
        table_name = r["table_name"]
        column_name = r["column_name"]
        
        try:
            table_canon = normalizer.get_canonical_table(table_name)
        except ValueError:
            table_canon = "AMBIGUOUS_TABLE"
            
        try:
            column_canon = normalizer.get_canonical_column(column_name, table_canon)
        except ValueError:
            column_canon = "AMBIGUOUS_COLUMN"
            
        uniques.append({
            "submission_id": submission_id,
            "table_name_canonical": table_canon,
            "constraint_name": r["index_name"],
            "column_name_canonical": column_canon,
            "key_ordinal": r["key_ordinal"]
        })
    return uniques

