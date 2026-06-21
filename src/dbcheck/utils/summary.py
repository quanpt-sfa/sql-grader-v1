"""
summary.py — Compile a unified summary.csv from manifest + per-submission reports.

View status taxonomy (view_test_report.csv):
  VIEW_PASS
  VIEW_NOT_FOUND          → view_missing_count
  VIEW_MAPPING_AMBIGUOUS  → view_ambiguous_count
  VIEW_EXECUTION_ERROR    → view_execution_error_count
  VIEW_OUTPUT_SCHEMA_MISMATCH → view_output_schema_mismatch_count
  VIEW_VALUE_MISMATCH     → view_value_mismatch_count
  VIEW_ROW_COUNT_MISMATCH → view_row_count_mismatch_count
  VIEW_ORDER_MISMATCH     → view_order_mismatch_count

view_test_status:
  (empty)         — view testing completed normally
  COMMAND_ERROR   — global view-testing command failed before any per-submission work
"""

import csv
from pathlib import Path
from typing import Dict, Any, List
from dbcheck.utils.logging import get_logger

SUMMARY_HEADERS: List[str] = [
    "submission_id",
    "manifest_status",
    "manifest_error",
    # Structure metrics
    "struct_pass_count",
    "struct_missing_count",
    "struct_extra_count",
    "struct_type_mismatch_count",
    "struct_type_warning_count",
    "identifier_type_warning_count",
    "struct_ambiguous_count",
    "data_rowcount_mismatch_count",
    # PK adequacy metrics
    "pk_exact_match_count",
    "pk_alias_equivalent_count",
    "pk_surrogate_accepted_count",
    "pk_natural_accepted_count",
    "pk_alternative_accepted_count",
    "pk_review_required_count",
    "pk_missing_count",
    "pk_invalid_count",
    # FK adequacy metrics
    "fk_exact_match_count",
    "fk_relationship_match_count",
    "fk_alias_equivalent_count",
    "fk_surrogate_accepted_count",
    "fk_natural_accepted_count",
    "fk_review_required_count",
    "fk_missing_count",
    "fk_wrong_target_count",
    "fk_expected_count",
    "fk_declared_match_count",
    "fk_implied_review_count",
    "fk_wrong_parent_count",
    "fk_wrong_child_columns_count",
    "fk_relationship_error_count",
    # View metrics
    "view_required_count",
    "view_expected_count",
    "view_pass_count",
    "view_output_match_count",
    "view_partial_match_count",
    "view_no_matching_output_count",
    "view_missing_count",
    "view_ambiguous_count",
    "view_execution_error_count",
    "view_rewrite_error_count",
    "view_output_schema_mismatch_count",
    "view_schema_mismatch_count",
    "view_value_mismatch_count",
    "view_row_count_mismatch_count",
    "view_order_mismatch_count",
    "view_review_required_count",
    # Global view command status
    "view_test_status",
]

# Backward-compat alias list so old code that imports HEADERS still works.
HEADERS = SUMMARY_HEADERS

_VIEW_STATUS_MAP = {
    "VIEW_PASS": "view_pass_count",
    "VIEW_NOT_FOUND": "view_missing_count",
    "VIEW_MAPPING_AMBIGUOUS": "view_ambiguous_count",
    "VIEW_EXECUTION_ERROR": "view_execution_error_count",
    "DATA_SEED_ERROR": "view_execution_error_count",
    "VIEW_OUTPUT_SCHEMA_MISMATCH": "view_output_schema_mismatch_count",
    # Legacy statuses from old seeded mode — map to closest equivalent
    "PASS": "view_pass_count",
    "VALUE_MISMATCH": "view_value_mismatch_count",
    "OUTPUT_SCHEMA_MISMATCH": "view_output_schema_mismatch_count",
    "VIEW_OUTPUT_MAPPING_AMBIGUOUS": "view_ambiguous_count",
}


