from typing import List, Dict, Any

def match_views_structure(
    ans_views: List[Dict[str, Any]], stud_views: List[Dict[str, Any]],
    ans_view_cols: List[Dict[str, Any]], stud_view_cols: List[Dict[str, Any]],
    config: Any = None
) -> List[Dict[str, Any]]:
    results = []

    # Resolve expected views list based on mode
    views_mode = getattr(config, "views_mode", "answer_snapshot") if config else "answer_snapshot"
    
    if views_mode == "explicit_config" and config and config.views:
        expected_ans_views = []
        expected_ans_view_cols = []
        for vc in config.views:
            ans_v = next((v for v in ans_views if (v.get("view_name_canonical") or v["view_name"]).lower() == vc.answer_view.lower()), None)
            ans_phys_name = ans_v["view_name"] if ans_v else vc.answer_view
            expected_ans_views.append({
                "view_name": ans_phys_name,
                "view_name_canonical": vc.answer_view,
                "execution_status": ans_v["execution_status"] if ans_v else "OK"
            })
            for col_idx, col in enumerate(vc.columns):
                expected_ans_view_cols.append({
                    "view_name_canonical": vc.answer_view,
                    "ordinal_position": col_idx + 1,
                    "column_name": col["canonical"],
                    "column_name_canonical": col["canonical"],
                    "data_type": col.get("type", "text")
                })
    else:
        # Default: answer_snapshot mode
        expected_ans_views = ans_views
        expected_ans_view_cols = ans_view_cols

    # Map views by canonical name
    ans_view_map = {v["view_name_canonical"]: v for v in expected_ans_views if v.get("view_name_canonical")}
    stud_view_map = {v["view_name_canonical"]: v for v in stud_views if v.get("view_name_canonical")}

    # Group view columns by view canonical name
    ans_cols_by_view = {}
    for col in expected_ans_view_cols:
        v_canon = col.get("view_name_canonical")
        if v_canon:
            ans_cols_by_view.setdefault(v_canon, []).append(col)

    stud_cols_by_view = {}

    for col in stud_view_cols:
        v_canon = col["view_name_canonical"]
        if v_canon:
            stud_cols_by_view.setdefault(v_canon, []).append(col)

    # 1. Match expected answer views
    for canon, ans_v in ans_view_map.items():
        if canon not in stud_view_map:
            results.append({
                "component": "view",
                "answer_object": ans_v["view_name"],
                "student_object": "",
                "status": "MISSING",
                "severity": "high",
                "message": f"Required view '{canon}' is missing in student submission",
                "evidence": f"Expected canonical view '{canon}'"
            })
        else:
            stud_v = stud_view_map[canon]
            ans_phys = ans_v["view_name"]
            stud_phys = stud_v["view_name"]

            # Check compilation/execution status
            if stud_v["execution_status"] == "ERROR":
                results.append({
                    "component": "view",
                    "answer_object": ans_phys,
                    "student_object": stud_phys,
                    "status": "VIEW_EXECUTION_ERROR",
                    "severity": "high",
                    "message": f"Student view '{stud_phys}' failed execution test (has compilation or runtime errors)",
                    "evidence": f"execution_status: ERROR"
                })
                continue

            # Compare view columns
            ans_v_cols = ans_cols_by_view.get(canon, [])
            stud_v_cols = stud_cols_by_view.get(canon, [])

            ans_col_canons = {c["column_name_canonical"]: c for c in ans_v_cols if c["column_name_canonical"]}
            stud_col_canons = {c["column_name_canonical"]: c for c in stud_v_cols if c["column_name_canonical"]}

            missing_cols = []
            for col_canon in ans_col_canons:
                if col_canon not in stud_col_canons:
                    missing_cols.append(col_canon)

            extra_cols = []
            for col_canon in stud_col_canons:
                if col_canon not in ans_col_canons:
                    extra_cols.append(col_canon)

            if missing_cols or extra_cols:
                err_msg = ""
                if missing_cols:
                    err_msg += f"Missing columns: {missing_cols}. "
                if extra_cols:
                    err_msg += f"Extra columns: {extra_cols}."
                results.append({
                    "component": "view",
                    "answer_object": ans_phys,
                    "student_object": stud_phys,
                    "status": "OUTPUT_SCHEMA_MISMATCH",
                    "severity": "high",
                    "message": f"View output columns mismatch for '{canon}'. {err_msg}",
                    "evidence": f"Expected count: {len(ans_col_canons)}, Got: {len(stud_col_canons)}"
                })
            else:
                msg = f"View matched by alias" if ans_phys.lower() != stud_phys.lower() else "View matched exactly"
                results.append({
                    "component": "view",
                    "answer_object": ans_phys,
                    "student_object": stud_phys,
                    "status": "PASS",
                    "severity": "info",
                    "message": msg,
                    "evidence": f"Mapped '{stud_phys}' to '{ans_phys}'"
                })

    # 2. Check for extra views
    for canon, stud_v in stud_view_map.items():
        if canon not in ans_view_map:
            results.append({
                "component": "view",
                "answer_object": "",
                "student_object": stud_v["view_name"],
                "status": "EXTRA",
                "severity": "low",
                "message": f"Extra view '{stud_v['view_name']}' found in student database",
                "evidence": f"Physical: {stud_v['view_name']} (mapped to '{canon}')"
            })

    return results
