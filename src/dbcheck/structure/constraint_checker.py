from typing import List, Dict, Any

def match_constraints(
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
    # Group PK columns by table
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

    # Sort columns by key_ordinal
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
            
            # Compare columns
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
    # Group FK relations. A database can have multiple FKs.
    # We will identify each FK by (parent_table, parent_col, ref_table, ref_col)
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
        # If there are duplicates in student (which is weird but possible), keep one
        stud_fk_set[key] = fk

    # Match expected FKs
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
            
            # Check cascade rules
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

    # Check for extra FKs in student database
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
