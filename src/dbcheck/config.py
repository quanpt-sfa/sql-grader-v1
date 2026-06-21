import re
import unicodedata
import yaml
from pathlib import Path
from typing import Dict, List, Any, Optional, Set


def _remove_accents(s: str) -> str:
    """Remove Vietnamese accents for exclusion normalization (inline to avoid circular imports)."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("đ", "d").replace("Đ", "D")
    return s


class ViewConfig:
    def __init__(self, data: Dict[str, Any]):
        self.answer_view: str = data.get("answer_view", "")
        self.answer_required: bool = bool(data.get("answer_required", True))
        self.student_required: bool = bool(data.get("student_required", True))
        self.check_mode: str = data.get("check_mode", "full")
        # Per-view order sensitivity: if True, rows must match in order.
        # Default False = multiset (order-insensitive) comparison.
        self.order_sensitive: bool = bool(data.get("order_sensitive", False))
        expected = data.get("expected_output", {})
        self.columns: List[Dict[str, Any]] = expected.get("columns", [])
        self.sort_by: List[str] = expected.get("sort_by", [])
        self.numeric_tolerance: float = expected.get("numeric_tolerance", 0.01)


class TypeCompatibilityConfig:
    def __init__(self, data: Dict[str, Any]):
        self.mode: str = data.get("mode", "group_with_warnings")
        self.compatible_warning_score: float = float(data.get("compatible_warning_score", 0.75))
        self.allow_identifier_integer_text_compatibility: bool = bool(data.get("allow_identifier_integer_text_compatibility", True))
        self.allow_integer_decimal_compatibility: bool = bool(data.get("allow_integer_decimal_compatibility", True))
        self.allow_decimal_float_compatibility: bool = bool(data.get("allow_decimal_float_compatibility", True))
        self.allow_bit_integer_compatibility: bool = bool(data.get("allow_bit_integer_compatibility", False))
        self.strict_length_check: bool = bool(data.get("strict_length_check", False))
        self.strict_precision_scale_check: bool = bool(data.get("strict_precision_scale_check", False))
        raw_id_cols = data.get("identifier_columns", {})
        self.identifier_columns_global: List[str] = []
        self.identifier_columns_by_table: Dict[str, List[str]] = {}
        if isinstance(raw_id_cols, list):
            self.identifier_columns_global = [str(x) for x in raw_id_cols]
        elif isinstance(raw_id_cols, dict):
            self.identifier_columns_global = [str(x) for x in raw_id_cols.get("global", [])]
            by_table_raw = raw_id_cols.get("by_table", {}) or {}
            self.identifier_columns_by_table = {
                str(k).lower().strip(): [str(x) for x in v]
                for k, v in by_table_raw.items() if v
            }



# Tables that are always excluded regardless of config
_DEFAULT_EXCLUDED_TABLES: Set[str] = {"sysdiagrams"}


def _normalize_table_name_for_exclusion(name: str) -> str:
    """Normalize a table name for exclusion lookup: accent-removal + lowercase."""
    return _remove_accents(name).lower().strip()


class KeyGradingConfig:
    def __init__(self, data: Dict[str, Any]):
        if not data:
            data = {}
        self.mode: str = data.get("mode", "exact")
        self.allow_surrogate_keys: bool = bool(data.get("allow_surrogate_keys", False))
        self.allow_natural_keys: bool = bool(data.get("allow_natural_keys", True))
        self.require_business_key_uniqueness: bool = bool(data.get("require_business_key_uniqueness", True))
        self.surrogate_key_patterns: List[str] = data.get("surrogate_key_patterns", ["id", "{table}id", "{table}_id"])
        self.business_key_patterns: List[str] = data.get("business_key_patterns", ["ma", "code", "so", "phieu", "chungtu", "number", "no"])
        self.natural_key_aliases: Dict[str, Dict[str, List[str]]] = {}
        
        aliases = data.get("natural_key_aliases", {}) or {}
        for table_canon, key_aliases in aliases.items():
            if isinstance(key_aliases, dict):
                self.natural_key_aliases[table_canon] = {
                    str(k): [str(x) for x in v] if isinstance(v, list) else [str(v)]
                    for k, v in key_aliases.items()
                }


class SchemaConfig:
    def __init__(self, data: Dict[str, Any]):
        self.matching_threshold: float = data.get("matching_threshold", 0.8)
        self.table_accept_threshold: float = data.get("table_accept_threshold", 0.90)
        self.table_ambiguous_threshold: float = data.get("table_ambiguous_threshold", 0.75)
        self.column_accept_threshold: float = data.get("column_accept_threshold", 0.88)
        self.column_ambiguous_threshold: float = data.get("column_ambiguous_threshold", 0.75)

        aliases = data.get("aliases", {})
        self.tables: Dict[str, List[str]] = aliases.get("tables", {})
        
        kg_data = data.get("key_grading", {}) or {}
        self.key_grading = KeyGradingConfig(kg_data)


        raw_abbs = data.get("abbreviations", {}) or {}
        self.abbreviations: Dict[str, str] = {}
        for k, v in raw_abbs.items():
            norm_k = _remove_accents(str(k)).lower().strip()
            norm_k = re.sub(r'[_\s\-]+', '', norm_k)
            norm_v = _remove_accents(str(v)).lower().strip()
            norm_v = re.sub(r'[_\s\-]+', '', norm_v)
            self.abbreviations[norm_k] = norm_v


        cols_data = aliases.get("columns", {})
        self.columns_global: Dict[str, List[str]] = {}
        self.columns_by_table: Dict[str, Dict[str, List[str]]] = {}

        if isinstance(cols_data, dict) and ("global" in cols_data or "by_table" in cols_data):
            self.columns_global = cols_data.get("global", {}) or {}
            self.columns_by_table = cols_data.get("by_table", {}) or {}
        else:
            # Backward compatibility: treat flat dict as global
            self.columns_global = cols_data or {}

        # Excluded tables: always add defaults, then add config-specified ones
        raw_excluded: List[str] = data.get("excluded_tables", []) or []
        self._excluded_normalized: Set[str] = set()
        for t in list(_DEFAULT_EXCLUDED_TABLES) + [str(x) for x in raw_excluded]:
            self._excluded_normalized.add(_normalize_table_name_for_exclusion(t))

    def is_excluded(self, table_name: str) -> bool:
        """Return True if table_name (raw or canonical) should be excluded from grading."""
        return _normalize_table_name_for_exclusion(table_name) in self._excluded_normalized


class SqlRewriteConfig:
    def __init__(self, data: Dict[str, Any]):
        if not data:
            data = {}
        self.enabled: bool = bool(data.get("enabled", True))
        self.use_existing_mapping_reports: bool = bool(data.get("use_existing_mapping_reports", True))
        self.reject_unsafe_sql: bool = bool(data.get("reject_unsafe_sql", True))
        self.execute_on_answer_db: bool = bool(data.get("execute_on_answer_db", True))
        self.allow_weak_column_aliases: bool = bool(data.get("allow_weak_column_aliases", False))
        self.allow_weak_table_aliases: bool = bool(data.get("allow_weak_table_aliases", False))
        self.max_execution_seconds: int = int(data.get("max_execution_seconds", 10))


class AssignmentConfig:
    def __init__(self, data: Dict[str, Any]):
        assignment = data.get("assignment", {})
        self.name: str = assignment.get("name", "SQL Server Assignment")
        self.protected_answer_db: str = assignment.get("protected_answer_db", "00000001")

        schema_data = data.get("schema", {})
        self.schema = SchemaConfig(schema_data)

        # Parse views configuration
        views_data = data.get("views", {})
        self.views_mode: str = "answer_snapshot"
        # execution_mode: how view outputs are obtained for comparison.
        #   compare_existing_data  — restore DBs and SELECT without any seeding (default)
        #   compare_seeded_test_data — seed test CSV data before querying (legacy)
        self.execution_mode: str = "compare_existing_data"
        self.export_outputs: bool = True   # write raw answer/student CSVs
        self.compare_as_multiset: bool = True  # row-order-insensitive by default

        views_list: List[Dict[str, Any]] = []
        sql_rewrite_data = {}
        if isinstance(views_data, list):
            # Backward compat: bare list → explicit_config + seeded mode
            self.views_mode = "explicit_config"
            self.execution_mode = "compare_seeded_test_data"
            views_list = views_data
        elif isinstance(views_data, dict):
            self.views_mode = views_data.get("mode", "answer_snapshot")
            self.execution_mode = views_data.get("execution_mode", "compare_existing_data")
            self.export_outputs = bool(views_data.get("export_outputs", True))
            self.compare_as_multiset = bool(views_data.get("compare_as_multiset", True))
            views_list = views_data.get("expected", []) or []
            sql_rewrite_data = views_data.get("sql_rewrite", {}) or {}

        self.views: List[ViewConfig] = [ViewConfig(v) for v in views_list]
        self.sql_rewrite = SqlRewriteConfig(sql_rewrite_data)

        # type_compatibility lives under schema: in YAML; fall back to top-level for compat
        tc_data = schema_data.get("type_compatibility") or data.get("type_compatibility") or {}
        self.type_compatibility = TypeCompatibilityConfig(tc_data)


def load_config(config_path: str) -> AssignmentConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found at: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError("Config file is empty or invalid")

    return AssignmentConfig(data)
