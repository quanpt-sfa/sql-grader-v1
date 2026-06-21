import csv
import logging
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple, Set, Optional
import pandas as pd
from dbcheck.config import AssignmentConfig

logger = logging.getLogger("dbcheck")

# Define status lists based on user specification
PASS_STATUSES = {
    "TABLE_MATCHED_EXACT", "TABLE_MATCHED_ALIAS", "TABLE_MATCHED_ABBREVIATION", "TABLE_MATCHED_FUZZY_HIGH",
    "COLUMN_MATCHED_EXACT", "COLUMN_MATCHED_ALIAS", "COLUMN_MATCHED_ABBREVIATION",
    "PK_MATCH_EXACT", "PK_MATCH_ALIAS_EQUIVALENT", "PK_SURROGATE_ACCEPTED", "PK_NATURAL_ACCEPTED",
    "FK_MATCH_EXACT", "FK_ALIAS_EQUIVALENT", "FK_SURROGATE_ACCEPTED", "FK_NATURAL_ACCEPTED",
    "FK_RELATIONSHIP_MATCH", "FK_RELATIONSHIP_MATCH_ALIAS_EQUIVALENT", "FK_RELATIONSHIP_SURROGATE_ACCEPTED", "FK_RELATIONSHIP_NATURAL_ACCEPTED",
    "VIEW_PASS", "VIEW_OUTPUT_MATCH", "PASS"
}

FAIL_STATUSES = {
    "MISSING", "COLUMN_MISSING_ANSWER", "PK_MISSING", "PK_INVALID", "FK_MISSING", "FK_WRONG_TARGET",
    "FK_RELATIONSHIP_MISSING", "FK_RELATIONSHIP_WRONG_PARENT", "FK_RELATIONSHIP_WRONG_CHILD",
    "FK_RELATIONSHIP_WRONG_CHILD_COLUMNS", "FK_RELATIONSHIP_WRONG_PARENT_COLUMNS",
    "VIEW_NOT_FOUND", "VIEW_EXECUTION_ERROR", "VIEW_VALUE_MISMATCH", "VIEW_ROW_COUNT_MISMATCH", "ROW_COUNT_MISMATCH",
    "VIEW_NO_MATCHING_OUTPUT", "VIEW_SQL_PARSE_ERROR", "VIEW_SQL_REWRITE_UNMAPPED_TABLE",
    "VIEW_SQL_REWRITE_UNMAPPED_COLUMN", "VIEW_SQL_REWRITE_UNSUPPORTED_VIEW_DEPENDENCY",
    "VIEW_SQL_DEFINITION_MISSING", "VIEW_ORDER_MISMATCH"
}

REVIEW_STATUSES = {
    "PK_REVIEW_REQUIRED", "FK_REVIEW_REQUIRED", "FK_IMPLIED_REVIEW_REQUIRED",
    "FK_RELATIONSHIP_IMPLIED_REVIEW_REQUIRED", "FK_RELATIONSHIP_AMBIGUOUS", "FK_RELATIONSHIP_MAPPING_ERROR",
    "MAPPING_AMBIGUOUS", "VIEW_MAPPING_AMBIGUOUS", "VIEW_OUTPUT_SCHEMA_MISMATCH",
    "TYPE_WARNING", "IDENTIFIER_TYPE_WARNING", "COLUMN_UNMAPPED_STUDENT",
    "EXTRA_REVIEW", "DUPLICATE_MAPPING_REVIEW", "SURROGATE_KEY_REVIEW",
    "TABLE_REVIEW_REQUIRED", "COLUMN_MATCHED_WEAK_ALIAS",
    "VIEW_OUTPUT_PARTIAL_MATCH", "VIEW_SQL_UNSAFE_REVIEW", "VIEW_SQL_REWRITE_AMBIGUOUS_COLUMN"
}

