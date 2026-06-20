import yaml
from pathlib import Path
from typing import Dict, List, Any, Optional

class ViewConfig:
    def __init__(self, data: Dict[str, Any]):
        self.answer_view: str = data.get("answer_view", "")
        expected = data.get("expected_output", {})
        self.columns: List[Dict[str, Any]] = expected.get("columns", [])
        self.sort_by: List[str] = expected.get("sort_by", [])
        self.numeric_tolerance: float = expected.get("numeric_tolerance", 0.01)

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


class AssignmentConfig:
    def __init__(self, data: Dict[str, Any]):
        assignment = data.get("assignment", {})
        self.name: str = assignment.get("name", "SQL Server Assignment")
        self.protected_answer_db: str = assignment.get("protected_answer_db", "00000001")
        
        self.schema = SchemaConfig(data.get("schema", {}))
        self.views: List[ViewConfig] = [ViewConfig(v) for v in data.get("views", [])]

def load_config(config_path: str) -> AssignmentConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found at: {config_path}")
        
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    if not data:
        raise ValueError("Config file is empty or invalid")
        
    return AssignmentConfig(data)
