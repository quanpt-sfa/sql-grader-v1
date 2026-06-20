import re
from typing import Optional, Dict, Any

# ---------------------------------------------------------------------------
# Type group definitions
# ---------------------------------------------------------------------------
TYPE_GROUPS: Dict[str, list] = {
    "integer":       ["tinyint", "smallint", "int", "bigint"],
    "fixed_decimal": ["decimal", "numeric", "money", "smallmoney"],
    "floating":      ["real", "float"],
    "text":          ["char", "varchar", "nchar", "nvarchar", "text", "ntext"],
    "date_time":     ["date", "datetime", "datetime2", "smalldatetime", "time", "datetimeoffset"],
    "boolean":       ["bit"],
    "binary":        ["binary", "varbinary", "image"],
    "guid":          ["uniqueidentifier"],
    "xml_json":      ["xml", "json"],
}

# Reverse lookup: type -> group name
_TYPE_TO_GROUP: Dict[str, str] = {}
for _group, _types in TYPE_GROUPS.items():
    for _t in _types:
        _TYPE_TO_GROUP[_t] = _group


def normalize_sql_type(sql_type: str) -> str:
    """
    Normalize a SQL Server type string to a bare lowercase type name.

    Examples:
        varchar(50)       -> varchar
        decimal(18,2)     -> decimal
        NUMERIC(10,0)     -> numeric
        [nvarchar](255)   -> nvarchar
        nvarchar(255)     -> nvarchar
    """
    if not sql_type:
        return ""
    s = sql_type.strip().lower()
    # Strip length/precision/scale first: varchar(50) -> varchar, decimal(18,2) -> decimal
    # Also handles [nvarchar](255) -> [nvarchar]
    s = re.sub(r"\s*\(.*\)\s*$", "", s)
    # Now remove surrounding brackets: [nvarchar] -> nvarchar
    s = re.sub(r"^\[(.+)\]$", r"\1", s)
    # Strip any remaining whitespace
    s = s.strip()
    return s


def get_type_group(sql_type: str) -> str:
    """
    Return the type group for a normalized (or raw) SQL type.
    Normalizes the type first, then performs the lookup.

    Returns 'unknown' if no group is found.
    """
    norm = normalize_sql_type(sql_type)
    return _TYPE_TO_GROUP.get(norm, "unknown")


def is_numeric_integer_like(sql_type: str, scale: Optional[int] = None) -> bool:
    """Return True if sql_type is a decimal/numeric with scale = 0."""
    norm = normalize_sql_type(sql_type)
    if norm not in ["decimal", "numeric"]:
        return False
    if scale is not None:
        return scale == 0
    # Try to parse scale from type string, e.g., decimal(18,0)
    match = re.search(r"\(\s*\d+\s*,\s*(\d+)\s*\)", sql_type)
    if match:
        try:
            return int(match.group(1)) == 0
        except ValueError:
            pass
    return False


def is_identifier_column(
    column_name: str,
    participates_in_pk_fk: bool = False,
    global_identifiers: Optional[list] = None,
    table_identifiers: Optional[list] = None
) -> bool:
    """Identify if a column serves as a key or identifier."""
    if not column_name:
        return False
    if participates_in_pk_fk:
        return True
    name_l = column_name.lower()
    if any(k in name_l for k in ["ma", "id", "code", "key"]):
        return True
    if table_identifiers:
        if any(x.lower() == name_l for x in table_identifiers):
            return True
    if global_identifiers:
        if any(x.lower() == name_l for x in global_identifiers):
            return True
    return False


