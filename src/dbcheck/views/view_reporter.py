"""
view_reporter.py — Execute and compare student views against answer views.

Supports two execution modes (configured via AssignmentConfig.execution_mode):
  compare_existing_data      — no seeding; restore DBs and SELECT from existing data.
  compare_seeded_test_data   — seed CSV test data before querying (legacy / explicit mode).

Statuses emitted (per view):
  VIEW_PASS
  VIEW_NOT_FOUND
  VIEW_MAPPING_AMBIGUOUS
  VIEW_EXECUTION_ERROR
  VIEW_OUTPUT_SCHEMA_MISMATCH
  VIEW_VALUE_MISMATCH
  VIEW_ROW_COUNT_MISMATCH
  VIEW_ORDER_MISMATCH
  DATA_SEED_ERROR            — answer view failed (only in seeded mode)
"""

import csv
import difflib
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd

from dbcheck.config import AssignmentConfig, ViewConfig
from dbcheck.views.view_output_reader import read_view_output
from dbcheck.views.output_canonicalizer import resolve_view_columns, canonicalize_view_output
from dbcheck.views.value_normalizer import normalize_dataframe, compare_ordered
from dbcheck.views.result_comparator import compare_multisets
from dbcheck.structure.type_compatibility import get_type_group
from dbcheck.utils.logging import get_logger
from dbcheck.views.sql_rewriter import extract_select_body, rewrite_sql_query
from dbcheck.snapshot.normalizer import normalize_key

