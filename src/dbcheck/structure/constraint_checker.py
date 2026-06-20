import re
from typing import List, Dict, Any

def get_is_identity(table_name: str, col_name: str, stud_cols: List[Dict[str, Any]]) -> int:
    col_name_l = col_name.lower().strip()
    table_name_l = table_name.lower().strip()
    for c in stud_cols:
        if c.get("table_name", "").lower().strip() == table_name_l and c.get("column_name", "").lower().strip() == col_name_l:
            return c.get("is_identity", 0)
    return 0

def get_is_nullable(table_name: str, col_name: str, stud_cols: List[Dict[str, Any]]) -> int:
    col_name_l = col_name.lower().strip()
    table_name_l = table_name.lower().strip()
    for c in stud_cols:
        if c.get("table_name", "").lower().strip() == table_name_l and c.get("column_name", "").lower().strip() == col_name_l:
            return c.get("is_nullable", 1)
    return 1

def is_surrogate_column(col_name: str, table_name: str, is_identity: int, config: Any) -> bool:
    """Check if a column is a surrogate ID based on patterns in config."""
    if not config or not hasattr(config.schema, "key_grading"):
        return False
    kg = config.schema.key_grading
    if kg.mode != "adequacy":
        return False
    if is_identity == 1:
        return True
        
    col_name_l = col_name.lower().strip()
    if col_name_l == "id" or col_name_l.endswith("_id"):
        return True
        
    table_name_l = table_name.lower().strip()
    for pattern in kg.surrogate_key_patterns:
        pattern_l = pattern.lower().strip()
        if pattern_l == "id":
            if col_name_l == "id":
                return True
        else:
            resolved = pattern_l.replace("{table}", table_name_l)
            if col_name_l == resolved:
                return True
            resolved_clean = pattern_l.replace("{table}", re.sub(r'[\s_–\-]', '', table_name_l))
            if col_name_l == resolved_clean:
                return True
    return False

def suggests_relationship(stud_child_t: str, ans_parent_t: str, ans_parent_col: str, stud_cols: List[Dict[str, Any]], config: Any) -> bool:
    """Check if child table has columns implying relationship to parent table."""
    parent_key_canon = ans_parent_col.lower()
    parent_surr_names = [ans_parent_t.lower() + "id", ans_parent_t.lower() + "_id", "id"]
    
    for c in stud_cols:
        if c.get("table_name", "") == stud_child_t:
            c_name_l = c.get("column_name", "").lower()
            c_canon_l = (c.get("column_name_canonical") or "").lower()
            if c_canon_l == parent_key_canon or c_name_l == parent_key_canon:
                return True
            if c_name_l in parent_surr_names:
                return True
            if config and hasattr(config.schema, "key_grading"):
                for pat in config.schema.key_grading.surrogate_key_patterns:
                    pat_resolved = pat.lower().replace("{table}", ans_parent_t.lower())
                    if c_name_l == pat_resolved:
                        return True
    return False

def get_raw_col_name(table_canon: str, col_canon: str, cols_list: List[Dict[str, Any]], is_student: bool) -> str:
    for c in cols_list:
        if (c.get("table_name_canonical") == table_canon or c.get("table_name") == table_canon):
            if c.get("column_name_canonical") == col_canon:
                return c.get("column_name", "")
    return col_canon


def match_constraints(
    ans_pks: List[Dict[str, Any]], stud_pks: List[Dict[str, Any]],
    ans_fks: List[Dict[str, Any]], stud_fks: List[Dict[str, Any]],
    accepted_table_pairs: Any,
    config: Any = None,
    column_mappings: List[Dict[str, Any]] = None,
    ans_cols: List[Dict[str, Any]] = None,
    stud_cols: List[Dict[str, Any]] = None,
    stud_uniques: List[Dict[str, Any]] = None
) -> Any:
    # If legacy mode or config is missing, fall back to exact mode
    mode = "exact"
    if config and hasattr(config.schema, "key_grading"):
        mode = config.schema.key_grading.mode

    if isinstance(accepted_table_pairs, set):
        accepted_set = accepted_table_pairs
        accepted_dict = {t: t for t in accepted_set}
    else:
        accepted_dict = accepted_table_pairs or {}
        accepted_set = set(accepted_dict.keys())

    if mode == "exact" or column_mappings is None or ans_cols is None or stud_cols is None:
        legacy_res = _match_constraints_exact(ans_pks, stud_pks, ans_fks, stud_fks, accepted_set)
        # Return tuple compatibility: (results, key_report, fk_report, counts)
        return legacy_res, [], [], {}

    return _match_constraints_adequacy(
        ans_pks, stud_pks, ans_fks, stud_fks,
        accepted_dict, config, column_mappings,
        ans_cols, stud_cols, stud_uniques
    )


