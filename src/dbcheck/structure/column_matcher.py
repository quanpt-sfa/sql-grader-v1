from typing import List, Dict, Any

def match_columns(answer_columns: List[Dict[str, Any]], student_columns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = []

    # Group columns by (table_canonical, column_canonical)
    ans_map = {}
    for col in answer_columns:
        t_canon = col["table_name_canonical"]
        c_canon = col["column_name_canonical"]
        if t_canon and c_canon:
            ans_map[(t_canon, c_canon)] = col

    stud_map = {}
    for col in student_columns:
        t_canon = col["table_name_canonical"]
        c_canon = col["column_name_canonical"]
        
        if c_canon == "AMBIGUOUS_COLUMN":
            results.append({
                "component": "column",
                "answer_object": "",
                "student_object": f"{col['table_name']}.{col['column_name']}",
                "status": "MAPPING_AMBIGUOUS",
                "severity": "high",
                "message": f"Ambiguous column mapping discovered for '{col['table_name']}.{col['column_name']}'",
                "evidence": f"Physical: {col['column_name']}"
            })
            continue

        if t_canon and c_canon:
            # Save by canonical coords
            stud_map[(t_canon, c_canon)] = col

    # 1. Match expected answer columns
    for (t_canon, c_canon), ans_col in ans_map.items():
        if (t_canon, c_canon) not in stud_map:
            results.append({
                "component": "column",
                "answer_object": f"{ans_col['table_name']}.{ans_col['column_name']}",
                "student_object": "",
                "status": "MISSING",
                "severity": "high",
                "message": f"Required column '{c_canon}' is missing in table '{t_canon}'",
                "evidence": f"Expected canonical column '{t_canon}.{c_canon}'"
            })
        else:
            stud_col = stud_map[(t_canon, c_canon)]
            ans_phys = ans_col["column_name"]
            stud_phys = stud_col["column_name"]
            
            # Check for type mismatch
            ans_type = ans_col["data_type"].lower()
            stud_type = stud_col["data_type"].lower()
            
            # Simple compatibility helper
            is_compatible = (ans_type == stud_type)
            # Standard string type compatibilities
            if not is_compatible:
                char_types = {"varchar", "nvarchar", "char", "nchar"}
                if ans_type in char_types and stud_type in char_types:
                    is_compatible = True
                # Standard numeric compatibilities
                elif {"int", "bigint", "smallint", "tinyint"}.issubset({ans_type, stud_type}):
                    is_compatible = True
                elif {"decimal", "numeric", "money", "smallmoney"}.issubset({ans_type, stud_type}):
                    is_compatible = True
                    
            if not is_compatible:
                results.append({
                    "component": "column",
                    "answer_object": f"{ans_col['table_name']}.{ans_phys}",
                    "student_object": f"{stud_col['table_name']}.{stud_phys}",
                    "status": "TYPE_MISMATCH",
                    "severity": "high",
                    "message": f"Data type mismatch for column '{c_canon}'. Expected '{ans_type}', got '{stud_type}'",
                    "evidence": f"Answer: {ans_type}, Student: {stud_type}"
                })
            else:
                # Nullability check (warning level)
                null_msg = ""
                if ans_col["is_nullable"] != stud_col["is_nullable"]:
                    null_msg = f" (Nullability warning: answer is_nullable={ans_col['is_nullable']}, student is_nullable={stud_col['is_nullable']})"
                
                msg = f"Column matched by alias" if ans_phys.lower() != stud_phys.lower() else "Column matched exactly"
                results.append({
                    "component": "column",
                    "answer_object": f"{ans_col['table_name']}.{ans_phys}",
                    "student_object": f"{stud_col['table_name']}.{stud_phys}",
                    "status": "PASS",
                    "severity": "info",
                    "message": msg + null_msg,
                    "evidence": f"Mapped '{stud_phys}' to '{ans_phys}'"
                })

    # 2. Check for extra columns in mapped student tables
    for (t_canon, c_canon), stud_col in stud_map.items():
        # Check if the table is an answer table, but column is extra
        has_table = any(col["table_name_canonical"] == t_canon for col in answer_columns)
        if has_table and (t_canon, c_canon) not in ans_map:
            results.append({
                "component": "column",
                "answer_object": "",
                "student_object": f"{stud_col['table_name']}.{stud_col['column_name']}",
                "status": "EXTRA",
                "severity": "low",
                "message": f"Extra column '{stud_col['column_name']}' found in student table '{stud_col['table_name']}'",
                "evidence": f"Physical: {stud_col['column_name']} (canonical: {c_canon})"
            })

    return results