REPORT_HEADERS = [
    "submission_id",
    "answer_view",
    "student_view",
    "matched_student_view",
    "match_method",
    "status",
    "matched_columns",
    "missing_columns",
    "extra_columns",
    "row_count_answer",
    "row_count_student",
    "schema_score",
    "row_count_score",
    "value_score",
    "order_score",
    "total_match_score",
    "answer_minus_student_count",
    "student_minus_answer_count",
    "value_mismatch_count",
    "order_mismatch_count",
    "execution_error",
    "reason",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_view_config_from_snapshot(
    av_name: str,
    av_canon: str,
    ans_view_cols_snap: List[Dict[str, Any]],
    explicit_by_canon: Dict[str, ViewConfig],
) -> ViewConfig:
    """Construct a ViewConfig for a view that exists in the answer snapshot but is not
    explicitly configured.  Uses snapshot column metadata to infer types."""
    view_cols = []
    av_canon_l = av_canon.lower().strip()
    av_name_l = av_name.lower().strip()
    for col in ans_view_cols_snap:
        col_v_canon = (col.get("view_name_canonical") or col.get("view_name") or "").lower().strip()
        if col_v_canon in (av_canon_l, av_name_l):
            dt = col.get("data_type", "text")
            group = get_type_group(dt)
            v_type = "text"
            if group in ("integer", "fixed_decimal", "floating"):
                v_type = "number"
            elif group == "date_time":
                v_type = "date"
            elif group == "boolean":
                v_type = "boolean"
            canon_col = col.get("column_name_canonical") or col.get("column_name", "")
            view_cols.append({"canonical": canon_col, "type": v_type, "aliases": []})

    return ViewConfig({
        "answer_view": av_canon,
        "answer_required": True,
        "student_required": True,
        "check_mode": "full",
        "order_sensitive": False,
        "expected_output": {
            "columns": view_cols,
            "sort_by": [view_cols[0]["canonical"]] if view_cols else [],
            "numeric_tolerance": 0.01,
        },
    })


def _resolve_expected_views(
    config: AssignmentConfig,
    ans_views_snap: List[Dict[str, Any]],
    ans_view_cols_snap: List[Dict[str, Any]],
) -> List[ViewConfig]:
    """Determine the list of views to test according to views_mode.

    answer_snapshot (default):
      - Required views come solely from answer_snapshot/views.csv.
      - Config entries refine tolerance/aliases/order_sensitive; they do NOT add new requirements.

    explicit_config:
      - Required views come from config.views list only.
    """
    logger = get_logger()
    explicit_by_canon: Dict[str, ViewConfig] = {
        vc.answer_view.lower().strip(): vc for vc in config.views
    }

    def _copy_view_config_for_answer_view(source: ViewConfig, answer_view: str) -> ViewConfig:
        copied = ViewConfig({
            "answer_view": answer_view,
            "answer_required": source.answer_required,
            "student_required": source.student_required,
            "check_mode": source.check_mode,
            "order_sensitive": source.order_sensitive,
            "expected_output": {
                "columns": list(source.columns),
                "sort_by": list(source.sort_by),
                "numeric_tolerance": source.numeric_tolerance,
            },
        })
        return copied

    def _view_key_matches(config_key: str, answer_name: str) -> bool:
        config_key = (config_key or "").strip()
        answer_name = (answer_name or "").strip()
        if not config_key or not answer_name:
            return False
        if config_key.lower() == answer_name.lower():
            return True
        if normalize_key(config_key) == normalize_key(answer_name):
            return True
        import re
        return re.search(
            rf"(^|[^A-Za-z0-9]){re.escape(config_key)}([^A-Za-z0-9]|$)",
            answer_name,
            flags=re.IGNORECASE,
        ) is not None

    if config.views_mode == "explicit_config":
        return config.views

    # answer_snapshot mode — source of truth is the snapshot
    # Warn about configured views not present in snapshot
    ans_snap_names = {v["view_name"].lower().strip() for v in ans_views_snap}
    ans_snap_canons = {
        v["view_name_canonical"].lower().strip()
        for v in ans_views_snap
        if v.get("view_name_canonical")
    }
    all_ans_names = ans_snap_names | ans_snap_canons

    for cv in config.views:
        if cv.answer_view.lower().strip() not in all_ans_names and not any(
            _view_key_matches(cv.answer_view, name) for name in all_ans_names
        ):
            logger.warning(
                f"[CONFIG_VIEW_NOT_IN_ANSWER_SNAPSHOT] Configured view '{cv.answer_view}' "
                f"not present in answer snapshot — ignored."
            )

    expected: List[ViewConfig] = []
    for av in ans_views_snap:
        av_name = av["view_name"]
        av_canon = av.get("view_name_canonical") or av_name
        av_canon_l = av_canon.lower().strip()
        av_name_l = av_name.lower().strip()

        matched_cfg = explicit_by_canon.get(av_canon_l) or explicit_by_canon.get(av_name_l)
        if not matched_cfg:
            matched_cfg = next(
                (
                    vc for vc in config.views
                    if _view_key_matches(vc.answer_view, av_name)
                    or _view_key_matches(vc.answer_view, av_canon)
                ),
                None,
            )
        if matched_cfg:
            if matched_cfg.answer_view.lower().strip() in {av_name_l, av_canon_l}:
                expected.append(matched_cfg)
            else:
                expected.append(_copy_view_config_for_answer_view(matched_cfg, av_name))
        else:
            vc = _build_view_config_from_snapshot(av_name, av_canon, ans_view_cols_snap, explicit_by_canon)
            expected.append(vc)

    return expected


def _find_student_view(
    ans_v_canon: str,
    stud_view_map: Dict[str, List[str]],
) -> Tuple[str, str]:
    """Resolve the student physical view name matching a given answer canonical name.

    Returns (stud_v_name, status_if_error). If status_if_error is empty string,
    resolution succeeded.
    """
    candidates = stud_view_map.get(ans_v_canon.lower().strip(), [])
    if not candidates:
        return "", "VIEW_NOT_FOUND"
    if len(candidates) > 1:
        return candidates[0], "VIEW_MAPPING_AMBIGUOUS"
    return candidates[0], ""


def _export_raw_csv(df: Optional[pd.DataFrame], path: Path) -> None:
    """Write a raw (un-normalized) DataFrame to a CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if df is not None and not df.empty:
        df.to_csv(path, index=False, encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")


def _export_diff_csv(diff_df: Optional[pd.DataFrame], path: Path) -> None:
    """Write a normalized diff DataFrame to a CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if diff_df is not None and not diff_df.empty:
        diff_df.to_csv(path, index=False, encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")


def _write_sql_files(
    sql_dir: Path,
    view_name: str,
    raw_definition: str,
    select_body: str,
    rewritten_sql: str,
    rewrite_status: str,
    rewrite_error: str,
    unmapped_tables: Any,
    unmapped_columns: Any,
    ambiguous_columns: Any,
) -> Dict[str, str]:
    """Write raw/select_body/rewritten/*.sql and diff/*.diff.txt for one view.

    Returns a dict with keys: raw_path, select_body_path, rewritten_path, diff_path
    (all as POSIX strings relative to the run directory, or empty string if not written).
    """
    raw_dir = sql_dir / "raw"
    body_dir = sql_dir / "select_body"
    rw_dir = sql_dir / "rewritten"
    diff_dir_out = sql_dir / "diff"

    for d in (raw_dir, body_dir, rw_dir, diff_dir_out):
        d.mkdir(parents=True, exist_ok=True)

    # Sanitize view name for use as a filename (replace characters that break paths on Windows)
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in view_name)

    raw_path = raw_dir / f"{safe_name}.sql"
    body_path = body_dir / f"{safe_name}.sql"
    rw_path = rw_dir / f"{safe_name}.sql"
    diff_path = diff_dir_out / f"{safe_name}.diff.txt"

    # 1. Raw definition
    if raw_definition:
        raw_path.write_text(raw_definition, encoding="utf-8")

    # 2. Select body
    if select_body:
        body_path.write_text(select_body, encoding="utf-8")

    # 3. Rewritten SQL (or failure diagnostic header)
    if rewrite_status == "VIEW_SQL_REWRITE_SUCCESS" and rewritten_sql:
        rw_path.write_text(rewritten_sql, encoding="utf-8")
    else:
        # Write diagnostic comment header followed by original select body
        lines = [
            "-- Rewrite failed",
            f"-- Status: {rewrite_status}",
        ]
        if rewrite_error:
            lines.append(f"-- Error: {rewrite_error}")
        if isinstance(unmapped_tables, list) and unmapped_tables:
            lines.append(f"-- Unmapped tables: {', '.join(str(x) for x in unmapped_tables)}")
        elif isinstance(unmapped_tables, str) and unmapped_tables:
            lines.append(f"-- Unmapped tables: {unmapped_tables}")
        if isinstance(unmapped_columns, list) and unmapped_columns:
            lines.append(f"-- Unmapped columns: {', '.join(str(x) for x in unmapped_columns)}")
        elif isinstance(unmapped_columns, str) and unmapped_columns:
            lines.append(f"-- Unmapped columns: {unmapped_columns}")
        if isinstance(ambiguous_columns, list) and ambiguous_columns:
            lines.append(f"-- Ambiguous columns: {', '.join(str(x) for x in ambiguous_columns)}")
        elif isinstance(ambiguous_columns, str) and ambiguous_columns:
            lines.append(f"-- Ambiguous columns: {ambiguous_columns}")
        lines.append("")
        if select_body:
            lines.append(select_body)
        rw_path.write_text("\n".join(lines), encoding="utf-8")

    # 4. Unified diff: select_body vs rewritten (or diagnostic)
    from_text = (select_body or raw_definition or "").splitlines(keepends=True)
    to_text = (rewritten_sql if rewrite_status == "VIEW_SQL_REWRITE_SUCCESS" and rewritten_sql else rw_path.read_text(encoding="utf-8")).splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        from_text,
        to_text,
        fromfile=f"select_body/{safe_name}.sql",
        tofile=f"rewritten/{safe_name}.sql",
        lineterm="",
    ))
    diff_path.write_text("\n".join(diff_lines), encoding="utf-8")

    return {
        "raw_path": str(raw_path),
        "select_body_path": str(body_path) if select_body else "",
        "rewritten_path": str(rw_path),
        "diff_path": str(diff_path),
    }


def _empty_metrics() -> Dict[str, Any]:
    return {
        "row_count_answer": 0,
        "row_count_student": 0,
        "answer_minus_student_count": 0,
        "student_minus_answer_count": 0,
        "value_mismatch_count": 0,
    }


def _rewrite_diagnostic_message(status: str, rw_res: Dict[str, Any]) -> str:
    if rw_res.get("error_message"):
        return rw_res.get("error_message", "")
    if status == "VIEW_SQL_REWRITE_UNMAPPED_COLUMN":
        cols = ";".join(str(x) for x in rw_res.get("unmapped_columns", []) if x)
        return f"Unmapped columns: {cols}. See raw_select_sql_path and rewritten_sql_path."
    if status == "VIEW_SQL_REWRITE_UNMAPPED_TABLE":
        tables = ";".join(str(x) for x in rw_res.get("unmapped_tables", []) if x)
        return f"Unmapped tables: {tables}. See raw_select_sql_path and rewritten_sql_path."
    if status == "VIEW_SQL_REWRITE_AMBIGUOUS_COLUMN":
        cols = ";".join(str(x) for x in rw_res.get("ambiguous_columns", []) if x)
        return f"Ambiguous columns: {cols}. See raw_select_sql_path and rewritten_sql_path."
    if status == "VIEW_SQL_REWRITE_UNSUPPORTED_VIEW_DEPENDENCY":
        views = ";".join(str(x) for x in rw_res.get("dependent_views", []) if x)
        return f"dependent_view={views}"
    return ""