def _match_constraints_exact(
    ans_pks: List[Dict[str, Any]], stud_pks: List[Dict[str, Any]],
    ans_fks: List[Dict[str, Any]], stud_fks: List[Dict[str, Any]],
    accepted_tables: set
) -> List[Dict[str, Any]]:
    # Filter PKs and FKs to only accepted tables
    ans_pks = [pk for pk in ans_pks if pk.get("table_name_canonical") in accepted_tables]
    stud_pks = [pk for pk in stud_pks if pk.get("table_name_canonical") in accepted_tables]
    ans_fks = [fk for fk in ans_fks if fk.get("parent_table_canonical") in accepted_tables and fk.get("referenced_table_canonical") in accepted_tables]
    stud_fks = [fk for fk in stud_fks if fk.get("parent_table_canonical") in accepted_tables and fk.get("referenced_table_canonical") in accepted_tables]

    results = []

    # 1. Match Primary Keys
    ans_table_pks = {}
    for pk in ans_pks:
        t_canon = pk["table_name_canonical"]
        if t_canon:
            ans_table_pks.setdefault(t_canon, []).append(pk)
            
    stud_table_pks = {}
    for pk in stud_pks:
        t_canon = pk["table_name_canonical"]
        if t_canon:
            stud_table_pks.setdefault(t_canon, []).append(pk)

    for t_canon in ans_table_pks:
        ans_table_pks[t_canon].sort(key=lambda x: int(x["key_ordinal"]))
    for t_canon in stud_table_pks:
        stud_table_pks[t_canon].sort(key=lambda x: int(x["key_ordinal"]))

    for t_canon, ans_cols in ans_table_pks.items():
        ans_col_names = [col["column_name_canonical"] for col in ans_cols]
        
        if t_canon not in stud_table_pks:
            results.append({
                "component": "pk",
                "answer_object": f"{t_canon}.PK",
                "student_object": "",
                "status": "MISSING",
                "severity": "high",
                "message": f"Primary key constraint is missing on table '{t_canon}'",
                "evidence": f"Expected columns: {ans_col_names}"
            })
        else:
            stud_cols = stud_table_pks[t_canon]
            stud_col_names = [col["column_name_canonical"] for col in stud_cols]
            
            if ans_col_names == stud_col_names:
                results.append({
                    "component": "pk",
                    "answer_object": f"{t_canon}.PK",
                    "student_object": f"{t_canon}.PK",
                    "status": "PASS",
                    "severity": "info",
                    "message": f"Primary key columns match",
                    "evidence": f"Columns: {stud_col_names}"
                })
            else:
                results.append({
                    "component": "pk",
                    "answer_object": f"{t_canon}.PK",
                    "student_object": f"{t_canon}.PK",
                    "status": "PK_MISMATCH",
                    "severity": "high",
                    "message": f"Primary key column mismatch on table '{t_canon}'",
                    "evidence": f"Expected: {ans_col_names}, Got: {stud_col_names}"
                })

    # 2. Match Foreign Keys
    ans_fk_set = {}
    for fk in ans_fks:
        key = (
            fk["parent_table_canonical"],
            fk["parent_column_canonical"],
            fk["referenced_table_canonical"],
            fk["referenced_column_canonical"]
        )
        ans_fk_set[key] = fk

    stud_fk_set = {}
    for fk in stud_fks:
        key = (
            fk["parent_table_canonical"],
            fk["parent_column_canonical"],
            fk["referenced_table_canonical"],
            fk["referenced_column_canonical"]
        )
        stud_fk_set[key] = fk

    for key, ans_fk in ans_fk_set.items():
        parent_t, parent_c, ref_t, ref_c = key
        ans_obj = f"FK: {parent_t}.{parent_c} -> {ref_t}.{ref_c}"
        
        if key not in stud_fk_set:
            results.append({
                "component": "fk",
                "answer_object": ans_obj,
                "student_object": "",
                "status": "MISSING",
                "severity": "high",
                "message": f"Foreign key relationship from {parent_t}({parent_c}) to {ref_t}({ref_c}) is missing",
                "evidence": f"Expected: {ans_fk['fk_name']}"
            })
        else:
            stud_fk = stud_fk_set[key]
            rules_match = (ans_fk["delete_rule"] == stud_fk["delete_rule"] and
                           ans_fk["update_rule"] == stud_fk["update_rule"])
            rule_msg = ""
            if not rules_match:
                rule_msg = f" (Rule mismatch: Answer DR={ans_fk['delete_rule']}/UR={ans_fk['update_rule']}, Student DR={stud_fk['delete_rule']}/UR={stud_fk['update_rule']})"
                
            results.append({
                "component": "fk",
                "answer_object": ans_obj,
                "student_object": f"FK: {stud_fk['parent_table_canonical']}.{stud_fk['parent_column_canonical']} -> {stud_fk['referenced_table_canonical']}.{stud_fk['referenced_column_canonical']}",
                "status": "PASS" if rules_match else "RULE_MISMATCH",
                "severity": "info" if rules_match else "low",
                "message": "Foreign key relationship matches" + rule_msg,
                "evidence": f"Mapped: {stud_fk['fk_name']} to {ans_fk['fk_name']}"
            })

    for key, stud_fk in stud_fk_set.items():
        if key not in ans_fk_set:
            parent_t, parent_c, ref_t, ref_c = key
            results.append({
                "component": "fk",
                "answer_object": "",
                "student_object": f"FK: {parent_t}.{parent_c} -> {ref_t}.{ref_c}",
                "status": "EXTRA",
                "severity": "low",
                "message": f"Extra foreign key relationship found in student database: {parent_t}({parent_c}) to {ref_t}({ref_c})",
                "evidence": f"FK Name: {stud_fk['fk_name']}"
            })

    return results


