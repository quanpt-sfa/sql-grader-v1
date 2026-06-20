import csv
from pathlib import Path
from typing import List, Dict, Any, Set
from dbcheck.snapshot.reader import read_full_snapshot
from dbcheck.snapshot.normalizer import NameNormalizer
from dbcheck.structure.constraint_checker import match_constraints
from dbcheck.structure.view_matcher import match_views_structure
from dbcheck.structure.type_compatibility import compare_sql_types
from dbcheck.utils.logging import get_logger

HEADERS = ["component", "answer_object", "student_object", "status", "severity", "message", "evidence"]

TABLE_MAP_HEADERS = [
    "answer_table", "raw_answer_table", "student_table", "raw_student_table",
    "normalized_student_table", "expanded_student_table",
    "match_status", "match_method", "match_score",
    "candidate_tables", "review_required", "suggested_alias_entry"
]

COLUMN_MAP_HEADERS = [
    "answer_table", "student_table", "answer_column", "student_column",
    "raw_student_column", "normalized_student_column", "expanded_student_column",
    "match_status", "match_method", "match_score",
    "answer_type", "student_type",
    "answer_type_group", "student_type_group",
    "type_status", "type_score", "type_reason",
    "role_guard_result", "review_required", "suggested_alias_entry"
]


def _build_excluded_set(tables_list: List[Dict[str, Any]], config: Any) -> Set[str]:
    """Return the set of raw table names that should be excluded from grading."""
    excluded = set()
    for t in tables_list:
        raw = t.get("table_name", "")
        canon = t.get("table_name_canonical", "")
        if config.schema.is_excluded(raw) or config.schema.is_excluded(canon):
            excluded.add(raw)
    return excluded