def load_rubric(rubric_path: Path) -> List[Dict[str, Any]]:
    """Load and validate grading rubric CSV."""
    if not rubric_path.exists():
        raise FileNotFoundError(f"Rubric file not found at: {rubric_path}")
        
    rubric_rows = []
    with open(rubric_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Validate required fields
            row["total_points"] = float(row.get("total_points", 0.0))
            if row["total_points"] < 0:
                raise ValueError("total_points must be non-negative")
            rubric_rows.append(row)
    return rubric_rows

def load_overrides(overrides_path: Path) -> List[Dict[str, Any]]:
    """Load manual overrides CSV if present."""
    if not overrides_path or not overrides_path.exists():
        return []
        
    overrides_list = []
    with open(overrides_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "override_points" in row and row["override_points"]:
                row["override_points"] = float(row["override_points"])
            else:
                row["override_points"] = 0.0
            overrides_list.append(row)
    return overrides_list

def safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        text = str(value).strip()
        if text == "":
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default

def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        text = str(value).strip()
        if text == "":
            return default
        return float(text)
    except (TypeError, ValueError):
        return default

def get_answer_atomic_items(run_dir: Path, config: AssignmentConfig) -> Dict[str, List[Any]]:
    """Retrieve ground-truth atomic items from answer snapshot folder."""
    snap_dir = run_dir / "answer_snapshot"
    items = {
        "tables": [],
        "columns": [],
        "primary_keys": [],
        "foreign_keys": [],
        "row_counts": [],
        "views": []
    }
    
    # 1. Tables & Row counts
    tables_file = snap_dir / "tables.csv"
    if tables_file.exists():
        with open(tables_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t_name = r.get("table_name")
                if t_name and not config.schema.is_excluded(t_name):
                    items["tables"].append(t_name)
                    items["row_counts"].append(t_name)
                    
    # 2. Columns
    cols_file = snap_dir / "columns.csv"
    if cols_file.exists():
        with open(cols_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t_name = r.get("table_name")
                c_name = r.get("column_name")
                if t_name and c_name and not config.schema.is_excluded(t_name):
                    items["columns"].append(f"{t_name}.{c_name}")
                    
    # 3. Primary Keys
    pks_file = snap_dir / "primary_keys.csv"
    if pks_file.exists():
        seen_pks = set()
        with open(pks_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t_name = r.get("table_name")
                if t_name and not config.schema.is_excluded(t_name) and t_name not in seen_pks:
                    seen_pks.add(t_name)
                    items["primary_keys"].append(t_name)
                    
    # 4. Foreign Keys
    fks_file = snap_dir / "foreign_keys.csv"
    fk_sigs = []
    use_report = True
    
    from dbcheck.snapshot.normalizer import NameNormalizer
    normalizer = NameNormalizer(config)
    
    if fks_file.exists():
        try:
            with open(fks_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames or []
                required = {"parent_table_canonical", "parent_column_canonical", 
                            "referenced_table_canonical", "referenced_column_canonical"}
                if required.issubset(set(headers)):
                    rows = list(reader)
                    if rows:
                        by_name = {}
                        for r in rows:
                            fk_name = r.get("fk_name") or ""
                            by_name.setdefault(fk_name, []).append(r)
                            
                        for fk_name, fk_rows in by_name.items():
                            first = fk_rows[0]
                            c_child_t = first.get("parent_table_canonical") or ""
                            c_parent_t = first.get("referenced_table_canonical") or ""
                            
                            if config.schema.is_excluded(c_child_t) or config.schema.is_excluded(c_parent_t):
                                continue
                                
                            col_pairs = []
                            for r in fk_rows:
                                pc = r.get("parent_column_canonical") or ""
                                rc = r.get("referenced_column_canonical") or ""
                                c_id = r.get("constraint_column_id")
                                if c_id is not None and c_id != "":
                                    try: c_id = int(c_id)
                                    except ValueError: c_id = None
                                else:
                                    c_id = None
                                col_pairs.append((pc, rc, c_id))
                                
                            has_ordinals = all(p[2] is not None for p in col_pairs)
                            if has_ordinals:
                                sorted_pairs = sorted(col_pairs, key=lambda x: x[2])
                            else:
                                sorted_pairs = sorted(col_pairs, key=lambda x: (x[0], x[1]))
                                
                            child_cols = [p[0] for p in sorted_pairs]
                            parent_cols = [p[1] for p in sorted_pairs]
                            
                            sig = f"{c_child_t}|{c_parent_t}|{','.join(child_cols)}|{','.join(parent_cols)}"
                            if sig not in fk_sigs:
                                fk_sigs.append(sig)
                        use_report = False
        except Exception as e:
            pass

    if use_report:
        sub_dir = run_dir / "submissions"
        if sub_dir.exists():
            for student_folder in sub_dir.iterdir():
                if student_folder.is_dir():
                    fk_report = student_folder / "reports" / "fk_relationship_report.csv"
                    if fk_report.exists():
                        try:
                            with open(fk_report, "r", encoding="utf-8") as f:
                                for r in csv.DictReader(f):
                                    sig = r.get("answer_relationship_signature")
                                    if not sig:
                                        c_child_t = r.get("answer_child_table") or ""
                                        c_parent_t = r.get("answer_parent_table") or ""
                                        child_cols = r.get("answer_child_columns") or ""
                                        parent_cols = r.get("answer_parent_columns") or ""
                                        if c_child_t and c_parent_t:
                                            sig = f"{c_child_t}|{c_parent_t}|{child_cols}|{parent_cols}"
                                    if sig and sig not in fk_sigs:
                                        parts = sig.split("|")
                                        if len(parts) >= 2:
                                            c_child_t = parts[0]
                                            c_parent_t = parts[1]
                                            if config.schema.is_excluded(c_child_t) or config.schema.is_excluded(c_parent_t):
                                                continue
                                        fk_sigs.append(sig)
                            if fk_sigs:
                                break
                        except Exception as e:
                            pass
                            
    items["foreign_keys"] = fk_sigs
                            
    # 5. Views
    views_file = snap_dir / "views.csv"
    if views_file.exists():
        with open(views_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                v_name = r.get("view_name") or r.get("object_name")
                if v_name:
                    items["views"].append(v_name)
                    
    return items

def get_submission_statuses(reports_dir: Path) -> Dict[str, Dict[str, str]]:
    """Load grading item status dictionaries for a single submission."""
    statuses = {
        "tables": {},
        "columns": {},
        "primary_keys": {},
        "foreign_keys": {},
        "row_counts": {},
        "views": {}
    }
    
    # 1. Tables mapping status
    table_map_file = reports_dir / "table_mapping_report.csv"
    if table_map_file.exists():
        with open(table_map_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ans = r.get("answer_table")
                if ans:
                    statuses["tables"][ans] = r.get("match_status", "MISSING")
                    
    # 2. Columns mapping status
    col_map_file = reports_dir / "column_mapping_report.csv"
    if col_map_file.exists():
        with open(col_map_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ans_t = r.get("answer_table")
                ans_c = r.get("answer_column")
                if ans_t and ans_c:
                    statuses["columns"][f"{ans_t}.{ans_c}"] = r.get("match_status", "MISSING")
                    
    # 3. Primary keys
    pk_file = reports_dir / "key_adequacy_report.csv"
    if pk_file.exists():
        with open(pk_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t_name = r.get("table_name")
                if t_name:
                    statuses["primary_keys"][t_name] = r.get("key_status", "PK_MISSING")
                    
    # 4. Foreign keys
    fk_file = reports_dir / "fk_relationship_report.csv"
    if fk_file.exists():
        with open(fk_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                sig = r.get("answer_relationship_signature")
                if not sig:
                    c_child_t = r.get("answer_child_table") or ""
                    c_parent_t = r.get("answer_parent_table") or ""
                    child_cols = r.get("answer_child_columns") or ""
                    parent_cols = r.get("answer_parent_columns") or ""
                    if c_child_t and c_parent_t:
                        sig = f"{c_child_t}|{c_parent_t}|{child_cols}|{parent_cols}"
                if sig:
                    statuses["foreign_keys"][sig] = r.get("fk_status", "FK_MISSING")
                    
    # 5. Row counts (extracted from table mapping and structure reports)
    struct_file = reports_dir / "structure_report.csv"
    mismatch_tables = set()
    if struct_file.exists():
        with open(struct_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("component") == "table" and r.get("status") == "ROW_COUNT_MISMATCH":
                    mismatch_tables.add(r.get("answer_object"))
    # Default is PASS if table matched and no mismatch warning exists
    for t_name, t_status in statuses["tables"].items():
        if t_name in mismatch_tables:
            statuses["row_counts"][t_name] = "ROW_COUNT_MISMATCH"
        elif t_status == "MISSING" or "MISSING" in t_status:
            statuses["row_counts"][t_name] = "MISSING"
        else:
            statuses["row_counts"][t_name] = "PASS"
            
    # 6. Views
    views_file = reports_dir / "view_test_report.csv"
    if views_file.exists():
        with open(views_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                v_name = r.get("answer_view")
                if v_name:
                    statuses["views"][v_name] = r
                    
    return statuses

def _normalize_view_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())

def _view_scope_matches(scope: str, answer_view: str) -> bool:
    scope = (scope or "").strip()
    answer_view = (answer_view or "").strip()
    if not scope or not answer_view:
        return False
    if scope == answer_view:
        return True
    if _normalize_view_key(scope) == _normalize_view_key(answer_view):
        return True
    pattern = rf"(^|[^A-Za-z0-9]){re.escape(scope)}([^A-Za-z0-9]|$)"
    return re.search(pattern, answer_view, flags=re.IGNORECASE) is not None

def _resolve_view_result(view_statuses: Dict[str, Dict[str, Any]], scope: str) -> Optional[Dict[str, Any]]:
    if scope in view_statuses:
        return view_statuses[scope]
    matches = [
        row for answer_view, row in view_statuses.items()
        if _view_scope_matches(scope, answer_view)
    ]
    if len(matches) == 1:
        return matches[0]
    return None

def match_override(sub_id: str, row: Dict[str, Any], overrides: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Check if manual override matches a student and rubric row."""
    for o in overrides:
        if o["submission_id"].strip() != sub_id:
            continue
        # Compare section, component and answer_object
        if "section" in o and o["section"].strip() != row["section"].strip():
            continue
        if o["component"].lower().strip() != row["component"].lower().strip():
            continue
        # Compare answer_object to scope or object_name
        o_obj = o.get("answer_object", "").strip()
        r_scope = row.get("scope", "").strip()
        r_obj = row.get("object_name", "").strip()
        
        if not o_obj or o_obj.lower() == "all":
            if r_scope.lower() == "all":
                return o
        else:
            if o_obj == r_scope or o_obj == r_obj:
                return o
    return None

def score_submission(
    sub_id: str,
    manifest_status: str,
    run_dir: Path,
    config: AssignmentConfig,
    rubric: List[Dict[str, Any]],
    overrides: List[Dict[str, Any]],
    answer_items: Dict[str, List[Any]]
) -> Tuple[List[Dict[str, Any]], float, int, int]:
    """Score a single student submission."""
    details = []
    total_score = 0.0
    review_required_count = 0
    hard_error_count = 0
    
    if manifest_status != "OK":
        # Database restore failed or snapshot was not generated
        hard_error_count += 1
        for row in rubric:
            d = {
                "submission_id": sub_id,
                "section": row["section"],
                "component": row["component"],
                "answer_object": row["scope"],
                "student_object": "",
                "status": "FAIL_RESTORE_OR_SNAPSHOT",
                "points_possible": row["total_points"],
                "original_points_awarded": 0.0,
                "final_points_awarded": 0.0,
                "review_required": False,
                "override_applied": False,
                "reviewer_note": "Submission restore failed",
                "source_report": "manifest.csv",
                "message": "Database restore failed"
            }
            # Check override anyway
            o = match_override(sub_id, row, overrides)
            if o:
                d["final_points_awarded"] = o["override_points"]
                d["override_applied"] = True
                d["reviewer_note"] = o.get("reviewer_note", "")
                
            details.append(d)
            total_score += d["final_points_awarded"]
        return details, total_score, 0, hard_error_count
        
    reports_dir = run_dir / "submissions" / sub_id / "reports"
    sub_statuses = get_submission_statuses(reports_dir)
    
    for row in rubric:
        comp = row["component"]
        scope = row["scope"]
        obj_name = row["object_name"]
        total_pts = row["total_points"]
        mode = row["scoring_mode"]
        inc_statuses = set(s.strip() for s in row.get("include_statuses", "").split("|") if s.strip())
        if comp == "foreign_keys":
            extra_inc = set()
            for s in inc_statuses:
                if s == "FK_MATCH_EXACT":
                    extra_inc.add("FK_RELATIONSHIP_MATCH")
                elif s == "FK_RELATIONSHIP_MATCH":
                    extra_inc.add("FK_MATCH_EXACT")
                elif s == "FK_ALIAS_EQUIVALENT":
                    extra_inc.add("FK_RELATIONSHIP_MATCH_ALIAS_EQUIVALENT")
                elif s == "FK_RELATIONSHIP_MATCH_ALIAS_EQUIVALENT":
                    extra_inc.add("FK_ALIAS_EQUIVALENT")
                elif s == "FK_SURROGATE_ACCEPTED":
                    extra_inc.add("FK_RELATIONSHIP_SURROGATE_ACCEPTED")
                elif s == "FK_RELATIONSHIP_SURROGATE_ACCEPTED":
                    extra_inc.add("FK_SURROGATE_ACCEPTED")
                elif s == "FK_NATURAL_ACCEPTED":
                    extra_inc.add("FK_RELATIONSHIP_NATURAL_ACCEPTED")
                elif s == "FK_RELATIONSHIP_NATURAL_ACCEPTED":
                    extra_inc.add("FK_NATURAL_ACCEPTED")
            inc_statuses.update(extra_inc)
        policy = row.get("partial_policy", "").strip()
        
        # 1. Filter expected items matching this rubric row scope
        expected = []
        if comp in answer_items:
            expected_all = answer_items[comp]
            if scope.lower() == "all":
                expected = expected_all
            else:
                if comp == "columns":
                    # scope represents table, object_name represents column
                    prefix = f"{scope}."
                    if obj_name:
                        expected = [c for c in expected_all if c == f"{scope}.{obj_name}"]
                    else:
                        expected = [c for c in expected_all if c.startswith(prefix)]
                elif comp == "foreign_keys":
                    # scope represents child_table, parent_table or fk_name
                    expected = [fk for fk in expected_all if scope in fk]
                else:
                    expected = [item for item in expected_all if item == scope]
                    
        # 2. Evaluate point outcomes
        orig_points = 0.0
        row_review_required = False
        status_desc = []
        source_report = ""
        msg = ""
        
        if mode in ("manual", "manual_only"):
            orig_points = 0.0
            status_desc.append("MANUAL")
            msg = "Manual grading required"
        elif mode == "proportional":
            num_expected = len(expected)
            if num_expected == 0:
                orig_points = 0.0
                status_desc.append("NO_ITEMS")
            else:
                pts_per_item = total_pts / num_expected
                passed_items = 0
                for item in expected:
                    # Look up student status
                    item_status = sub_statuses.get(comp, {}).get(item, "MISSING")
                    status_desc.append(f"{item}:{item_status}")
                    
                    if item_status in inc_statuses:
                        passed_items += 1
                        orig_points += pts_per_item
                    elif item_status in REVIEW_STATUSES:
                        if policy == "review_pending":
                            row_review_required = True
                        elif policy == "warning_pass":
                            passed_items += 1
                            orig_points += pts_per_item
                            row_review_required = True
                            
                msg = f"Passed {passed_items}/{num_expected} items"
                # Map source report
                source_report = {
                    "tables": "table_mapping_report.csv",
                    "columns": "column_mapping_report.csv",
                    "primary_keys": "key_adequacy_report.csv",
                    "foreign_keys": "fk_relationship_report.csv",
                    "row_counts": "structure_report.csv"
                }.get(comp, "structure_report.csv")
                
        elif mode == "weighted_subchecks":
            # Typically views scoring
            view_name = scope
            view_res = _resolve_view_result(sub_statuses.get("views", {}), view_name)
            source_report = "view_test_report.csv"
            
            if not view_res:
                orig_points = 0.0
                status_desc.append("VIEW_NOT_FOUND")
                msg = "View test result not found"
            else:
                v_status = view_res.get("status", "VIEW_NOT_FOUND")
                status_desc.append(v_status)
                
                if v_status in inc_statuses:
                    orig_points = total_pts
                    msg = "View passed fully"
                elif v_status in ("VIEW_NOT_FOUND", "VIEW_NO_MATCHING_OUTPUT", "VIEW_EXECUTION_ERROR",
                                  "VIEW_SQL_PARSE_ERROR", "VIEW_SQL_REWRITE_UNMAPPED_TABLE",
                                  "VIEW_SQL_REWRITE_UNMAPPED_COLUMN", "VIEW_SQL_REWRITE_AMBIGUOUS_COLUMN",
                                  "VIEW_SQL_REWRITE_UNSUPPORTED_VIEW_DEPENDENCY",
                                  "VIEW_SQL_DEFINITION_MISSING",
                                  "VIEW_SQL_UNSAFE_REVIEW", "VIEW_MAPPING_AMBIGUOUS"):
                    # If view is missing or fails, subchecks receive zero
                    orig_points = 0.0
                    msg = f"View execution failed, rewrite error, unsafe, or not found: {v_status}"
                elif policy == "partial_view":
                    # Subchecks evaluated independently
                    # Check if view is order sensitive
                    v_config = next((v for v in config.views if v.answer_view == view_name), None)
                    order_sensitive = v_config.order_sensitive if v_config else False
                    
                    if order_sensitive:
                        weights = {"exists": 0.2, "schema": 0.2, "row_count": 0.2, "value_match": 0.3, "order_match": 0.1}
                    else:
                        weights = {"exists": 2/9, "schema": 2/9, "row_count": 2/9, "value_match": 3/9, "order_match": 0.0}
                        
                    # Determine subcheck truths
                    missing_cols = view_res.get("missing_columns", "").strip()
                    row_count_ans = safe_int(view_res.get("row_count_answer", 0))
                    row_count_stud = safe_int(view_res.get("row_count_student", 0))
                    val_mismatch = safe_int(view_res.get("value_mismatch_count", 0))
                    safe_int(view_res.get("answer_minus_student_count", 0))
                    safe_int(view_res.get("student_minus_answer_count", 0))
                    safe_float(view_res.get("schema_score", 0.0))
                    safe_float(view_res.get("row_count_score", 0.0))
                    safe_float(view_res.get("value_score", 0.0))
                    safe_float(view_res.get("order_score", 0.0))
                    safe_float(view_res.get("total_match_score", 0.0))
                    
                    subchecks = {
                        "exists": v_status not in ("VIEW_NOT_FOUND", "VIEW_NO_MATCHING_OUTPUT"),
                        "schema": (v_status not in ("VIEW_NOT_FOUND", "VIEW_NO_MATCHING_OUTPUT")) and (not missing_cols) and (v_status != "VIEW_OUTPUT_SCHEMA_MISMATCH"),
                        "row_count": (v_status not in ("VIEW_NOT_FOUND", "VIEW_NO_MATCHING_OUTPUT")) and (row_count_ans == row_count_stud) and (v_status not in ("VIEW_ROW_COUNT_MISMATCH", "VIEW_EXECUTION_ERROR")),
                        "value_match": (v_status not in ("VIEW_NOT_FOUND", "VIEW_NO_MATCHING_OUTPUT")) and (val_mismatch == 0) and (v_status not in ("VIEW_VALUE_MISMATCH", "VIEW_EXECUTION_ERROR")),
                        "order_match": (v_status not in ("VIEW_NOT_FOUND", "VIEW_NO_MATCHING_OUTPUT")) and (v_status in ("VIEW_PASS", "VIEW_OUTPUT_MATCH") or (v_status != "VIEW_ORDER_MISMATCH" and v_status != "VIEW_EXECUTION_ERROR"))
                    }
                    
                    weighted_score = 0.0
                    for check_name, check_pass in subchecks.items():
                        if check_pass:
                            weighted_score += weights[check_name]
                            
                    orig_points = total_pts * weighted_score
                    msg = f"Partial view match subchecks: {[k for k,v in subchecks.items() if v]}"
                else:
                    orig_points = 0.0
                    msg = f"View failed strict grading: {v_status}"
                    
        # 3. Construct grading detail row
        d = {
            "submission_id": sub_id,
            "section": row["section"],
            "component": comp,
            "answer_object": scope,
            "student_object": "",
            "status": ",".join(status_desc[:3]) if status_desc else "OK",
            "points_possible": total_pts,
            "original_points_awarded": orig_points,
            "final_points_awarded": orig_points,
            "review_required": row_review_required,
            "override_applied": False,
            "reviewer_note": "",
            "source_report": source_report,
            "message": msg
        }
        
        # 4. Apply override
        o = match_override(sub_id, row, overrides)
        if o:
            d["final_points_awarded"] = o["override_points"]
            d["override_applied"] = True
            d["reviewer_note"] = o.get("reviewer_note", "")
            if "override_status" in o and o["override_status"]:
                d["status"] = o["override_status"]
                
        details.append(d)
        total_score += d["final_points_awarded"]
        if d["review_required"]:
            review_required_count += 1
            
    # Check if there are any hard errors in student reports
    struct_report_path = reports_dir / "structure_report.csv"
    if struct_report_path.exists():
        with open(struct_report_path, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("status") in FAIL_STATUSES:
                    hard_error_count += 1
                    
    view_report_path = reports_dir / "view_test_report.csv"
    if view_report_path.exists():
        with open(view_report_path, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("status") in FAIL_STATUSES:
                    hard_error_count += 1
                    
    return details, total_score, review_required_count, hard_error_count

def write_xlsx_report(
    run_dir: Path,
    summary_rows: List[Dict[str, Any]],
    detail_rows: List[Dict[str, Any]],
    rubric_rows: List[Dict[str, Any]],
    overrides_rows: List[Dict[str, Any]]
) -> None:
    """Compile aggregated reports into multi-sheet grading_summary.xlsx."""
    output_path = run_dir / "grading_summary.xlsx"
    
    # Compile Review Required & Hard Errors sheets across all student reports
    reviews_list = []
    errors_list = []
    
    # Scan reviews and hard errors from summary folders
    rev_file = run_dir / "review_queue.csv"
    if rev_file.exists():
        try:
            reviews_list = pd.read_csv(rev_file).to_dict("records")
        except Exception:
            pass
            
    err_file = run_dir / "hard_errors.csv"
    if err_file.exists():
        try:
            errors_list = pd.read_csv(err_file).to_dict("records")
        except Exception:
            pass
            
    # Load all sheets to DataFrames
    df_summary = pd.DataFrame(summary_rows)
    df_detail = pd.DataFrame(detail_rows)
    df_rubric = pd.DataFrame(rubric_rows)
    df_overrides = pd.DataFrame(overrides_rows) if overrides_rows else pd.DataFrame(columns=["submission_id", "section", "component", "answer_object", "override_points", "override_status", "reviewer_note"])
    df_reviews = pd.DataFrame(reviews_list) if reviews_list else pd.DataFrame(columns=["submission_id", "component", "answer_object", "student_object", "status", "severity", "message", "evidence"])
    df_errors = pd.DataFrame(errors_list) if errors_list else pd.DataFrame(columns=["submission_id", "component", "answer_object", "student_object", "status", "severity", "message", "evidence"])
    
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df_summary.to_excel(writer, sheet_name="Summary", index=False)
        df_detail.to_excel(writer, sheet_name="Detail", index=False)
        df_rubric.to_excel(writer, sheet_name="Rubric", index=False)
        df_overrides.to_excel(writer, sheet_name="Overrides", index=False)
        df_reviews.to_excel(writer, sheet_name="Review Required", index=False)
        df_errors.to_excel(writer, sheet_name="Hard Errors", index=False)
        
    logger.info(f"Successfully compiled multi-sheet Excel report: {output_path}")