def _match_constraints_adequacy(
    ans_pks: List[Dict[str, Any]], stud_pks: List[Dict[str, Any]],
    ans_fks: List[Dict[str, Any]], stud_fks: List[Dict[str, Any]],
    accepted_table_pairs: Dict[str, str],
    config: Any,
    column_mappings: List[Dict[str, Any]],
    ans_cols: List[Dict[str, Any]],
    stud_cols: List[Dict[str, Any]],
    stud_uniques: List[Dict[str, Any]]
) -> tuple:
    structure_results = []
    key_adequacy_results = []
    fk_relationship_results = []
    
    # Initialize counts
    counts = {
        "pk_exact_match_count": 0, "pk_alias_equivalent_count": 0, "pk_surrogate_accepted_count": 0,
        "pk_natural_accepted_count": 0, "pk_alternative_accepted_count": 0, "pk_review_required_count": 0,
        "pk_missing_count": 0, "pk_invalid_count": 0,
        "fk_exact_match_count": 0, "fk_relationship_match_count": 0, "fk_alias_equivalent_count": 0,
        "fk_surrogate_accepted_count": 0, "fk_natural_accepted_count": 0, "fk_review_required_count": 0,
        "fk_missing_count": 0, "fk_wrong_target_count": 0
    }

    # Helpers
    ans_table_pks = {}
    for pk in ans_pks:
        t_canon = pk["table_name_canonical"]
        if t_canon:
            ans_table_pks.setdefault(t_canon, []).append(pk)
            
    stud_table_pks = {}
    for pk in stud_pks:
        t_canon = pk.get("table_name_canonical")
        phys_t = accepted_table_pairs.get(t_canon)
        if phys_t:
            stud_table_pks.setdefault(phys_t, []).append(pk)
            
    for t in ans_table_pks:
        ans_table_pks[t].sort(key=lambda x: int(x["key_ordinal"]))
    for t in stud_table_pks:
        stud_table_pks[t].sort(key=lambda x: int(x["key_ordinal"]))

    stud_mapped_cols = {}
    for cm in column_mappings:
        c_t = cm.get("answer_table")
        c_c = cm.get("answer_column")
        s_c = cm.get("student_column")
        if c_t and c_c and s_c:
            stud_mapped_cols[(c_t, c_c)] = s_c

    # Unique constraints dictionary
    stud_uniques_dict = {}
    if stud_uniques:
        for u in stud_uniques:
            t_canon = u.get("table_name_canonical")
            phys_t = accepted_table_pairs.get(t_canon)
            if phys_t:
                stud_uniques_dict.setdefault(phys_t, []).append(u)

    # 1. GRADE FOREIGN KEYS (used for PK detail validation)
    fk_grading_status = {}  # maps (ans_child_t, ans_parent_t) -> status
    
    # Track expected relationships
    expected_relations = set()
    for fk in ans_fks:
        expected_relations.add((fk["parent_table_canonical"], fk["referenced_table_canonical"]))

    for fk in ans_fks:
        ans_child_t = fk["parent_table_canonical"]
        ans_parent_t = fk["referenced_table_canonical"]
        ans_child_col = fk["parent_column_canonical"]
        ans_parent_col = fk["referenced_column_canonical"]
        
        stud_child_t = accepted_table_pairs.get(ans_child_t)
        stud_parent_t = accepted_table_pairs.get(ans_parent_t)
        
        fk_row = {
            "submission_id": fk.get("submission_id", "student"),
            "fk_name": "",
            "answer_child_table": ans_child_t,
            "answer_parent_table": ans_parent_t,
            "answer_child_columns": ans_child_col,
            "answer_parent_columns": ans_parent_col,
            "student_child_table": stud_child_t or "",
            "student_parent_table": stud_parent_t or "",
            "student_child_columns": "",
            "student_parent_columns": "",
            "fk_status": "FK_MISSING",
            "fk_reason": "",
            "fk_severity": "high"
        }

        if not stud_child_t or not stud_parent_t:
            fk_row["fk_status"] = "FK_MISSING"
            fk_row["fk_reason"] = f"Tables not mapped: child={ans_child_t}, parent={ans_parent_t}"
            fk_relationship_results.append(fk_row)
            fk_grading_status[(ans_child_t, ans_parent_t)] = "FK_MISSING"
            counts["fk_missing_count"] += 1
            continue

        # Look for matching student foreign keys
        matching_stud_fks = [
            sf for sf in stud_fks 
            if sf.get("parent_table_canonical") == ans_child_t and sf.get("referenced_table_canonical") == ans_parent_t
        ]

        if not matching_stud_fks:
            # Check for implied FK
            if suggests_relationship(stud_child_t, ans_parent_t, ans_parent_col, stud_cols, config):
                fk_row["fk_status"] = "FK_IMPLIED_REVIEW_REQUIRED"
                fk_row["fk_reason"] = f"Declared FK is missing, but child table '{stud_child_t}' has matching columns."
                fk_row["fk_severity"] = "warning"
                counts["fk_review_required_count"] += 1
            else:
                fk_row["fk_status"] = "FK_MISSING"
                fk_row["fk_reason"] = f"No declared FK or relationship columns between '{stud_child_t}' and '{stud_parent_t}'."
                counts["fk_missing_count"] += 1
            fk_relationship_results.append(fk_row)
            fk_grading_status[(ans_child_t, ans_parent_t)] = fk_row["fk_status"]
            continue

        # Grade first matching FK
        sfk = matching_stud_fks[0]
        fk_row["fk_name"] = sfk.get("fk_name", "")
        
        s_child_col = sfk.get("parent_column_canonical", "")
        s_parent_col = sfk.get("referenced_column_canonical", "")
        
        fk_row["student_child_columns"] = s_child_col
        fk_row["student_parent_columns"] = s_parent_col

        ans_child_raw = get_raw_col_name(ans_child_t, ans_child_col, ans_cols, False)
        ans_parent_raw = get_raw_col_name(ans_parent_t, ans_parent_col, ans_cols, False)
        stud_child_raw = get_raw_col_name(stud_child_t, s_child_col, stud_cols, True)
        stud_parent_raw = get_raw_col_name(stud_parent_t, s_parent_col, stud_cols, True)

        # Check exact and alias equivalent
        if s_child_col == ans_child_col and s_parent_col == ans_parent_col:
            if stud_child_raw.lower() == ans_child_raw.lower() and stud_parent_raw.lower() == ans_parent_raw.lower():
                fk_row["fk_status"] = "FK_MATCH_EXACT"
                fk_row["fk_reason"] = "Physical columns and relationship map exactly."
                fk_row["fk_severity"] = "info"
                counts["fk_exact_match_count"] += 1
            else:
                fk_row["fk_status"] = "FK_ALIAS_EQUIVALENT"
                fk_row["fk_reason"] = "Relationship matches via column aliases."
                fk_row["fk_severity"] = "info"
                counts["fk_alias_equivalent_count"] += 1
        else:
            # Check surrogate or natural accepted
            is_parent_identity = get_is_identity(stud_parent_t, stud_parent_raw, stud_cols)
            is_parent_surrogate = is_surrogate_column(stud_parent_raw, stud_parent_t, is_parent_identity, config)
            
            ans_parent_identity = 0
            for ac in ans_cols:
                if ac.get("table_name_canonical") == ans_parent_t and ac.get("column_name_canonical") == ans_parent_col:
                    ans_parent_identity = ac.get("is_identity", 0)
                    break
            ans_parent_surrogate = is_surrogate_column(ans_parent_raw, ans_parent_t, ans_parent_identity, config)

            if is_parent_surrogate and not ans_parent_surrogate:
                fk_row["fk_status"] = "FK_SURROGATE_ACCEPTED"
                fk_row["fk_reason"] = "Student references parent surrogate key instead of business key."
                fk_row["fk_severity"] = "info"
                counts["fk_surrogate_accepted_count"] += 1
            elif not is_parent_surrogate and ans_parent_surrogate:
                fk_row["fk_status"] = "FK_NATURAL_ACCEPTED"
                fk_row["fk_reason"] = "Student references parent natural key instead of surrogate key."
                fk_row["fk_severity"] = "info"
                counts["fk_natural_accepted_count"] += 1
            else:
                fk_row["fk_status"] = "FK_RELATIONSHIP_MATCH"
                fk_row["fk_reason"] = "Correct parent-child relationship declared."
                fk_row["fk_severity"] = "info"
                counts["fk_relationship_match_count"] += 1

        fk_relationship_results.append(fk_row)
        fk_grading_status[(ans_child_t, ans_parent_t)] = fk_row["fk_status"]

    # Check for wrong target FKs in student database
    for sfk in stud_fks:
        s_child_t = sfk.get("parent_table_canonical")
        s_parent_t = sfk.get("referenced_table_canonical")
        if not s_child_t or not s_parent_t or s_child_t == "AMBIGUOUS_TABLE" or s_parent_t == "AMBIGUOUS_TABLE":
            continue
        # If there's an expected relationship for child table, but not to this parent table
        child_expected_parents = {p for c, p in expected_relations if c == s_child_t}
        if child_expected_parents and s_parent_t not in child_expected_parents:
            # Report Wrong Target
            fk_row = {
                "submission_id": sfk.get("submission_id", "student"),
                "fk_name": sfk.get("fk_name", ""),
                "answer_child_table": s_child_t,
                "answer_parent_table": list(child_expected_parents)[0] if child_expected_parents else "",
                "answer_child_columns": "",
                "answer_parent_columns": "",
                "student_child_table": accepted_table_pairs.get(s_child_t) or s_child_t,
                "student_parent_table": accepted_table_pairs.get(s_parent_t) or s_parent_t,
                "student_child_columns": sfk.get("parent_column_canonical", ""),
                "student_parent_columns": sfk.get("referenced_column_canonical", ""),
                "fk_status": "FK_WRONG_TARGET",
                "fk_reason": f"Child table referenced wrong parent: expected {child_expected_parents}, got {s_parent_t}",
                "fk_severity": "high"
            }
            fk_relationship_results.append(fk_row)
            counts["fk_wrong_target_count"] += 1

    # 2. GRADE PRIMARY KEYS
    for canon_t, ans_pk_records in ans_table_pks.items():
        ans_pk_cols = [c["column_name_canonical"] for c in ans_pk_records]
        stud_t = accepted_table_pairs.get(canon_t)
        
        pk_row = {
            "submission_id": "student",
            "table_name": canon_t,
            "student_table": stud_t or "",
            "answer_pk_columns": ";".join(ans_pk_cols),
            "student_pk_columns": "",
            "answer_business_key_columns": ";".join(ans_pk_cols),
            "student_business_key_columns": "",
            "key_status": "PK_MISSING",
            "key_reason": "",
            "key_severity": "high"
        }

        if not stud_t:
            pk_row["key_status"] = "PK_MISSING"
            pk_row["key_reason"] = f"Table '{canon_t}' not found in student database."
            key_adequacy_results.append(pk_row)
            counts["pk_missing_count"] += 1
            continue

        stud_pk_records = stud_table_pks.get(stud_t, [])
        stud_pk_cols = [r["column_name_canonical"] for r in stud_pk_records]
        pk_row["student_pk_columns"] = ";".join(stud_pk_cols)

        # Resolve raw names
        ans_pk_raws = [get_raw_col_name(canon_t, c, ans_cols, False) for c in ans_pk_cols]
        stud_pk_raws = [get_raw_col_name(stud_t, c, stud_cols, True) for c in stud_pk_cols]

        if not stud_pk_cols:
            pk_row["key_status"] = "PK_MISSING"
            pk_row["key_reason"] = f"No primary key defined on table '{stud_t}'."
            key_adequacy_results.append(pk_row)
            counts["pk_missing_count"] += 1
            continue

        # Exact check
        if len(stud_pk_cols) == len(ans_pk_cols) and all(s.lower() == a.lower() for s, a in zip(stud_pk_raws, ans_pk_raws)):
            pk_row["key_status"] = "PK_MATCH_EXACT"
            pk_row["key_reason"] = "Primary key columns match exactly."
            pk_row["key_severity"] = "info"
            counts["pk_exact_match_count"] += 1
        elif len(stud_pk_cols) == len(ans_pk_cols) and all(s == a for s, a in zip(stud_pk_cols, ans_pk_cols)):
            pk_row["key_status"] = "PK_MATCH_ALIAS_EQUIVALENT"
            pk_row["key_reason"] = "Primary key columns match after alias expansion."
            pk_row["key_severity"] = "info"
            counts["pk_alias_equivalent_count"] += 1
        else:
            # Check surrogate accepted
            is_surrogate = len(stud_pk_cols) == 1 and all(
                is_surrogate_column(c, stud_t, get_is_identity(stud_t, c, stud_cols), config) 
                for c in stud_pk_raws
            )
            
            # Check if answer PK is surrogate
            ans_is_surrogate = len(ans_pk_cols) == 1 and all(
                is_surrogate_column(c, canon_t, 0, config)
                for c in ans_pk_raws
            )

            if is_surrogate:
                # 1. Business key columns presence & NOT NULL
                bus_cols_present = True
                bus_cols_not_null = True
                missing_bus_col = ""
                null_bus_col = ""
                
                for bc in ans_pk_cols:
                    bc_stud_phys = stud_mapped_cols.get((canon_t, bc))
                    if not bc_stud_phys:
                        bus_cols_present = False
                        missing_bus_col = bc
                        break
                    if get_is_nullable(stud_t, bc_stud_phys, stud_cols) == 1:
                        bus_cols_not_null = False
                        null_bus_col = bc_stud_phys
                        break
                
                if not bus_cols_present:
                    pk_row["key_status"] = "PK_REVIEW_REQUIRED"
                    pk_row["key_reason"] = f"Surrogate PK used, but business key column '{missing_bus_col}' is missing."
                    pk_row["key_severity"] = "high"
                    counts["pk_review_required_count"] += 1
                elif not bus_cols_not_null:
                    pk_row["key_status"] = "PK_REVIEW_REQUIRED"
                    pk_row["key_reason"] = f"Surrogate PK used, but business key column '{null_bus_col}' is nullable."
                    pk_row["key_severity"] = "high"
                    counts["pk_review_required_count"] += 1
                else:
                    # 2. Uniqueness check (if required)
                    uniqueness_verified = True
                    if config.schema.key_grading.require_business_key_uniqueness:
                        uniques_on_table = uniques_on_table = stud_uniques_dict.get(stud_t, [])
                        has_unique = False
                        idx_groups = {}
                        for u in uniques_on_table:
                            idx_groups.setdefault(u["constraint_name"], []).append(u)
                        
                        for idx_name, idx_cols in idx_groups.items():
                            idx_cols_canon = {u["column_name_canonical"] for u in idx_cols}
                            if idx_cols_canon == set(ans_pk_cols):
                                has_unique = True
                                break
                        
                        if not has_unique:
                            uniqueness_verified = False
                    
                    if not uniqueness_verified:
                        pk_row["key_status"] = "PK_REVIEW_REQUIRED"
                        pk_row["key_reason"] = "Surrogate PK used, but business key uniqueness cannot be verified (missing UNIQUE constraint/index)."
                        pk_row["key_severity"] = "warning"
                        counts["pk_review_required_count"] += 1
                    else:
                        # 3. Detail table parent relationship adequacy
                        relations_adequate = True
                        failed_relation = ""
                        for r_child, r_parent in expected_relations:
                            if r_child == canon_t:
                                r_status = fk_grading_status.get((r_child, r_parent), "FK_MISSING")
                                if r_status not in ["FK_MATCH_EXACT", "FK_ALIAS_EQUIVALENT", "FK_SURROGATE_ACCEPTED", "FK_NATURAL_ACCEPTED", "FK_RELATIONSHIP_MATCH"]:
                                    relations_adequate = False
                                    failed_relation = r_parent
                                    break
                        
                        if not relations_adequate:
                            pk_row["key_status"] = "PK_REVIEW_REQUIRED"
                            pk_row["key_reason"] = f"Surrogate PK used on detail table, but relationship to '{failed_relation}' is missing or invalid."
                            pk_row["key_severity"] = "high"
                            counts["pk_review_required_count"] += 1
                        else:
                            pk_row["key_status"] = "PK_SURROGATE_ACCEPTED"
                            pk_row["key_reason"] = "Surrogate primary key used and business key columns are valid and unique."
                            pk_row["key_severity"] = "info"
                            counts["pk_surrogate_accepted_count"] += 1
            elif not is_surrogate and ans_is_surrogate:
                pk_row["key_status"] = "PK_NATURAL_ACCEPTED"
                pk_row["key_reason"] = "Natural business key used while answer uses surrogate key."
                pk_row["key_severity"] = "info"
                counts["pk_natural_accepted_count"] += 1
            else:
                pk_row["key_status"] = "PK_REVIEW_REQUIRED"
                pk_row["key_reason"] = f"Primary key mismatch: expected {ans_pk_cols}, got {stud_pk_cols}."
                pk_row["key_severity"] = "warning"
                counts["pk_review_required_count"] += 1

        key_adequacy_results.append(pk_row)

    # 3. WRITE COMBINED RESULTS FOR STRUCTURE REPORT
    for pk in key_adequacy_results:
        status_to_report = "PASS"
        if pk["key_status"] in ["PK_MISSING", "PK_INVALID"]:
            status_to_report = "MISSING" if pk["key_status"] == "PK_MISSING" else "PK_MISMATCH"
        elif pk["key_status"] == "PK_REVIEW_REQUIRED":
            status_to_report = "PK_MISMATCH"
        else:
            status_to_report = "PASS"

        structure_results.append({
            "component": "pk",
            "answer_object": f"{pk['table_name']}.PK",
            "student_object": f"{pk['student_table']}.PK" if pk["student_table"] else "",
            "status": status_to_report,
            "severity": pk["key_severity"],
            "message": pk["key_reason"],
            "evidence": f"Adequacy Status: {pk['key_status']} (Expected: {pk['answer_pk_columns']}, Student: {pk['student_pk_columns']})"
        })

    for fk in fk_relationship_results:
        status_to_report = "PASS"
        if fk["fk_status"] == "FK_MISSING":
            status_to_report = "MISSING"
        elif fk["fk_status"] in ["FK_IMPLIED_REVIEW_REQUIRED", "FK_WRONG_TARGET"]:
            status_to_report = "RULE_MISMATCH"
        else:
            status_to_report = "PASS"

        ans_obj = f"FK: {fk['answer_child_table']}.{fk['answer_child_columns']} -> {fk['answer_parent_table']}.{fk['answer_parent_columns']}"
        stud_obj = ""
        if fk["student_child_table"]:
            stud_obj = f"FK: {fk['student_child_table']}.{fk['student_child_columns']} -> {fk['student_parent_table']}.{fk['student_parent_columns']}"

        structure_results.append({
            "component": "fk",
            "answer_object": ans_obj,
            "student_object": stud_obj,
            "status": status_to_report,
            "severity": fk["fk_severity"],
            "message": fk["fk_reason"],
            "evidence": f"Adequacy Status: {fk['fk_status']}"
        })

    return structure_results, key_adequacy_results, fk_relationship_results, counts