def run_structure_comparison(answer_dir: Path, student_dir: Path, output_report_path: Path, config: Any) -> Dict[str, int]:
    logger = get_logger()

    # 1. Read snapshots
    ans_snap = read_full_snapshot(answer_dir)
    stud_snap = read_full_snapshot(student_dir)

    # 2. Initialize NameNormalizer
    normalizer = NameNormalizer(config)

    # -----------------------------------------------------------------------
    # Apply exclusion to answer and student tables
    # -----------------------------------------------------------------------
    ans_excluded_raw = _build_excluded_set(ans_snap["tables"], config)
    stu_excluded_raw = _build_excluded_set(stud_snap["tables"], config)

    ans_tables_active = [t for t in ans_snap["tables"] if t["table_name"] not in ans_excluded_raw]
    stu_tables_active = [t for t in stud_snap["tables"] if t["table_name"] not in stu_excluded_raw]

    # Filter dependent snapshot collections
    ans_cols_active = [c for c in ans_snap["columns"] if c.get("table_name") not in ans_excluded_raw]
    stu_cols_active = [c for c in stud_snap["columns"] if c.get("table_name") not in stu_excluded_raw]
    ans_pks_active  = [k for k in ans_snap["primary_keys"] if k.get("table_name") not in ans_excluded_raw]
    stu_pks_active  = [k for k in stud_snap["primary_keys"] if k.get("table_name") not in stu_excluded_raw]
    ans_fks_active  = [k for k in ans_snap["foreign_keys"] if k.get("table_name") not in ans_excluded_raw]
    stu_fks_active  = [k for k in stud_snap["foreign_keys"] if k.get("table_name") not in stu_excluded_raw]

    # -----------------------------------------------------------------------
    # Fix A: build the required answer table set by canonical name only.
    #
    # The answer snapshot may contain BOTH raw and canonical names when the
    # answer DB was processed through the normalizer (e.g. it stored
    # "HangHoa" as table_name and "Hang" as table_name_canonical).
    # We must deduplicate by canonical so that raw aliases of the same
    # concept are not counted as separate required tables.
    # -----------------------------------------------------------------------
    # Mapping: canonical_name -> raw_name (first occurrence wins)
    required_answer_canons: Dict[str, str] = {}   # canon -> raw
    for t in ans_tables_active:
        raw = t["table_name"]
        canon = t.get("table_name_canonical") or raw
        if canon not in required_answer_canons:
            required_answer_canons[canon] = raw

    # -----------------------------------------------------------------------
    # 3. Phase 1 & 2: Map each active student table to a canonical answer table
    # -----------------------------------------------------------------------
    table_mappings: List[Dict[str, Any]] = []
    accepted_statuses = {
        "TABLE_MATCHED_EXACT", "TABLE_MATCHED_ALIAS",
        "TABLE_MATCHED_ABBREVIATION", "TABLE_MATCHED_FUZZY_HIGH"
    }

    for s_tab in stu_tables_active:
        raw_table = s_tab["table_name"]
        map_res = normalizer.map_table(raw_table)
        # Enrich with a raw_answer_table field (the raw name of the matched answer table)
        canon = map_res.get("answer_table", "")
        map_res["raw_answer_table"] = required_answer_canons.get(canon, canon)
        table_mappings.append(map_res)

    # One-to-One: detect competing student tables for the same canonical answer table
    canon_counts: Dict[str, int] = {}
    for m in table_mappings:
        if m["match_status"] in accepted_statuses:
            canon = m["answer_table"]
            canon_counts[canon] = canon_counts.get(canon, 0) + 1

    for m in table_mappings:
        if m["match_status"] in accepted_statuses:
            canon = m["answer_table"]
            if canon_counts[canon] > 1:
                m["match_status"] = "TABLE_AMBIGUOUS"
                m["match_method"] = "multiple_matches"
                m["review_required"] = True
                m["suggested_alias_entry"] = f"# Competing match for {canon}"
                m["answer_table"] = ""
                m["raw_answer_table"] = ""

    # Gather mapped canonicals (accepted)
    mapped_answer_canons: Set[str] = {
        m["answer_table"] for m in table_mappings if m["answer_table"]
    }
    all_answer_canons_mapped = set(required_answer_canons.keys()).issubset(mapped_answer_canons)

    # Unmapped → Extra if all answer tables are already covered
    for m in table_mappings:
        if m["match_status"] == "TABLE_UNMAPPED":
            if all_answer_canons_mapped:
                m["match_status"] = "TABLE_EXTRA_STUDENT"
                m["review_required"] = False

    # Missing answer tables: iterate by CANONICAL (deduplicated) required set
    for canon, raw in required_answer_canons.items():
        if canon not in mapped_answer_canons:
            table_mappings.append({
                "answer_table": canon,
                "raw_answer_table": raw,
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

    # Ensure every row has raw_answer_table key
    for m in table_mappings:
        if "raw_answer_table" not in m:
            canon = m.get("answer_table", "")
            m["raw_answer_table"] = required_answer_canons.get(canon, canon)

    # Save table mapping report
    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    table_report_path = output_report_path.parent / "table_mapping_report.csv"
    with open(table_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TABLE_MAP_HEADERS)
        writer.writeheader()
        for row in table_mappings:
            # Write only known headers; ignore extra keys
            out = {k: row.get(k, "") for k in TABLE_MAP_HEADERS}
            writer.writerow(out)

    # Gather accepted table pairs: canonical -> physical_student
    accepted_pairs: Dict[str, str] = {}
    for m in table_mappings:
        if m["match_status"] in accepted_statuses:
            accepted_pairs[m["answer_table"]] = m["student_table"]

    # -----------------------------------------------------------------------
    # 4. Phase 3: Column Normalization inside mapped pairs
    # -----------------------------------------------------------------------
    column_mappings: List[Dict[str, Any]] = []

    for canon_t, phys_t in accepted_pairs.items():
        # Use active (non-excluded) column snapshots
        expected_cols = [c for c in ans_cols_active if c.get("table_name_canonical") == canon_t]
        student_cols  = [c for c in stu_cols_active if c.get("table_name") == phys_t]

        table_col_mappings: List[Dict[str, Any]] = []
        for s_col in student_cols:
            raw_col = s_col["column_name"]
            col_res = normalizer.map_column(raw_col, canon_t, phys_t, expected_cols)
            col_res["student_type"] = s_col.get("data_type", "")
            ans_col_meta = next(
                (c for c in expected_cols if c["column_name"] == col_res.get("answer_column")),
                None
            )
            col_res["answer_type"] = ans_col_meta["data_type"] if ans_col_meta else ""
            table_col_mappings.append(col_res)

        # One-to-One column constraint
        col_counts: Dict[str, int] = {}
        for cm in table_col_mappings:
            if cm["match_status"].startswith("COLUMN_MATCHED"):
                canon_c = cm["answer_column"]
                col_counts[canon_c] = col_counts.get(canon_c, 0) + 1

        for cm in table_col_mappings:
            if cm["match_status"].startswith("COLUMN_MATCHED"):
                canon_c = cm["answer_column"]
                if col_counts[canon_c] > 1:
                    cm["match_status"] = "COLUMN_AMBIGUOUS"
                    cm["match_method"] = "multiple_matches"
                    cm["review_required"] = True
                    cm["answer_column"] = ""

        # Type comparison using type_compatibility module
        for cm in table_col_mappings:
            if cm["match_status"].startswith("COLUMN_MATCHED"):
                type_result = compare_sql_types(
                    cm["answer_type"], cm["student_type"], config
                )
                cm["answer_type_group"]   = type_result["answer_type_group"]
                cm["student_type_group"]  = type_result["student_type_group"]
                cm["type_status"]         = type_result["type_status"]
                cm["type_score"]          = type_result["type_score"]
                cm["type_reason"]         = type_result["reason"]

                # Hard mismatch → demote the column match status
                if type_result["type_status"] == "TYPE_MISMATCH":
                    cm["match_status"] = "COLUMN_TYPE_MISMATCH"
                    cm["review_required"] = True
            else:
                cm.setdefault("answer_type_group", "")
                cm.setdefault("student_type_group", "")
                cm.setdefault("type_status", "")
                cm.setdefault("type_score", 0.0)
                cm.setdefault("type_reason", "")

        # Unmapped → Extra if all expected columns are covered
        mapped_cols_in_table: Set[str] = {
            cm["answer_column"] for cm in table_col_mappings if cm.get("answer_column")
        }
        all_expected_cols = {c["column_name"] for c in expected_cols}
        all_expected_mapped = all_expected_cols.issubset(mapped_cols_in_table)

        for cm in table_col_mappings:
            if cm["match_status"] == "COLUMN_UNMAPPED":
                if all_expected_mapped:
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
                    "answer_type": col_meta.get("data_type", ""),
                    "student_type": "",
                    "answer_type_group": "",
                    "student_type_group": "",
                    "type_status": "",
                    "type_score": 0.0,
                    "type_reason": "",
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
            out = {k: row.get(k, "") for k in COLUMN_MAP_HEADERS}
            writer.writerow(out)

    # -----------------------------------------------------------------------
    # 5. Build structure_report.csv
    # -----------------------------------------------------------------------
    structure_results: List[Dict[str, Any]] = []

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
                "evidence": f"raw_answer={m.get('raw_answer_table', '')}"
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

    for cm in column_mappings:
        status = cm["match_status"]
        ans_obj  = f"{cm['answer_table']}.{cm.get('answer_column','')}" if cm.get("answer_column") else ""
        stud_obj = f"{cm['student_table']}.{cm.get('student_column','')}" if cm.get("student_column") else ""
        type_ev  = f"ans_group={cm.get('answer_type_group','')}, stu_group={cm.get('student_type_group','')}, score={cm.get('type_score','')}"

        if status.startswith("COLUMN_MATCHED"):
            structure_results.append({
                "component": "column",
                "answer_object": ans_obj,
                "student_object": stud_obj,
                "status": "PASS",
                "severity": "info",
                "message": f"Column matched by {cm['match_method']}",
                "evidence": f"Mapped '{cm.get('student_column','')}' to '{cm.get('answer_column','')}'"
            })
        elif status == "COLUMN_MISSING_ANSWER":
            structure_results.append({
                "component": "column",
                "answer_object": ans_obj,
                "student_object": "",
                "status": "MISSING",
                "severity": "high",
                "message": f"Required column '{cm.get('answer_column','')}' is missing in table '{cm['answer_table']}'",
                "evidence": ""
            })
        elif status == "COLUMN_EXTRA_STUDENT":
            structure_results.append({
                "component": "column",
                "answer_object": "",
                "student_object": stud_obj,
                "status": "EXTRA",
                "severity": "low",
                "message": f"Extra column '{cm.get('student_column','')}' in table '{cm['student_table']}'",
                "evidence": ""
            })
        elif status == "COLUMN_AMBIGUOUS":
            structure_results.append({
                "component": "column",
                "answer_object": "",
                "student_object": stud_obj,
                "status": "MAPPING_AMBIGUOUS",
                "severity": "high",
                "message": f"Ambiguous column mapping for '{cm.get('student_column','')}' in table '{cm['student_table']}'",
                "evidence": ""
            })
        elif status == "COLUMN_UNMAPPED":
            structure_results.append({
                "component": "column",
                "answer_object": "",
                "student_object": stud_obj,
                "status": "MISSING",
                "severity": "high",
                "message": f"Unmapped column '{cm.get('student_column','')}' in table '{cm['student_table']}'",
                "evidence": ""
            })
        elif status == "COLUMN_INCOMPATIBLE_ROLE":
            structure_results.append({
                "component": "column",
                "answer_object": ans_obj,
                "student_object": stud_obj,
                "status": "INCOMPATIBLE_ROLE",
                "severity": "high",
                "message": f"Column role guard block: {cm.get('role_guard_result','')}",
                "evidence": f"Answer: {cm.get('answer_column','')}, Student: {cm.get('student_column','')}"
            })
        elif status == "COLUMN_TYPE_MISMATCH":
            structure_results.append({
                "component": "column",
                "answer_object": ans_obj,
                "student_object": stud_obj,
                "status": "TYPE_MISMATCH",
                "severity": "high",
                "message": (
                    f"Type mismatch for '{cm.get('answer_column','')}': "
                    f"expected '{cm.get('answer_type','')}' ({cm.get('answer_type_group','')}), "
                    f"got '{cm.get('student_type','')}' ({cm.get('student_type_group','')})"
                ),
                "evidence": cm.get("type_reason", "")
            })



    # View structure matching
    view_results = match_views_structure(
        ans_snap["views"], stud_snap["views"],
        ans_snap["view_columns"], stud_snap["view_columns"]
    )
    structure_results.extend(view_results)

    # Constraint matching (only for accepted tables)
    accepted_table_set = set(accepted_pairs.keys())
    constraint_results = match_constraints(
        ans_pks_active, stu_pks_active,
        ans_fks_active, stu_fks_active,
        accepted_table_set
    )
    structure_results.extend(constraint_results)

    # Write structure_report.csv
    with open(output_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for row in structure_results:
            writer.writerow(row)

    # Calculate summary metrics
    status_counts: Dict[str, int] = {}
    for r in structure_results:
        s = r["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    logger.info(f"Structure comparison completed. Reports saved under: {output_report_path.parent}")
    logger.info(f"Results summary: {status_counts}")

    return status_counts
