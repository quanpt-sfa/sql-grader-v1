from typing import List, Dict, Any

def match_tables(answer_tables: List[Dict[str, Any]], student_tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = []
    
    # Map by canonical name
    ans_by_canon = {t["table_name_canonical"]: t for t in answer_tables if t["table_name_canonical"]}
    stud_by_canon = {}
    
    for t in student_tables:
        canon = t["table_name_canonical"]
        if canon == "AMBIGUOUS_TABLE":
            results.append({
                "component": "table",
                "answer_object": "",
                "student_object": t["table_name"],
                "status": "MAPPING_AMBIGUOUS",
                "severity": "high",
                "message": f"Ambiguous table mapping discovered for student table '{t['table_name']}'",
                "evidence": f"Physical: {t['table_name']}"
            })
            continue
            
        if canon:
            # If there's multiple matching, collect them (we should report ambiguous, but normalizer already flags it)
            stud_by_canon[canon] = t

    # 1. Check for expected answer tables
    for canon, ans_t in ans_by_canon.items():
        if canon not in stud_by_canon:
            results.append({
                "component": "table",
                "answer_object": f"{ans_t['schema_name']}.{ans_t['table_name']}",
                "student_object": "",
                "status": "MISSING",
                "severity": "high",
                "message": f"Required table '{canon}' is missing in student submission",
                "evidence": f"Expected canonical table '{canon}'"
            })
        else:
            stud_t = stud_by_canon[canon]
            ans_phys = ans_t["table_name"]
            stud_phys = stud_t["table_name"]
            
            if ans_phys.lower() == stud_phys.lower():
                msg = "Table matched exactly"
                status = "PASS"
                severity = "info"
            else:
                msg = f"Table matched by alias ('{stud_phys}' -> '{ans_phys}')"
                status = "PASS"
                severity = "info"
                
            results.append({
                "component": "table",
                "answer_object": f"{ans_t['schema_name']}.{ans_t['table_name']}",
                "student_object": f"{stud_t['schema_name']}.{stud_t['table_name']}",
                "status": status,
                "severity": severity,
                "message": msg,
                "evidence": f"Answer: {ans_phys}, Student: {stud_phys}"
            })

    # 2. Check for extra student tables
    for canon, stud_t in stud_by_canon.items():
        if canon not in ans_by_canon:
            results.append({
                "component": "table",
                "answer_object": "",
                "student_object": f"{stud_t['schema_name']}.{stud_t['table_name']}",
                "status": "EXTRA",
                "severity": "low",
                "message": f"Extra table '{stud_t['table_name']}' found in student database",
                "evidence": f"Physical: {stud_t['table_name']} (mapped to '{canon}')"
            })
            
    return results