def compare_sql_types(
    answer_type: str,
    student_type: str,
    config: Any = None,
    column_name: str = "",
    participates_in_pk_fk: bool = False,
    answer_scale: Optional[int] = None,
    student_scale: Optional[int] = None,
    table_name: str = ""
) -> Dict[str, Any]:
    """
    Compare two SQL Server column types and return a structured result dict.

    Returns:
        {
            "answer_type_raw":          str,
            "student_type_raw":         str,
            "answer_type_normalized":   str,
            "student_type_normalized":  str,
            "answer_type_group":        str,
            "student_type_group":       str,
            "type_status":              str,   # TYPE_MATCH_EXACT | TYPE_MATCH_GROUP |
                                               # TYPE_COMPATIBLE_WARNING | TYPE_IDENTIFIER_COMPATIBLE_WARNING | TYPE_MISMATCH
            "type_score":               float,
            "reason":                   str,
        }
    """
    # Read type_compatibility section from config if available
    tc: Dict[str, Any] = {}
    global_identifiers = []
    table_identifiers = []
    if config is not None and hasattr(config, "type_compatibility"):
        tc_obj = config.type_compatibility
        tc = tc_obj.__dict__ if hasattr(tc_obj, "__dict__") else {}
        global_identifiers = getattr(tc_obj, "identifier_columns_global", [])
        by_table = getattr(tc_obj, "identifier_columns_by_table", {})
        if table_name and by_table:
            table_identifiers = by_table.get(table_name.lower(), [])

    mode: str = tc.get("mode", "group_with_warnings")
    warning_score: float = float(tc.get("compatible_warning_score", 0.75))
    allow_id_int_text: bool = bool(tc.get("allow_identifier_integer_text_compatibility", True))
    allow_int_decimal: bool = bool(tc.get("allow_integer_decimal_compatibility", True))
    allow_dec_float: bool = bool(tc.get("allow_decimal_float_compatibility", True))
    allow_bit_int: bool = bool(tc.get("allow_bit_integer_compatibility", False))

    ans_norm = normalize_sql_type(answer_type)
    stu_norm = normalize_sql_type(student_type)
    ans_group = get_type_group(ans_norm)
    stu_group = get_type_group(stu_norm)

    # ---- Mode: exact ----
    if mode == "exact":
        if ans_norm == stu_norm:
            return _result(
                answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
                "TYPE_MATCH_EXACT", 1.0, "Exact type match"
            )
        return _result(
            answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
            "TYPE_MISMATCH", 0.0, f"Exact mode: {ans_norm!r} != {stu_norm!r}"
        )

    # ---- Modes: group and group_with_warnings ----

    # 1. Exact normalized type match
    if ans_norm == stu_norm:
        return _result(
            answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
            "TYPE_MATCH_EXACT", 1.0, "Exact type match"
        )

    # 2. Same group
    if ans_group == stu_group and ans_group != "unknown":
        status = "TYPE_MATCH_GROUP"
        reason = f"Same type group '{ans_group}'"
        return _result(
            answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
            status, 1.0, reason
        )

    # 3. Identifier compatibility (Warning instead of hard mismatch)
    if allow_id_int_text and is_identifier_column(column_name, participates_in_pk_fk, global_identifiers, table_identifiers):
        def get_identifier_set(sql_type: str, group: str, scale: Optional[int]) -> Optional[str]:
            norm = normalize_sql_type(sql_type)
            if group == "integer":
                return "integer"
            if group == "text" and norm in ["char", "varchar", "nchar", "nvarchar"]:
                return "text"
            if is_numeric_integer_like(sql_type, scale):
                return "numeric_int"
            return None

        ans_set = get_identifier_set(answer_type, ans_group, answer_scale)
        stu_set = get_identifier_set(student_type, stu_group, student_scale)

        if ans_set is not None and stu_set is not None:
            if ans_set != stu_set:
                return _result(
                    answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
                    "TYPE_IDENTIFIER_COMPATIBLE_WARNING", warning_score,
                    f"Identifier compatible warning: {ans_set} ↔ {stu_set} on '{column_name}'"
                )

    # 4. Cross-group compatibility warnings (for non-identifier columns or fallback)
    pair = frozenset([ans_group, stu_group])

    # decimal ↔ floating
    if pair == frozenset(["fixed_decimal", "floating"]):
        if allow_dec_float:
            return _result(
                answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
                "TYPE_COMPATIBLE_WARNING", warning_score,
                "Numeric compatible (fixed_decimal ↔ floating): potential precision risk"
            )
        return _result(
            answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
            "TYPE_MISMATCH", 0.0,
            "fixed_decimal ↔ floating not allowed by config"
        )

    # integer ↔ fixed_decimal
    if pair == frozenset(["integer", "fixed_decimal"]):
        if allow_int_decimal:
            return _result(
                answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
                "TYPE_COMPATIBLE_WARNING", warning_score,
                "Numeric widening (integer ↔ fixed_decimal): potential precision change"
            )
        return _result(
            answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
            "TYPE_MISMATCH", 0.0,
            "integer ↔ fixed_decimal not allowed by config"
        )

    # boolean ↔ integer
    if pair == frozenset(["boolean", "integer"]):
        if allow_bit_int:
            return _result(
                answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
                "TYPE_COMPATIBLE_WARNING", warning_score,
                "boolean (bit) ↔ integer: allowed by config"
            )
        return _result(
            answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
            "TYPE_MISMATCH", 0.0,
            "boolean ↔ integer not allowed by default config"
        )

    # 5. Hard mismatch for all other cross-group pairs
    return _result(
        answer_type, student_type, ans_norm, stu_norm, ans_group, stu_group,
        "TYPE_MISMATCH", 0.0,
        f"Incompatible type groups: '{ans_group}' vs '{stu_group}'"
    )


def _result(
    answer_type_raw: str,
    student_type_raw: str,
    ans_norm: str,
    stu_norm: str,
    ans_group: str,
    stu_group: str,
    type_status: str,
    type_score: float,
    reason: str
) -> Dict[str, Any]:
    return {
        "answer_type_raw":        answer_type_raw,
        "student_type_raw":       student_type_raw,
        "answer_type_normalized": ans_norm,
        "student_type_normalized": stu_norm,
        "answer_type_group":      ans_group,
        "student_type_group":     stu_group,
        "type_status":            type_status,
        "type_score":             type_score,
        "reason":                 reason,
    }