# ---------------------------------------------------------------------------
# Execution mode implementations
# ---------------------------------------------------------------------------

def _run_compare_existing(
    db_conn: Any,
    ans_db: str,
    stud_db: str,
    ans_v_name: str,
    stud_v_name: str,
    view_cfg: ViewConfig,
    view_outputs_dir: Path,
    diff_dir: Path,
    accepted_col_mappings: Dict[str, str],
    col_accept_threshold: float,
    export_outputs: bool,
) -> Dict[str, Any]:
    """Compare existing data: SELECT from both DBs, no seeding, export raw + diff CSVs."""
    logger = get_logger()
    status = ""
    execution_error = ""
    matched_cols: List[str] = []
    missing_cols: List[str] = []
    extra_cols: List[str] = []
    metrics = _empty_metrics()

    # 1. Execute views
    ans_df, ans_err = read_view_output(db_conn, ans_db, ans_v_name)
    stud_df, stud_err = read_view_output(db_conn, stud_db, stud_v_name)

    if ans_err:
        # Answer view itself failed — infrastructure error
        status = "VIEW_EXECUTION_ERROR"
        execution_error = f"Answer view error: {ans_err}"
        logger.error(f"Answer view '{ans_v_name}' failed: {ans_err}")
        return _build_result(status, ans_v_name, stud_v_name, execution_error,
                              matched_cols, missing_cols, extra_cols, metrics)

    if stud_err:
        status = "VIEW_EXECUTION_ERROR"
        execution_error = stud_err
        logger.warning(f"Student view '{stud_v_name}' failed: {stud_err}")
        # Still export answer raw output for reference
        if export_outputs:
            _export_raw_csv(ans_df, view_outputs_dir / "answer" / f"{ans_v_name}.csv")
        return _build_result(status, ans_v_name, stud_v_name, execution_error,
                              matched_cols, missing_cols, extra_cols, metrics)

    # 2. Export raw CSVs
    if export_outputs:
        _export_raw_csv(ans_df, view_outputs_dir / "answer" / f"{ans_v_name}.csv")
        _export_raw_csv(stud_df, view_outputs_dir / "student" / f"{ans_v_name}.csv")

    # 3. Map student output columns → canonical names
    if view_cfg.columns:
        try:
            phys_cols = list(stud_df.columns) if stud_df is not None else []
            col_mapping = resolve_view_columns(
                phys_cols, view_cfg, accepted_col_mappings, col_accept_threshold
            )
            expected_canonicals = [c["canonical"] for c in view_cfg.columns]
            for _p, canon in col_mapping.items():
                matched_cols.append(canon)
            for c in expected_canonicals:
                if c not in matched_cols:
                    missing_cols.append(c)
            for p in phys_cols:
                if p not in col_mapping:
                    extra_cols.append(p)
            if missing_cols or extra_cols:
                status = "VIEW_OUTPUT_SCHEMA_MISMATCH"
                logger.warning(
                    f"Schema mismatch for view '{stud_v_name}': "
                    f"missing={missing_cols}, extra={extra_cols}"
                )
                return _build_result(status, ans_v_name, stud_v_name, execution_error,
                                     matched_cols, missing_cols, extra_cols, metrics)
        except ValueError as ve:
            status = "VIEW_OUTPUT_SCHEMA_MISMATCH"
            execution_error = str(ve)
            logger.warning(f"Column mapping ambiguity for '{stud_v_name}': {ve}")
            return _build_result(status, ans_v_name, stud_v_name, execution_error,
                                 matched_cols, missing_cols, extra_cols, metrics)

    # 4. Canonicalize and compare
    try:
        ans_canon = canonicalize_view_output(ans_df, view_cfg, {}, col_accept_threshold)
        stud_canon = canonicalize_view_output(stud_df, view_cfg, accepted_col_mappings, col_accept_threshold)

        if view_cfg.order_sensitive:
            # Order-sensitive: row-by-row comparison after normalization
            metrics_ord, diff_df = compare_ordered(ans_canon, stud_canon, view_cfg)
            metrics["row_count_answer"] = metrics_ord["row_count_answer"]
            metrics["row_count_student"] = metrics_ord["row_count_student"]
            metrics["value_mismatch_count"] = metrics_ord["value_mismatch_count"]

            if diff_df is not None:
                status = "VIEW_ORDER_MISMATCH"
                _export_diff_csv(diff_df, diff_dir / f"view_diff_{ans_v_name}.csv")
            elif metrics_ord["row_count_answer"] != metrics_ord["row_count_student"]:
                status = "VIEW_ROW_COUNT_MISMATCH"
                metrics["answer_minus_student_count"] = metrics_ord["answer_minus_student_count"]
                metrics["student_minus_answer_count"] = metrics_ord["student_minus_answer_count"]
            else:
                status = "VIEW_PASS"
        else:
            # Multiset comparison (default)
            ans_minus_stud, stud_minus_ans, ms_metrics = compare_multisets(ans_canon, stud_canon)
            metrics.update(ms_metrics)

            if ms_metrics["row_count_answer"] != ms_metrics["row_count_student"] and (
                ms_metrics["answer_minus_student_count"] > 0 or ms_metrics["student_minus_answer_count"] > 0
            ):
                if ms_metrics["value_mismatch_count"] == 0:
                    status = "VIEW_ROW_COUNT_MISMATCH"
                else:
                    status = "VIEW_VALUE_MISMATCH"
            elif ms_metrics["answer_minus_student_count"] > 0 or ms_metrics["student_minus_answer_count"] > 0:
                status = "VIEW_VALUE_MISMATCH"
            else:
                status = "VIEW_PASS"

            if status != "VIEW_PASS":
                diff_path = diff_dir / f"view_diff_{ans_v_name}.csv"
                combined = pd.concat(
                    [
                        ans_minus_stud.assign(_source="answer_only"),
                        stud_minus_ans.assign(_source="student_only"),
                    ],
                    ignore_index=True,
                )
                _export_diff_csv(combined, diff_path)

    except Exception as ex:
        status = "VIEW_EXECUTION_ERROR"
        execution_error = f"Comparison error: {ex}"
        logger.error(f"Error comparing view '{ans_v_name}': {ex}")

    return _build_result(status, ans_v_name, stud_v_name, execution_error,
                         matched_cols, missing_cols, extra_cols, metrics)


