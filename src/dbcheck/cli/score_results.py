import csv
import logging
from pathlib import Path
from dbcheck.config import load_config
from dbcheck.utils.logging import get_logger
from dbcheck.utils.scoring import (
    load_rubric, load_overrides, get_answer_atomic_items,
    score_submission, write_xlsx_report
)

logger = logging.getLogger("dbcheck")

def run_score_results(args):
    """Execution endpoint for the score-results CLI command."""
    logger.info("Initializing score-results execution...")
    
    # 1. Paths
    run_dir = Path(args.run_dir)
    config_path = Path(args.config)
    rubric_path = Path(args.rubric)
    overrides_path = Path(args.overrides) if args.overrides else None
    
    manifest_path = run_dir / "manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.csv not found in {run_dir}. Please run snapshot first.")
        
    # 2. Load and validate config & rubric
    config = load_config(str(config_path))
    rubric = load_rubric(rubric_path)
    overrides = load_overrides(overrides_path)
    
    # Validation: show total points and warn if not equal to 10
    total_rubric_points = sum(row["total_points"] for row in rubric)
    logger.info(f"Loaded rubric contains {len(rubric)} items with total configured points: {total_rubric_points:.2f}")
    if total_rubric_points != 10.0:
        logger.warning(f"Total rubric points ({total_rubric_points:.2f}) is not equal to 10.0. Please verify the exam specification.")
        
    # 3. Compile ground-truth atomic items
    answer_items = get_answer_atomic_items(run_dir, config)
    
    # 4. Load submissions from manifest
    submissions = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            submissions.append(row)
            
    # 5. Score each submission
    summary_rows = []
    detail_rows = []
    
    for sub in submissions:
        sub_id = sub["submission_id"]
        manifest_status = sub["status"]
        
        logger.info(f"Scoring submission: {sub_id} (status: {manifest_status})...")
        details, final_score, rev_count, err_count = score_submission(
            sub_id, manifest_status, run_dir, config, rubric, overrides, answer_items
        )
        
        # Calculate auto_score (points before overrides)
        auto_score = sum(d["original_points_awarded"] for d in details)
        
        summary_rows.append({
            "submission_id": sub_id,
            "manifest_status": manifest_status,
            "auto_score": round(auto_score, 4),
            "final_score": round(final_score, 4),
            "review_required_count": rev_count,
            "hard_error_count": err_count
        })
        
        for d in details:
            # Round the float values for display
            d["points_possible"] = round(d["points_possible"], 4)
            d["original_points_awarded"] = round(d["original_points_awarded"], 4)
            d["final_points_awarded"] = round(d["final_points_awarded"], 4)
            detail_rows.append(d)
            
    # 6. Save reports
    # A. Copy / save rubric to run_dir
    rubric_dest = run_dir / "grading_rubric.csv"
    with open(rubric_dest, "w", newline="", encoding="utf-8") as f:
        headers = ["section", "component", "scope", "object_name", "total_points", "scoring_mode", "include_statuses", "partial_policy", "notes"]
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rubric:
            out_row = {k: row.get(k, "") for k in headers}
            writer.writerow(out_row)
            
    # B. Write grading_detail.csv
    detail_dest = run_dir / "grading_detail.csv"
    with open(detail_dest, "w", newline="", encoding="utf-8") as f:
        headers = [
            "submission_id", "section", "component", "answer_object", "student_object", "status",
            "points_possible", "original_points_awarded", "final_points_awarded", "review_required",
            "override_applied", "reviewer_note", "source_report", "message"
        ]
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in detail_rows:
            out_row = {k: row.get(k, "") for k in headers}
            writer.writerow(out_row)
            
    # C. Write grading_summary.csv
    summary_dest = run_dir / "grading_summary.csv"
    with open(summary_dest, "w", newline="", encoding="utf-8") as f:
        headers = ["submission_id", "manifest_status", "auto_score", "final_score", "review_required_count", "hard_error_count"]
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in summary_rows:
            writer.writerow(row)
            
    # D. Write grading_summary.xlsx
    write_xlsx_report(run_dir, summary_rows, detail_rows, rubric, overrides)
    
    logger.info("Scoring results completed successfully.")
