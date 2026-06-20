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
        expected = data.get("expected_output", {})
        self.columns: List[Dict[str, Any]] = expected.get("columns", [])
        self.sort_by: List[str] = expected.get("sort_by", [])
        self.numeric_tolerance: float = expected.get("numeric_tolerance", 0.01)


class TypeCompatibilityConfig:
    def __init__(self, data: Dict[str, Any]):
        self.mode: str = data.get("mode", "group_with_warnings")
        self.compatible_warning_score: float = float(data.get("compatible_warning_score", 0.75))
        self.allow_integer_decimal_compatibility: bool = bool(data.get("allow_integer_decimal_compatibility", True))
        self.allow_decimal_float_compatibility: bool = bool(data.get("allow_decimal_float_compatibility", True))
        self.allow_bit_integer_compatibility: bool = bool(data.get("allow_bit_integer_compatibility", False))
        self.strict_length_check: bool = bool(data.get("strict_length_check", False))
        self.strict_precision_scale_check: bool = bool(data.get("strict_precision_scale_check", False))


# Tables that are always excluded regardless of config
_DEFAULT_EXCLUDED_TABLES: Set[str] = {"sysdiagrams"}


def _normalize_table_name_for_exclusion(name: str) -> str:
    """Normalize a table name for exclusion lookup: accent-removal + lowercase."""
    return _remove_accents(name).lower().strip()


class SchemaConfig:
    def __init__(self, data: Dict[str, Any]):
        self.matching_threshold: float = data.get("matching_threshold", 0.8)
        self.table_accept_threshold: float = data.get("table_accept_threshold", 0.90)
        self.table_ambiguous_threshold: float = data.get("table_ambiguous_threshold", 0.75)
        self.column_accept_threshold: float = data.get("column_accept_threshold", 0.88)
        self.column_ambiguous_threshold: float = data.get("column_ambiguous_threshold", 0.75)

        aliases = data.get("aliases", {})
        self.tables: Dict[str, List[str]] = aliases.get("tables", {})

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


class AssignmentConfig:
    def __init__(self, data: Dict[str, Any]):
        assignment = data.get("assignment", {})
        self.name: str = assignment.get("name", "SQL Server Assignment")
        self.protected_answer_db: str = assignment.get("protected_answer_db", "00000001")

        schema_data = data.get("schema", {})
        self.schema = SchemaConfig(schema_data)
        self.views: List[ViewConfig] = [ViewConfig(v) for v in data.get("views", [])]
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
