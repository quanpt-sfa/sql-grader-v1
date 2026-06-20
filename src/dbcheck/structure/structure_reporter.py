import csv
from pathlib import Path
from typing import List, Dict, Any
from dbcheck.snapshot.reader import read_full_snapshot
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.structure.constraint_checker import match_constraints
from dbcheck.structure.view_matcher import match_views_structure
from dbcheck.utils.logging import get_logger

HEADERS = ["component", "answer_object", "student_object", "status", "severity", "message", "evidence"]

TABLE_MAP_HEADERS = [
    "answer_table", "student_table", "raw_student_table", "normalized_student_table",
    "expanded_student_table", "match_status", "match_method", "match_score",
    "candidate_tables", "review_required", "suggested_alias_entry"
]

COLUMN_MAP_HEADERS = [
    "answer_table", "student_table", "answer_column", "student_column",
    "raw_student_column", "normalized_student_column", "expanded_student_column",
    "match_status", "match_method", "match_score", "answer_type", "student_type",
    "role_guard_result", "review_required", "suggested_alias_entry"
]

def run_structure_comparison(answer_dir: Path, student_dir: Path, output_report_path: Path, config: Any) -> Dict[str, int]:
    logger = get_logger()
    
    # 1. Read snapshots
    ans_snap = read_full_snapshot(answer_dir)
    stud_snap = read_full_snapshot(student_dir)
    
    # 2. Initialize NameNormalizer
    normalizer = NameNormalizer(config)
    
    # 3. Perform Phase 1 & 2: Table Normalization and Gate
    table_mappings = []
    for s_tab in stud_snap["tables"]:
        raw_table = s_tab["table_name"]
        map_res = normalizer.map_table(raw_table)
        table_mappings.append(map_res)
        
    accepted_statuses = {"TABLE_MATCHED_EXACT", "TABLE_MATCHED_ALIAS", "TABLE_MATCHED_ABBREVIATION", "TABLE_MATCHED_FUZZY_HIGH"}
    
    # One-to-One: Count targets
    canon_counts = {}
    for m in table_mappings:
        if m["match_status"] in accepted_statuses:
            canon = m["answer_table"]
            canon_counts[canon] = canon_counts.get(canon, 0) + 1
            
    # Ambiguity conversion for competing tables
    for m in table_mappings:
        if m["match_status"] in accepted_statuses:
            canon = m["answer_table"]
            if canon_counts[canon] > 1:
                m["match_status"] = "TABLE_AMBIGUOUS"
                m["match_method"] = "multiple_matches"
                m["review_required"] = True
                m["suggested_alias_entry"] = f"# Competing match for {canon}"
                m["answer_table"] = ""
                
    # Gather mapped canonicals
    mapped_answer_tables = {m["answer_table"] for m in table_mappings if m["answer_table"]}
    all_answer_tables = {t["table_name"] for t in ans_snap["tables"]}
    all_answer_mapped = all_answer_tables.issubset(mapped_answer_tables)
    
    # Unmapped vs. Extra Student Table
    for m in table_mappings:
        if m["match_status"] == "TABLE_UNMAPPED":
            if all_answer_mapped:
                m["match_status"] = "TABLE_EXTRA_STUDENT"
                m["review_required"] = False
                
    # Check for Missing Answer Tables
    for ans_t in ans_snap["tables"]:
        canon = ans_t["table_name"]
        if canon not in mapped_answer_tables:
            table_mappings.append({
                "answer_table": canon,
                "student_table": "",
                "raw_student_table": "",
                "normalized_student_table": "",
                "expanded_student_table": "",
                "match_status": "TABLE_MISSING_ANSWER",
                "match_method": "",
                "match_score": 0.0,
                "candidate_tables": "",
                "review_required": True,
                "suggested_alias_entry": f"{canon}: [student_table_name]"
            })
            
    # Save table mapping report
    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    table_report_path = output_report_path.parent / "table_mapping_report.csv"
    with open(table_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TABLE_MAP_HEADERS)
        writer.writeheader()
        for row in table_mappings:
            writer.writerow(row)
            
    # Gather accepted table pairs
    accepted_pairs = {}
    for m in table_mappings:
        if m["match_status"] in accepted_statuses:
            accepted_pairs[m["answer_table"]] = m["student_table"]
            
    # 4. Perform Phase 3: Column Normalization inside mapped pairs
    column_mappings = []
    for canon_t, phys_t in accepted_pairs.items():
        expected_cols = [c for c in ans_snap["columns"] if c["table_name_canonical"] == canon_t]
        student_cols = [c for c in stud_snap["columns"] if c["table_name"] == phys_t]
        
        table_col_mappings = []
        for s_col in student_cols:
            raw_col = s_col["column_name"]
            col_res = normalizer.map_column(raw_col, canon_t, phys_t, expected_cols)
            col_res["student_type"] = s_col["data_type"]
            ans_col_meta = next((c for c in expected_cols if c["column_name"] == col_res["answer_column"]), None)
            col_res["answer_type"] = ans_col_meta["data_type"] if ans_col_meta else ""
            table_col_mappings.append(col_res)
            
        # One-to-One: column mapping counts
        col_counts = {}
        for cm in table_col_mappings:
            if cm["match_status"].startswith("COLUMN_MATCHED"):
                canon_c = cm["answer_column"]
                col_counts[canon_c] = col_counts.get(canon_c, 0) + 1
                
        # Competing columns ambiguity
        for cm in table_col_mappings:
            if cm["match_status"].startswith("COLUMN_MATCHED"):
                canon_c = cm["answer_column"]
                if col_counts[canon_c] > 1:
                    cm["match_status"] = "COLUMN_AMBIGUOUS"
                    cm["match_method"] = "multiple_matches"
                    cm["review_required"] = True
                    cm["answer_column"] = ""
                    
        # Column Type Mismatch
        for cm in table_col_mappings:
            if cm["match_status"].startswith("COLUMN_MATCHED"):
                ans_t_l = cm["answer_type"].lower()
                stud_t_l = cm["student_type"].lower()
                is_compatible = (ans_t_l == stud_t_l)
                char_types = {"varchar", "nvarchar", "char", "nchar"}
                if not is_compatible:
                    if ans_t_l in char_types and stud_t_l in char_types:
                        is_compatible = True
                    elif {"int", "bigint", "smallint", "tinyint"}.issubset({ans_t_l, stud_t_l}):
                        is_compatible = True
                    elif {"decimal", "numeric", "money", "smallmoney"}.issubset({ans_t_l, stud_t_l}):
                        is_compatible = True
                if not is_compatible:
                    cm["match_status"] = "COLUMN_TYPE_MISMATCH"
                    cm["review_required"] = True
                    
        # Unmapped vs. Extra Student Column
        mapped_cols_in_table = {cm["answer_column"] for cm in table_col_mappings if cm["answer_column"]}
        all_expected_cols_in_table = {c["column_name"] for c in expected_cols}
        all_expected_cols_mapped = all_expected_cols_in_table.issubset(mapped_cols_in_table)
        
        for cm in table_col_mappings:
            if cm["match_status"] == "COLUMN_UNMAPPED":
                if all_expected_cols_mapped:
                    cm["match_status"] = "COLUMN_EXTRA_STUDENT"
                    cm["review_required"] = False
                    
        # Missing columns
        for col_meta in expected_cols:
            canon_c = col_meta["column_name"]
            if canon_c not in mapped_cols_in_table:
                table_col_mappings.append({
                    "answer_table": canon_t,
                    "student_table": phys_t,
                    "answer_column": canon_c,
                    "student_column": "",
                    "raw_student_column": "",
                    "normalized_student_column": "",
                    "expanded_student_column": "",
                    "match_status": "COLUMN_MISSING_ANSWER",
                    "match_method": "",
                    "match_score": 0.0,
                    "answer_type": col_meta["data_type"],
                    "student_type": "",
                    "role_guard_result": "",
                    "review_required": True,
                    "suggested_alias_entry": f"by_table:\n  {canon_t}:\n    {canon_c}: [{canon_c}]"
                })
                
        column_mappings.extend(table_col_mappings)
        
    # Save column mapping report
    column_report_path = output_report_path.parent / "column_mapping_report.csv"
    with open(column_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMN_MAP_HEADERS)
        writer.writeheader()
        for row in column_mappings:
            writer.writerow(row)
            
    # 5. Populate structure_report.csv
    structure_results = []
    
    # Add tables results
    for m in table_mappings:
        status = m["match_status"]
        if status in accepted_statuses:
            structure_results.append({
                "component": "table",
                "answer_object": m["answer_table"],
                "student_object": m["student_table"],
                "status": "PASS",
                "severity": "info",
                "message": f"Table matched by {m['match_method']}",
                "evidence": f"Score: {m['match_score']}"
            })
        elif status == "TABLE_MISSING_ANSWER":
            structure_results.append({
                "component": "table",
                "answer_object": m["answer_table"],
                "student_object": "",
                "status": "MISSING",
                "severity": "high",
                "message": f"Required table '{m['answer_table']}' is missing",
                "evidence": ""
            })
        elif status == "TABLE_AMBIGUOUS":
            structure_results.append({
                "component": "table",
                "answer_object": "",
                "student_object": m["student_table"],
                "status": "MAPPING_AMBIGUOUS",
                "severity": "high",
                "message": f"Ambiguous table mapping for '{m['student_table']}'",
                "evidence": f"Candidates: {m['candidate_tables']}"
            })
        elif status == "TABLE_EXTRA_STUDENT":
            structure_results.append({
                "component": "table",
                "answer_object": "",
                "student_object": m["student_table"],
                "status": "EXTRA",
                "severity": "low",
                "message": f"Extra table '{m['student_table']}' found",
                "evidence": ""
            })
        elif status == "TABLE_UNMAPPED":
            structure_results.append({
                "component": "table",
                "answer_object": "",
                "student_object": m["student_table"],
                "status": "MISSING",
                "severity": "high",
                "message": f"Unmapped table '{m['student_table']}'",
                "evidence": ""
            })

    # Add columns results
    for cm in column_mappings:
        status = cm["match_status"]
        ans_obj = f"{cm['answer_table']}.{cm['answer_column']}" if cm["answer_column"] else ""
        stud_obj = f"{cm['student_table']}.{cm['student_column']}" if cm["student_column"] else ""
        
        if status.startswith("COLUMN_MATCHED"):
            structure_results.append({
                "component": "column",
                "answer_object": ans_obj,
                "student_object": stud_obj,
                "status": "PASS",
                "severity": "info",
                "message": f"Column matched by {cm['match_method']}",
                "evidence": f"Mapped '{cm['student_column']}' to '{cm['answer_column']}'"
            })
        elif status == "COLUMN_MISSING_ANSWER":
            structure_results.append({
                "component": "column",
                "answer_object": ans_obj,
                "student_object": "",
                "status": "MISSING",
                "severity": "high",
                "message": f"Required column '{cm['answer_column']}' is missing in table '{cm['answer_table']}'",
                "evidence": ""
            })
        elif status == "COLUMN_EXTRA_STUDENT":
            structure_results.append({
                "component": "column",
                "answer_object": "",
                "student_object": stud_obj,
                "status": "EXTRA",
                "severity": "low",
                "message": f"Extra column '{cm['student_column']}' found in table '{cm['student_table']}'",
                "evidence": ""
            })
        elif status == "COLUMN_AMBIGUOUS":
            structure_results.append({
                "component": "column",
                "answer_object": "",
                "student_object": stud_obj,
                "status": "MAPPING_AMBIGUOUS",
                "severity": "high",
                "message": f"Ambiguous column mapping for '{cm['student_column']}' in table '{cm['student_table']}'",
                "evidence": ""
            })
        elif status == "COLUMN_UNMAPPED":
            structure_results.append({
                "component": "column",
                "answer_object": "",
                "student_object": stud_obj,
                "status": "MISSING",
                "severity": "high",
                "message": f"Unmapped column '{cm['student_column']}' in table '{cm['student_table']}'",
                "evidence": ""
            })
        elif status == "COLUMN_INCOMPATIBLE_ROLE":
            structure_results.append({
                "component": "column",
                "answer_object": ans_obj,
                "student_object": stud_obj,
                "status": "INCOMPATIBLE_ROLE",
                "severity": "high",
                "message": f"Column role guard block: {cm['role_guard_result']}",
                "evidence": f"Answer: {cm['answer_column']}, Student: {cm['student_column']}"
            })
        elif status == "COLUMN_TYPE_MISMATCH":
            structure_results.append({
                "component": "column",
                "answer_object": ans_obj,
                "student_object": stud_obj,
                "status": "TYPE_MISMATCH",
                "severity": "high",
                "message": f"Data type mismatch for '{cm['answer_column']}'. Expected '{cm['answer_type']}', got '{cm['student_type']}'",
                "evidence": f"Answer: {cm['answer_type']}, Student: {cm['student_type']}"
            })

    # Call view structures matching
    view_results = match_views_structure(ans_snap["views"], stud_snap["views"], ans_snap["view_columns"], stud_snap["view_columns"])
    structure_results.extend(view_results)
    
    # Call constraints matching restricted only to accepted tables
    accepted_table_set = set(accepted_pairs.keys())
    constraint_results = match_constraints(
        ans_snap["primary_keys"], stud_snap["primary_keys"],
        ans_snap["foreign_keys"], stud_snap["foreign_keys"],
        accepted_table_set
    )
    structure_results.extend(constraint_results)
    
    # Write report CSV
    with open(output_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in structure_results:
            writer.writerow(row)
            
    # Calculate summary metrics
    status_counts = {}
    for r in structure_results:
        status = r["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
        
    logger.info(f"Structure comparison completed. Mappings and structural reports saved under: {output_report_path.parent}")
    logger.info(f"Results summary: {status_counts}")
    
    return status_counts