def compile_summary(run_dir: Path) -> Path:
    """Read manifest, structure reports, and view reports; write unified summary.csv."""
    logger = get_logger()
    manifest_path = run_dir / "manifest.csv"
    summary_path = run_dir / "summary.csv"

    if not manifest_path.exists():
        logger.warning(f"Cannot generate summary — manifest.csv missing in: {run_dir}")
        return summary_path

    # Read manifest
    submissions = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            submissions.append(row)

    summary_rows = []

    for sub in submissions:
        sub_id = sub["submission_id"]
        row: Dict[str, Any] = {h: 0 for h in SUMMARY_HEADERS}
        row["submission_id"] = sub_id
        row["manifest_status"] = sub["status"]
        row["manifest_error"] = sub.get("error_message", "")
        row["view_test_status"] = ""

        sub_dir = run_dir / "submissions" / sub_id
        struct_report = sub_dir / "reports" / "structure_report.csv"
        view_report = sub_dir / "reports" / "view_test_report.csv"

        # Check if fk_relationship_report.csv exists
        fk_report = sub_dir / "reports" / "fk_relationship_report.csv"
        fk_parsed = False
        if fk_report.exists():
            try:
                with open(fk_report, "r", encoding="utf-8") as ffr:
                    for fk_row in csv.DictReader(ffr):
                        row["fk_expected_count"] += 1
                        fk_status = fk_row.get("fk_status", "").upper()
                        
                        if fk_status in [
                            "FK_RELATIONSHIP_MATCH", "FK_RELATIONSHIP_MATCH_ALIAS_EQUIVALENT",
                            "FK_RELATIONSHIP_SURROGATE_ACCEPTED", "FK_RELATIONSHIP_NATURAL_ACCEPTED",
                            "FK_MATCH_EXACT", "FK_ALIAS_EQUIVALENT", "FK_SURROGATE_ACCEPTED", "FK_NATURAL_ACCEPTED"
                        ]:
                            row["fk_declared_match_count"] += 1
                            row["struct_pass_count"] += 1
                            if fk_status in ("FK_MATCH_EXACT", "FK_RELATIONSHIP_MATCH"):
                                row["fk_exact_match_count"] += 1
                            elif fk_status in ("FK_ALIAS_EQUIVALENT", "FK_RELATIONSHIP_MATCH_ALIAS_EQUIVALENT"):
                                row["fk_alias_equivalent_count"] += 1
                            elif fk_status in ("FK_SURROGATE_ACCEPTED", "FK_RELATIONSHIP_SURROGATE_ACCEPTED"):
                                row["fk_surrogate_accepted_count"] += 1
                            elif fk_status in ("FK_NATURAL_ACCEPTED", "FK_RELATIONSHIP_NATURAL_ACCEPTED"):
                                row["fk_natural_accepted_count"] += 1
                            else:
                                row["fk_relationship_match_count"] += 1
                                
                        elif fk_status in ["FK_RELATIONSHIP_IMPLIED_REVIEW_REQUIRED", "FK_IMPLIED_REVIEW_REQUIRED"]:
                            row["fk_implied_review_count"] += 1
                            row["fk_review_required_count"] += 1
                            row["struct_type_warning_count"] += 1
                            
                        elif fk_status in ["FK_RELATIONSHIP_MISSING", "FK_MISSING"]:
                            row["fk_missing_count"] += 1
                            row["struct_missing_count"] += 1
                            
                        elif fk_status in ["FK_RELATIONSHIP_WRONG_PARENT", "FK_WRONG_TARGET"]:
                            row["fk_wrong_parent_count"] += 1
                            row["fk_wrong_target_count"] += 1
                            row["struct_missing_count"] += 1
                            
                        elif fk_status == "FK_RELATIONSHIP_WRONG_CHILD_COLUMNS":
                            row["fk_wrong_child_columns_count"] += 1
                            row["struct_missing_count"] += 1
                            
                        elif fk_status in ["FK_RELATIONSHIP_WRONG_CHILD", "FK_RELATIONSHIP_WRONG_PARENT_COLUMNS",
                                           "FK_RELATIONSHIP_AMBIGUOUS", "FK_RELATIONSHIP_MAPPING_ERROR"]:
                            row["fk_relationship_error_count"] += 1
                            if fk_status in ("FK_RELATIONSHIP_AMBIGUOUS", "FK_RELATIONSHIP_MAPPING_ERROR"):
                                row["fk_review_required_count"] += 1
                                row["struct_ambiguous_count"] += 1
                            else:
                                row["struct_missing_count"] += 1
                                
                        elif fk_status in ["FK_REVIEW_REQUIRED", "FK_AMBIGUOUS", "FK_MAPPING_ERROR"]:
                            row["fk_review_required_count"] += 1
                            row["struct_type_warning_count"] += 1
                            
                fk_parsed = True
            except Exception as e:
                logger.warning(f"Error reading fk_relationship_report for '{sub_id}': {e}")

        # Structure stats
        if struct_report.exists():
            try:
                with open(struct_report, "r", encoding="utf-8") as sf:
                    for s_row in csv.DictReader(sf):
                        comp = s_row.get("component", "")
                        s_status = s_row["status"].upper()
                        if fk_parsed and (comp == "fk" or s_status.startswith("FK_")):
                            continue
                            
                        if s_status == "PK_MATCH_EXACT":
                            row["pk_exact_match_count"] += 1
                            row["struct_pass_count"] += 1
                        elif s_status == "PK_MATCH_ALIAS_EQUIVALENT":
                            row["pk_alias_equivalent_count"] += 1
                            row["struct_pass_count"] += 1
                        elif s_status == "PK_SURROGATE_ACCEPTED":
                            row["pk_surrogate_accepted_count"] += 1
                            row["struct_pass_count"] += 1
                        elif s_status == "PK_NATURAL_ACCEPTED":
                            row["pk_natural_accepted_count"] += 1
                            row["struct_pass_count"] += 1
                        elif s_status == "PK_ALTERNATIVE_ACCEPTED":
                            row["pk_alternative_accepted_count"] += 1
                            row["struct_pass_count"] += 1
                        elif s_status == "PK_REVIEW_REQUIRED":
                            row["pk_review_required_count"] += 1
                            row["struct_type_warning_count"] += 1
                        elif s_status == "PK_MISSING":
                            row["pk_missing_count"] += 1
                            row["struct_missing_count"] += 1
                        elif s_status == "PK_INVALID":
                            row["pk_invalid_count"] += 1
                            row["struct_missing_count"] += 1
                            
                        elif s_status == "FK_MATCH_EXACT":
                            row["fk_exact_match_count"] += 1
                            row["struct_pass_count"] += 1
                        elif s_status == "FK_ALIAS_EQUIVALENT":
                            row["fk_alias_equivalent_count"] += 1
                            row["struct_pass_count"] += 1
                        elif s_status == "FK_SURROGATE_ACCEPTED":
                            row["fk_surrogate_accepted_count"] += 1
                            row["struct_pass_count"] += 1
                        elif s_status == "FK_NATURAL_ACCEPTED":
                            row["fk_natural_accepted_count"] += 1
                            row["struct_pass_count"] += 1
                        elif s_status == "FK_RELATIONSHIP_MATCH":
                            row["fk_relationship_match_count"] += 1
                            row["struct_pass_count"] += 1
                        elif s_status == "FK_REVIEW_REQUIRED" or s_status == "FK_IMPLIED_REVIEW_REQUIRED":
                            row["fk_review_required_count"] += 1
                            row["struct_type_warning_count"] += 1
                        elif s_status == "FK_MISSING":
                            row["fk_missing_count"] += 1
                            row["struct_missing_count"] += 1
                        elif s_status == "FK_WRONG_TARGET":
                            row["fk_wrong_target_count"] += 1
                            row["struct_missing_count"] += 1
                            
                        elif s_status == "SURROGATE_KEY_ACCEPTED":
                            row["struct_pass_count"] += 1
                        elif s_status == "SURROGATE_KEY_IGNORED":
                            row["struct_pass_count"] += 1
                            
                        elif s_status == "PASS":
                            row["struct_pass_count"] += 1
                        elif s_status == "MISSING":
                            if s_row.get("component") == "view":
                                row["view_missing_count"] += 1
                                row["view_required_count"] += 1
                            else:
                                row["struct_missing_count"] += 1
                        elif s_status == "EXTRA":
                            row["struct_extra_count"] += 1
                        elif s_status == "TYPE_MISMATCH":
                            row["struct_type_mismatch_count"] += 1
                        elif s_status == "TYPE_WARNING":
                            row["struct_type_warning_count"] += 1
                        elif s_status == "IDENTIFIER_TYPE_WARNING":
                            row["identifier_type_warning_count"] += 1
                        elif s_status == "ROW_COUNT_MISMATCH":
                            row["data_rowcount_mismatch_count"] += 1
                        elif "AMBIGUOUS" in s_status:
                            row["struct_ambiguous_count"] += 1
            except Exception as e:
                logger.warning(f"Error reading structure report for '{sub_id}': {e}")

        # Fallback for FK counters if fk_relationship_report was missing
        if not fk_parsed:
            row["fk_expected_count"] = (
                row["fk_exact_match_count"] + row["fk_relationship_match_count"] +
                row["fk_alias_equivalent_count"] + row["fk_surrogate_accepted_count"] +
                row["fk_natural_accepted_count"] + row["fk_review_required_count"] +
                row["fk_missing_count"] + row["fk_wrong_target_count"]
            )
            row["fk_declared_match_count"] = (
                row["fk_exact_match_count"] + row["fk_relationship_match_count"] +
                row["fk_alias_equivalent_count"] + row["fk_surrogate_accepted_count"] +
                row["fk_natural_accepted_count"]
            )
            row["fk_implied_review_count"] = 0
            row["fk_wrong_parent_count"] = row["fk_wrong_target_count"]
            row["fk_wrong_child_columns_count"] = 0
            row["fk_relationship_error_count"] = 0

        # View stats
        if view_report.exists():
            try:
                statuses = []
                required_views_seen = set()
                row["view_required_count"] = 0
                row["view_expected_count"] = 0
                with open(view_report, "r", encoding="utf-8") as vf:
                    for v_row in csv.DictReader(vf):
                        answer_view = v_row.get("answer_view", "")
                        view_key = answer_view.strip().lower()
                        if view_key and view_key not in required_views_seen:
                            required_views_seen.add(view_key)
                            row["view_required_count"] += 1
                            row["view_expected_count"] += 1
                        v_status = v_row["status"].upper()
                        statuses.append(v_status)
                        
                        # Legacy column mapping
                        dest = _VIEW_STATUS_MAP.get(v_status)
                        if dest:
                            row[dest] += 1
                        elif "AMBIGUOUS" in v_status:
                            row["view_ambiguous_count"] += 1
                        elif "ERROR" in v_status:
                            row["view_execution_error_count"] += 1
                            
                        # New column mapping
                        if v_status in ("VIEW_OUTPUT_MATCH", "VIEW_PASS", "PASS"):
                            row["view_output_match_count"] += 1
                        elif v_status == "VIEW_OUTPUT_PARTIAL_MATCH":
                            row["view_partial_match_count"] += 1
                        elif v_status in ("VIEW_NO_MATCHING_OUTPUT", "VIEW_NOT_FOUND", "VIEW_NO_STUDENT_VIEWS"):
                            row["view_no_matching_output_count"] += 1
                        elif v_status in ("VIEW_SQL_PARSE_ERROR", "VIEW_SQL_REWRITE_UNMAPPED_TABLE", "VIEW_SQL_REWRITE_UNMAPPED_COLUMN", "VIEW_SQL_REWRITE_AMBIGUOUS_COLUMN", "VIEW_SQL_REWRITE_UNSUPPORTED_VIEW_DEPENDENCY", "VIEW_SQL_DEFINITION_MISSING"):
                            row["view_rewrite_error_count"] += 1
                        elif v_status in ("VIEW_SQL_UNSAFE_REVIEW", "VIEW_MAPPING_AMBIGUOUS", "VIEW_OUTPUT_SCHEMA_MISMATCH"):
                            row["view_review_required_count"] += 1
                            
                        if v_status == "VIEW_OUTPUT_SCHEMA_MISMATCH":
                            row["view_schema_mismatch_count"] += 1
                        elif v_status == "VIEW_ROW_COUNT_MISMATCH":
                            row["view_row_count_mismatch_count"] += 1
                        elif v_status == "VIEW_VALUE_MISMATCH":
                            row["view_value_mismatch_count"] += 1
                        elif v_status == "VIEW_ORDER_MISMATCH":
                            row["view_order_mismatch_count"] += 1
                            
                non_pass = [s for s in statuses if s not in ("VIEW_PASS", "PASS", "VIEW_OUTPUT_MATCH")]
                if non_pass:
                    if any("ERROR" in s for s in non_pass):
                        row["view_test_status"] = "ERROR"
                    else:
                        row["view_test_status"] = non_pass[0]
                else:
                    if statuses:
                        row["view_test_status"] = "OK"
            except Exception as e:
                logger.warning(f"Error reading view report for '{sub_id}': {e}")

        if row["view_missing_count"] > 0 and not row["view_test_status"]:
            row["view_test_status"] = "VIEW_NOT_FOUND"

        summary_rows.append(row)

    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_HEADERS)
        writer.writeheader()
        for row in summary_rows:
            writer.writerow(row)

    logger.info(f"Summary compiled and saved to: {summary_path}")
    return summary_path