def _run_compare_seeded(
    db_conn: Any,
    ans_db: str,
    stud_db: str,
    ans_v_name: str,
    stud_v_name: str,
    view_cfg: ViewConfig,
    view_outputs_dir: Path,
    diff_dir: Path,
    accepted_col_mappings: Dict[str, str],
    col_accept_threshold: float,
    export_outputs: bool,
) -> Dict[str, Any]:
    """Legacy seeded mode — same comparison logic as compare_existing after seeding is done
    externally by the CLI caller. Internally identical to compare_existing."""
    # Seeding is done by the CLI; here we just query and compare.
    return _run_compare_existing(
        db_conn, ans_db, stud_db, ans_v_name, stud_v_name,
        view_cfg, view_outputs_dir, diff_dir,
        accepted_col_mappings, col_accept_threshold, export_outputs,
    )


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------

def _build_result(
    status: str,
    ans_v_name: str,
    stud_v_name: str,
    execution_error: str,
    matched_cols: List[str],
    missing_cols: List[str],
    extra_cols: List[str],
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "answer_view": ans_v_name,
        "student_view": stud_v_name,
        "status": status,
        "matched_columns": ";".join(matched_cols),
        "missing_columns": ";".join(missing_cols),
        "extra_columns": ";".join(extra_cols),
        "row_count_answer": metrics.get("row_count_answer", 0),
        "row_count_student": metrics.get("row_count_student", 0),
        "answer_minus_student_count": metrics.get("answer_minus_student_count", 0),
        "student_minus_answer_count": metrics.get("student_minus_answer_count", 0),
        "value_mismatch_count": metrics.get("value_mismatch_count", 0),
        "execution_error": execution_error,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_student_views(
    db_conn: Any,
    stud_db: str,
    submission_id: str,
    sql_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Extract student view SQL from the database.

    If *sql_dir* is provided, write each raw DDL definition to
    ``sql_dir/raw/<view_name>.sql`` and record the path in the result dict.
    """
    logger = get_logger()
    sql = """
    SELECT 
        v.name AS view_name,
        COALESCE(m.definition, OBJECT_DEFINITION(v.object_id)) AS definition
    FROM sys.views v
    LEFT JOIN sys.sql_modules m ON v.object_id = m.object_id
    WHERE v.is_ms_shipped = 0
    """
    results = []
    try:
        rows = db_conn.execute_query(sql, db_name=stud_db)
        for r in rows:
            view_name = r["view_name"]
            definition = r["definition"]
            raw_definition_path = ""
            if definition:
                # Write raw SQL file
                if sql_dir is not None:
                    raw_dir = sql_dir / "raw"
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in view_name)
                    raw_file = raw_dir / f"{safe_name}.sql"
                    raw_file.write_text(definition, encoding="utf-8")
                    raw_definition_path = str(raw_file)
                results.append({
                    "submission_id": submission_id,
                    "student_view_name": view_name,
                    "definition_found": True,
                    "raw_definition": definition,
                    "raw_definition_path": raw_definition_path,
                    "extract_status": "VIEW_SQL_EXTRACTED",
                    "extract_error": ""
                })
            else:
                results.append({
                    "submission_id": submission_id,
                    "student_view_name": view_name,
                    "definition_found": False,
                    "raw_definition": "",
                    "raw_definition_path": "",
                    "extract_status": "VIEW_SQL_DEFINITION_MISSING",
                    "extract_error": ""
                })
    except Exception as e:
        logger.error(f"Error extracting student views for {submission_id}: {e}")
        try:
            simple_rows = db_conn.execute_query("SELECT name FROM sys.views WHERE is_ms_shipped = 0", db_name=stud_db)
            for r in simple_rows:
                view_name = r["name"]
                results.append({
                    "submission_id": submission_id,
                    "student_view_name": view_name,
                    "definition_found": False,
                    "raw_definition": "",
                    "raw_definition_path": "",
                    "extract_status": "VIEW_SQL_EXTRACTION_ERROR",
                    "extract_error": str(e)
                })
        except Exception as e2:
            logger.error(f"Fallback view name extraction failed: {e2}")
            
    return results


def run_compare_rewritten_sql_on_answer_db(
    db_conn: Any,
    ans_db: str,
    stud_db: str,
    submission_id: str,
    config: AssignmentConfig,
    expected_views: List[ViewConfig],
    output_report_path: Path,
    diff_dir: Path,
    col_accept_threshold: float,
    export_outputs: bool,
    student_view_definitions: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    logger = get_logger()
    output_report_path.parent.mkdir(parents=True, exist_ok=True)

    # Compute view_sql_dir: runs/<run_id>/submissions/<submission_id>/view_sql/
    # output_report_path is runs/<run_id>/submissions/<submission_id>/reports/view_test_report.csv
    view_sql_dir = output_report_path.parent.parent / "view_sql"
    view_sql_dir.mkdir(parents=True, exist_ok=True)

    # 1. Use snapshot view DDL when provided; otherwise keep the legacy live extraction path.
    extracted_views = []
    if student_view_definitions is not None:
        for row in student_view_definitions:
            extracted_views.append({
                "submission_id": submission_id,
                "student_view_name": row.get("student_view_name") or row.get("view_name", ""),
                "definition_found": str(row.get("definition_found", "")).lower() in ("true", "1", "yes") or bool(row.get("raw_definition")),
                "raw_definition": row.get("raw_definition", ""),
                "raw_definition_path": row.get("raw_definition_path", ""),
                "extract_status": row.get("extract_status", ""),
                "extract_error": row.get("extract_error", ""),
            })
    else:
        extracted_views = extract_student_views(db_conn, stud_db, submission_id, sql_dir=view_sql_dir)
    dependent_view_names = {
        ev.get("student_view_name") or ev.get("view_name") or ""
        for ev in extracted_views
        if ev.get("student_view_name") or ev.get("view_name")
    }
    
    # Write view_sql_extraction_report.csv (updated schema with raw_definition_path)
    extract_report_path = output_report_path.parent / "view_sql_extraction_report.csv"
    with open(extract_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "submission_id", "student_view_name", "definition_found",
            "raw_definition", "raw_definition_path", "extract_status", "extract_error"
        ])
        writer.writeheader()
        for ev in extracted_views:
            writer.writerow({k: ev.get(k, "") for k in [
                "submission_id", "student_view_name", "definition_found",
                "raw_definition", "raw_definition_path", "extract_status", "extract_error"
            ]})
            
    # 2. Load accepted mapping reports
    table_map = {}
    column_map = {}
    
    table_report_path = output_report_path.parent / "table_mapping_report.csv"
    if table_report_path.exists():
        try:
            with open(table_report_path, "r", encoding="utf-8") as f:
                good_table_statuses = {
                    "TABLE_MATCHED_EXACT", "TABLE_MATCHED_ALIAS",
                    "TABLE_MATCHED_ABBREVIATION", "TABLE_MATCHED_FUZZY_HIGH"
                }
                if config.sql_rewrite.allow_weak_table_aliases:
                    good_table_statuses.add("TABLE_MATCHED_WEAK_ALIAS")
                for row in csv.DictReader(f):
                    if row["match_status"] in good_table_statuses:
                        student_table = row["student_table"]
                        answer_table = row["answer_table"]
                        table_map[student_table] = answer_table
                        table_map[answer_table] = answer_table
        except Exception as e:
            logger.warning(f"Failed to read table mapping report: {e}")
            
    col_report_path = output_report_path.parent / "column_mapping_report.csv"
    if col_report_path.exists():
        try:
            with open(col_report_path, "r", encoding="utf-8") as f:
                good_col_statuses = {
                    "COLUMN_MATCHED_EXACT", "COLUMN_MATCHED_ALIAS",
                    "COLUMN_MATCHED_ABBREVIATION"
                }
                if config.sql_rewrite.allow_weak_column_aliases:
                    good_col_statuses.add("COLUMN_MATCHED_WEAK_ALIAS")
                for row in csv.DictReader(f):
                    if row["match_status"] in good_col_statuses:
                        student_table = row["student_table"]
                        student_column = row["student_column"]
                        answer_table = table_map.get(student_table, row.get("answer_table", ""))
                        answer_column = row["answer_column"]
                        column_map[(student_table, student_column)] = answer_column
                        if answer_table:
                            column_map[(answer_table, student_column)] = answer_column
        except Exception as e:
            logger.warning(f"Failed to read column mapping report: {e}")
            
    # 3. Rewrite each safe candidate and write .sql files
    rewritten_candidates = []

    for ev in extracted_views:
        student_view_name = ev["student_view_name"]
        raw_definition = ev.get("raw_definition", "")
        raw_definition_path = ev.get("raw_definition_path", "")

        if not ev["definition_found"]:
            # Still write diagnostic rewritten SQL file (failure comment only)
            file_paths = _write_sql_files(
                view_sql_dir, student_view_name,
                raw_definition="", select_body="",
                rewritten_sql="",
                rewrite_status="VIEW_SQL_REWRITE_PARSE_ERROR",
                rewrite_error=ev.get("extract_error") or "View definition missing.",
                unmapped_tables=[], unmapped_columns=[], ambiguous_columns=[],
            )
            rewritten_candidates.append({
                "submission_id": submission_id,
                "student_view_name": student_view_name,
                "raw_sql_available": False,
                "parse_status": "VIEW_SQL_PARSE_ERROR",
                "rewrite_status": "VIEW_SQL_REWRITE_PARSE_ERROR",
                "safety_status": "VIEW_SQL_UNSAFE_REVIEW",
                "table_mappings_used": "",
                "column_mappings_used": "",
                "unmapped_tables": "",
                "unmapped_columns": "",
                "ambiguous_columns": "",
                "raw_select_sql": "",
                "rewritten_sql": "",
                "raw_select_sql_path": file_paths["select_body_path"],
                "rewritten_sql_path": file_paths["rewritten_path"],
                "execution_status": "NOT_EXECUTED",
                "execution_error": ev.get("extract_error") or "View definition missing."
            })
            continue

        # Extract SELECT body
        parse_error_msg = ""
        try:
            raw_select_sql = extract_select_body(raw_definition)
            parse_status = "VIEW_SQL_PARSE_SUCCESS"
        except Exception as e:
            raw_select_sql = ""
            parse_status = "VIEW_SQL_PARSE_ERROR"
            parse_error_msg = str(e)

        if parse_status == "VIEW_SQL_PARSE_ERROR":
            file_paths = _write_sql_files(
                view_sql_dir, student_view_name,
                raw_definition=raw_definition, select_body="",
                rewritten_sql="",
                rewrite_status="VIEW_SQL_REWRITE_PARSE_ERROR",
                rewrite_error=parse_error_msg or "Could not parse DDL to extract SELECT statement query body.",
                unmapped_tables=[], unmapped_columns=[], ambiguous_columns=[],
            )
            rewritten_candidates.append({
                "submission_id": submission_id,
                "student_view_name": student_view_name,
                "raw_sql_available": True,
                "parse_status": "VIEW_SQL_PARSE_ERROR",
                "rewrite_status": "VIEW_SQL_REWRITE_PARSE_ERROR",
                "safety_status": "VIEW_SQL_UNSAFE_REVIEW",
                "table_mappings_used": "",
                "column_mappings_used": "",
                "unmapped_tables": "",
                "unmapped_columns": "",
                "ambiguous_columns": "",
                "raw_select_sql": "",
                "rewritten_sql": "",
                "raw_select_sql_path": file_paths["select_body_path"],
                "rewritten_sql_path": file_paths["rewritten_path"],
                "execution_status": "NOT_EXECUTED",
                "execution_error": parse_error_msg or "Could not parse DDL to extract SELECT statement query body."
            })
            continue

        # Rewrite query
        rw_res = rewrite_sql_query(
            raw_select_sql, table_map, column_map, config,
            dependent_view_names=dependent_view_names,
        )
        rw_status = rw_res["status"]
        rewritten_sql = rw_res.get("rewritten_sql", "")
        rewrite_error = _rewrite_diagnostic_message(rw_status, rw_res)

        # Write all .sql and .diff files
        file_paths = _write_sql_files(
            view_sql_dir, student_view_name,
            raw_definition=raw_definition,
            select_body=raw_select_sql,
            rewritten_sql=rewritten_sql,
            rewrite_status=rw_status,
            rewrite_error=rewrite_error,
            unmapped_tables=rw_res.get("unmapped_tables", []),
            unmapped_columns=rw_res.get("unmapped_columns", []),
            ambiguous_columns=rw_res.get("ambiguous_columns", []),
        )

        rewritten_candidates.append({
            "submission_id": submission_id,
            "student_view_name": student_view_name,
            "raw_sql_available": True,
            "parse_status": "VIEW_SQL_PARSE_SUCCESS",
            "rewrite_status": rw_status,
            "safety_status": "VIEW_SQL_UNSAFE_REVIEW" if rw_status == "VIEW_SQL_UNSAFE_REVIEW" else "VIEW_SQL_SAFE",
            "table_mappings_used": rw_res.get("table_mappings_used", []),
            "column_mappings_used": rw_res.get("column_mappings_used", []),
            "unmapped_tables": rw_res.get("unmapped_tables", []),
            "unmapped_columns": rw_res.get("unmapped_columns", []),
            "ambiguous_columns": rw_res.get("ambiguous_columns", []),
            "dependent_views": rw_res.get("dependent_views", []),
            "raw_select_sql": raw_select_sql,
            "rewritten_sql": rewritten_sql,
            "raw_select_sql_path": file_paths["select_body_path"],
            "rewritten_sql_path": file_paths["rewritten_path"],
            "execution_status": "NOT_EXECUTED",
            "execution_error": rewrite_error
        })
        
    # 4. Cache the expected answer outputs from answer database
    expected_outputs = {}
    expected_errors = {}
    
    for view_cfg in expected_views:
        ans_v_name = view_cfg.answer_view
        try:
            ans_df = db_conn.execute_query_df(f"SELECT * FROM dbo.[{ans_v_name}]", db_name=ans_db)
            expected_outputs[ans_v_name] = ans_df
        except Exception as e:
            logger.error(f"Failed to execute expected view '{ans_v_name}' on answer database: {e}")
            expected_errors[ans_v_name] = str(e)
            
    # 5. Execute rewritten candidates on answer database and compare
    all_matches = []
    
    for rc in rewritten_candidates:
        if rc["rewrite_status"] == "VIEW_SQL_REWRITE_SUCCESS" and rc["safety_status"] == "VIEW_SQL_SAFE":
            rewritten_sql = rc["rewritten_sql"]
            try:
                stud_df = db_conn.execute_query_df(rewritten_sql, db_name=ans_db)
                rc["execution_status"] = "EXECUTION_SUCCESS"
                
                if export_outputs:
                    raw_out_path = output_report_path.parent.parent / "view_outputs" / "student" / f"{rc['student_view_name']}_rewritten.csv"
                    _export_raw_csv(stud_df, raw_out_path)
            except Exception as e:
                stud_df = None
                rc["execution_status"] = "EXECUTION_ERROR"
                rc["execution_error"] = str(e)
        else:
            stud_df = None
            
        # Compare this candidate against all expected views
        for view_cfg in expected_views:
            ans_v_name = view_cfg.answer_view
            ans_df = expected_outputs.get(ans_v_name)
            
            schema_score = 0.0
            row_count_score = 0.0
            value_score = 0.0
            order_score = 0.0
            total_match_score = 0.0
            
            val_mismatch = 0
            ord_mismatch = 0
            matched_cols = []
            missing_cols = []
            extra_cols = []
            
            cand_status = "VIEW_NO_MATCHING_OUTPUT"
            reason = ""
            
            if ans_df is None:
                cand_status = "VIEW_EXECUTION_ERROR"
                reason = f"Answer view execution error: {expected_errors.get(ans_v_name)}"
            elif rc["rewrite_status"] == "VIEW_SQL_REWRITE_PARSE_ERROR":
                cand_status = "VIEW_SQL_PARSE_ERROR"
                reason = f"DDL extraction wrapper parsing failed: {rc['execution_error']}"
            elif rc["rewrite_status"] != "VIEW_SQL_REWRITE_SUCCESS":
                cand_status = rc["rewrite_status"]
                reason = f"Rewrite failed: {rc['execution_error']}"
            elif rc["safety_status"] != "VIEW_SQL_SAFE":
                cand_status = "VIEW_SQL_UNSAFE_REVIEW"
                reason = f"Safety violation: {rc['execution_error']}"
            elif rc["execution_status"] != "EXECUTION_SUCCESS":
                cand_status = "VIEW_EXECUTION_ERROR"
                reason = f"Execution error on answer DB: {rc['execution_error']}"
            else:
                try:
                    ans_canon = canonicalize_view_output(ans_df, view_cfg, {}, col_accept_threshold)
                    stud_canon = canonicalize_view_output(stud_df, view_cfg, {}, col_accept_threshold)
                    
                    expected_canonicals = [c["canonical"] for c in view_cfg.columns]
                    phys_cols = list(stud_df.columns) if stud_df is not None else []
                    
                    try:
                        col_mapping = resolve_view_columns(phys_cols, view_cfg, {}, col_accept_threshold)
                        for _p, canon in col_mapping.items():
                            matched_cols.append(canon)
                        for c in expected_canonicals:
                            if c not in matched_cols:
                                missing_cols.append(c)
                        for p in phys_cols:
                            if p not in col_mapping:
                                extra_cols.append(p)
                    except ValueError:
                        pass
                        
                    if len(expected_canonicals) > 0:
                        schema_score = len(matched_cols) / len(expected_canonicals)
                    else:
                        schema_score = 1.0
                        
                    R_a = len(ans_canon)
                    R_s = len(stud_canon)
                    row_count_score = max(0.0, 1.0 - abs(R_a - R_s) / max(R_a, 1))
                    
                    if view_cfg.order_sensitive:
                        metrics_ord, diff_df = compare_ordered(ans_canon, stud_canon, view_cfg)
                        val_mismatch = metrics_ord["value_mismatch_count"]
                        value_score = max(0.0, 1.0 - val_mismatch / max(R_a, 1))
                        order_score = 1.0 if val_mismatch == 0 else 0.0
                    else:
                        ans_minus, stud_minus, ms_metrics = compare_multisets(ans_canon, stud_canon)
                        val_mismatch = ms_metrics["value_mismatch_count"]
                        value_score = max(0.0, 1.0 - val_mismatch / max(R_a, 1))
                        order_score = 0.0
                        
                    if view_cfg.order_sensitive:
                        total_match_score = 0.2 * schema_score + 0.2 * row_count_score + 0.5 * value_score + 0.1 * order_score
                    else:
                        total_match_score = (2/9) * schema_score + (2/9) * row_count_score + (5/9) * value_score
                        
                    if missing_cols or extra_cols:
                        cand_status = "VIEW_OUTPUT_SCHEMA_MISMATCH"
                        reason = f"Schema mismatch: missing={missing_cols}, extra={extra_cols}"
                    elif row_count_score < 1.0:
                        cand_status = "VIEW_ROW_COUNT_MISMATCH"
                        reason = f"Row count mismatch: expected={R_a}, student={R_s}"
                    elif view_cfg.order_sensitive and order_score < 1.0:
                        cand_status = "VIEW_ORDER_MISMATCH"
                        reason = "Row order does not match expectation."
                    elif value_score < 1.0:
                        cand_status = "VIEW_VALUE_MISMATCH"
                        reason = f"Value mismatch: {val_mismatch} row(s)"
                    else:
                        cand_status = "VIEW_OUTPUT_MATCH"
                        reason = "Output matches expected answer."
                        
                except Exception as ex:
                    cand_status = "VIEW_EXECUTION_ERROR"
                    reason = f"Comparison failed: {ex}"
                    
            hint_score = 1.0 if normalize_key(rc["student_view_name"]) == normalize_key(ans_v_name) else 0.0
            
            all_matches.append({
                "submission_id": submission_id,
                "expected_view": ans_v_name,
                "student_view_candidate": rc["student_view_name"],
                "student_view_name_hint_score": hint_score,
                "parse_status": rc["parse_status"],
                "rewrite_status": rc["rewrite_status"],
                "safety_status": rc["safety_status"],
                "execution_status": rc["execution_status"],
                "schema_score": schema_score,
                "row_count_score": row_count_score,
                "value_score": value_score,
                "order_score": order_score,
                "total_match_score": total_match_score,
                "candidate_status": cand_status,
                "reason": reason,
                "matched_columns": ";".join(matched_cols),
                "missing_columns": ";".join(missing_cols),
                "extra_columns": ";".join(extra_cols),
                "row_count_answer": len(ans_df) if ans_df is not None else 0,
                "row_count_student": len(stud_df) if stud_df is not None else 0,
                "value_mismatch_count": val_mismatch,
                "order_mismatch_count": ord_mismatch,
                "execution_error": rc["execution_error"]
            })
            
    # Save view_sql_rewrite_report.csv (updated schema includes raw_select_sql_path, rewritten_sql_path)
    rewrite_report_path = output_report_path.parent / "view_sql_rewrite_report.csv"
    _REWRITE_REPORT_FIELDS = [
        "submission_id", "student_view_name", "parse_status",
        "rewrite_status", "safety_status",
        "raw_select_sql", "rewritten_sql",
        "raw_select_sql_path", "rewritten_sql_path",
        "table_mappings_used", "column_mappings_used",
        "unmapped_tables", "unmapped_columns", "ambiguous_columns", "dependent_views",
        "execution_status", "execution_error"
    ]
    with open(rewrite_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_REWRITE_REPORT_FIELDS)
        writer.writeheader()
        for rc in rewritten_candidates:
            rc_out = {k: rc.get(k, "") for k in _REWRITE_REPORT_FIELDS}
            # Format list fields as semicolon-separated strings
            for list_field in ("table_mappings_used", "column_mappings_used",
                               "unmapped_tables", "unmapped_columns", "ambiguous_columns",
                               "dependent_views"):
                val = rc_out[list_field]
                if isinstance(val, list):
                    rc_out[list_field] = ";".join(str(x) for x in val)
            writer.writerow(rc_out)
            
    # Save view_candidate_match_report.csv
    match_report_path = output_report_path.parent / "view_candidate_match_report.csv"
    with open(match_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "submission_id", "expected_view", "student_view_candidate", 
            "student_view_name_hint_score", "parse_status", "rewrite_status",
            "safety_status", "execution_status", "schema_score", "row_count_score",
            "value_score", "order_score", "total_match_score", "candidate_status", "reason"
        ])
        writer.writeheader()
        for match in all_matches:
            writer.writerow({k: match.get(k, "") for k in [
                "submission_id", "expected_view", "student_view_candidate", 
                "student_view_name_hint_score", "parse_status", "rewrite_status",
                "safety_status", "execution_status", "schema_score", "row_count_score",
                "value_score", "order_score", "total_match_score", "candidate_status", "reason"
            ]})
            
    # 6. One-to-one candidate assignment
    sorted_matches = sorted(all_matches, key=lambda x: (x["total_match_score"], x["student_view_name_hint_score"]), reverse=True)
    
    assigned_student_views = set()
    assigned_expected_views = set()
    final_assignments = {}
    
    for match in sorted_matches:
        e_view = match["expected_view"]
        s_view = match["student_view_candidate"]
        
        if e_view not in assigned_expected_views and s_view not in assigned_student_views:
            final_assignments[e_view] = match
            assigned_student_views.add(s_view)
            assigned_expected_views.add(e_view)
            
    final_results = []
    
    for view_cfg in expected_views:
        ans_v_name = view_cfg.answer_view
        
        if ans_v_name in final_assignments:
            match = final_assignments[ans_v_name]
            score = match["total_match_score"]
            s_view = match["student_view_candidate"]
            
            # Check for ambiguity: find second best candidate for this expected view
            other_candidates = [m for m in all_matches if m["expected_view"] == ans_v_name and m["student_view_candidate"] != s_view]
            other_candidates.sort(key=lambda x: (x["total_match_score"], x["student_view_name_hint_score"]), reverse=True)
            
            status = match["candidate_status"]
            reason = match["reason"]
            match_method = "output_based"
            
            if other_candidates:
                second_best = other_candidates[0]
                if abs(score - second_best["total_match_score"]) < 0.02 and score > 0.0:
                    status = "VIEW_MAPPING_AMBIGUOUS"
                    reason = f"Ambiguous match: multiple views ({s_view}, {second_best['student_view_candidate']}) score nearly identical ({score:.4f} vs {second_best['total_match_score']:.4f})."
                    match_method = "no_matching_output"
            
            if match["student_view_name_hint_score"] == 1.0 and match_method == "output_based":
                match_method = "name_hint_then_output"
                
            final_results.append({
                "submission_id": submission_id,
                "answer_view": ans_v_name,
                "matched_student_view": s_view,
                "match_method": match_method,
                "status": status,
                "row_count_answer": match["row_count_answer"],
                "row_count_student": match["row_count_student"],
                "schema_score": match["schema_score"],
                "row_count_score": match["row_count_score"],
                "value_score": match["value_score"],
                "order_score": match["order_score"],
                "total_match_score": match["total_match_score"],
                "value_mismatch_count": match["value_mismatch_count"],
                "order_mismatch_count": match["order_mismatch_count"],
                "execution_error": match["execution_error"],
                "reason": reason,
                "matched_columns": match["matched_columns"],
                "missing_columns": match["missing_columns"],
                "extra_columns": match["extra_columns"]
            })
        else:
            cands_for_e = [m for m in all_matches if m["expected_view"] == ans_v_name]
            cands_for_e.sort(key=lambda x: (x["total_match_score"], x["student_view_name_hint_score"]), reverse=True)
            
            status = "VIEW_NO_MATCHING_OUTPUT"
            reason = "No matching student view output found."
            match_method = "no_matching_output"
            s_view_name = ""
            
            if cands_for_e:
                best_cand = cands_for_e[0]
                if best_cand["total_match_score"] >= 0.5:
                    status = "VIEW_MAPPING_AMBIGUOUS"
                    reason = f"Ambiguous match: student view '{best_cand['student_view_candidate']}' was the best candidate for expected view '{ans_v_name}' (score {best_cand['total_match_score']:.4f}) but was assigned to another expected view."
                    s_view_name = best_cand["student_view_candidate"]
                    
            final_results.append({
                "submission_id": submission_id,
                "answer_view": ans_v_name,
                "matched_student_view": s_view_name,
                "match_method": match_method,
                "status": status,
                "row_count_answer": 0,
                "row_count_student": 0,
                "schema_score": 0.0,
                "row_count_score": 0.0,
                "value_score": 0.0,
                "order_score": 0.0,
                "total_match_score": 0.0,
                "value_mismatch_count": 0,
                "order_mismatch_count": 0,
                "execution_error": "",
                "reason": reason,
                "matched_columns": "",
                "missing_columns": "",
                "extra_columns": ""
            })
            
    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_HEADERS)
        writer.writeheader()
        for r in final_results:
            writer.writerow({k: r.get(k, "") for k in REPORT_HEADERS})
            
    # Export answer raw output if enabled and hasn't failed
    if export_outputs:
        for ans_v_name, ans_df in expected_outputs.items():
            ans_out_path = output_report_path.parent.parent / "view_outputs" / "answer" / f"{ans_v_name}.csv"
            _export_raw_csv(ans_df, ans_out_path)
            
    return final_results


def run_view_testing(
    db_conn: Any,
    ans_db: str,
    stud_db: str,
    submission_id: str,
    config: AssignmentConfig,
    ans_views_snap: List[Dict[str, Any]],
    stud_views_snap: List[Dict[str, Any]],
    stud_cols_snap: List[Dict[str, Any]],
    output_report_path: Path,
    diff_dir: Path,
    ans_view_cols_snap: Optional[List[Dict[str, Any]]] = None,
    student_view_definitions: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Execute and compare student views.  Mode is determined by config.execution_mode."""
    logger = get_logger()
    logger.info(f"Running view behavior tests for submission '{submission_id}'...")

    execution_mode = getattr(config, "execution_mode", "compare_existing_data")
    export_outputs = getattr(config, "export_outputs", True)
    col_accept_threshold = config.schema.column_accept_threshold

    # Output directories
    view_outputs_dir = output_report_path.parent.parent / "view_outputs"

    # Expected views list
    expected_views = _resolve_expected_views(
        config, ans_views_snap, ans_view_cols_snap or []
    )

    if execution_mode == "compare_rewritten_sql_on_answer_db":
        return run_compare_rewritten_sql_on_answer_db(
            db_conn, ans_db, stud_db, submission_id, config,
            expected_views, output_report_path, diff_dir,
            col_accept_threshold, export_outputs,
            student_view_definitions=student_view_definitions,
        )

    # Build student view lookup: canonical → [physical_name, ...]
    stud_view_map: Dict[str, List[str]] = {}
    for v in stud_views_snap:
        canon = (v.get("view_name_canonical") or v["view_name"]).lower().strip()
        stud_view_map.setdefault(canon, []).append(v["view_name"])

    # Load accepted column mappings from column_mapping_report.csv if present
    accepted_col_mappings: Dict[str, str] = {}
    col_report_path = output_report_path.parent / "column_mapping_report.csv"
    if col_report_path.exists():
        try:
            with open(col_report_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                good_statuses = {
                    "COLUMN_MATCHED_EXACT", "COLUMN_MATCHED_ALIAS",
                    "COLUMN_MATCHED_ABBREVIATION", "COLUMN_MATCHED_FUZZY_HIGH",
                }
                for row in reader:
                    if row["match_status"] in good_statuses:
                        accepted_col_mappings[row["student_column"].lower()] = row["answer_column"]
        except Exception as e:
            logger.warning(f"Failed to read column_mapping_report.csv: {e}")

    results: List[Dict[str, Any]] = []

    for view_cfg in expected_views:
        ans_v_name = view_cfg.answer_view
        check_mode = getattr(view_cfg, "check_mode", "full")
        ans_required = getattr(view_cfg, "answer_required", True)

        # Resolve student view
        stud_v_name, resolve_error = _find_student_view(ans_v_name, stud_view_map)

        if resolve_error:
            if resolve_error == "VIEW_NOT_FOUND":
                # Omit structural view missing from view-test report
                logger.info(f"Omitted missing view '{ans_v_name}' from view-test report for '{submission_id}'.")
                continue
            status = resolve_error
            logger.warning(f"View '{ans_v_name}' → {status} for student '{submission_id}'.")
            result = _build_result(
                status, ans_v_name, stud_v_name, "", [], [], [], _empty_metrics()
            )
            result["submission_id"] = submission_id
            results.append(result)
            continue

        # Execution-only check (e.g. Cau4: answer_required=false)
        if not ans_required or check_mode == "execution_only":
            stud_df, stud_err = read_view_output(db_conn, stud_db, stud_v_name)
            if stud_err:
                status = "VIEW_EXECUTION_ERROR"
                execution_error = stud_err
            else:
                status = "VIEW_PASS"
                execution_error = ""
            m = _empty_metrics()
            m["row_count_student"] = len(stud_df) if stud_df is not None and not stud_err else 0
            result = _build_result(status, ans_v_name, stud_v_name, execution_error, [], [], [], m)
            result["submission_id"] = submission_id
            results.append(result)
            continue

        # Full comparison
        if execution_mode == "compare_existing_data":
            result = _run_compare_existing(
                db_conn, ans_db, stud_db, ans_v_name, stud_v_name,
                view_cfg, view_outputs_dir, diff_dir,
                accepted_col_mappings, col_accept_threshold, export_outputs,
            )
        else:
            result = _run_compare_seeded(
                db_conn, ans_db, stud_db, ans_v_name, stud_v_name,
                view_cfg, view_outputs_dir, diff_dir,
                accepted_col_mappings, col_accept_threshold, export_outputs,
            )

        result["submission_id"] = submission_id
        results.append(result)

    # Write report CSV
    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["submission_id"] + REPORT_HEADERS[1:])
        writer.writeheader()
        for row in results:
            writer.writerow({h: row.get(h, "") for h in ["submission_id"] + REPORT_HEADERS[1:]})

    logger.info(f"View behavioral report saved to: {output_report_path}")
    return results
