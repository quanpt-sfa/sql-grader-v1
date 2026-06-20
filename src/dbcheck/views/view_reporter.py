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

REPORT_HEADERS = [
    "submission_id",
    "answer_view",
    "student_view",
    "status",
    "matched_columns",
    "missing_columns",
    "extra_columns",
    "row_count_answer",
    "row_count_student",
    "answer_minus_student_count",
    "student_minus_answer_count",
    "value_mismatch_count",
    "execution_error",
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
        if cv.answer_view.lower().strip() not in all_ans_names:
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
        if matched_cfg:
            expected.append(matched_cfg)
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


def _empty_metrics() -> Dict[str, Any]:
    return {
        "row_count_answer": 0,
        "row_count_student": 0,
        "answer_minus_student_count": 0,
        "student_minus_answer_count": 0,
        "value_mismatch_count": 0,
    }


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
