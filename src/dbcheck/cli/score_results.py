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
    
    import hashlib
    import json
    from datetime import datetime
    REPO_ROOT = Path(__file__).resolve().parents[3]
    
    # 1. Paths
    run_dir = Path(args.run_dir)
    config_path = Path(args.config)
    
    manifest_path = run_dir / "manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.csv not found in {run_dir}. Please run snapshot first.")
        
    # 2. Load and validate config
    config = load_config(str(config_path))
    
    # Resolve rubric path
    rubric_source_path = None
    if args.rubric:
        rubric_path = Path(args.rubric)
        rubric_source_path = rubric_path
    elif getattr(config, "scoring", None) and config.scoring.rubric_path:
        rubric_path = Path(config.scoring.rubric_path)
        if not rubric_path.is_absolute():
            rubric_path = REPO_ROOT / rubric_path
        rubric_source_path = rubric_path
    else:
        # Fall back to run-level rubric for backward compatibility
        rubric_path = run_dir / "grading_rubric.csv"
        if rubric_path.exists():
            logger.warning(f"No rubric path specified. Using legacy run-level rubric: {rubric_path}")
            rubric_source_path = rubric_path
        else:
            raise FileNotFoundError(
                "No rubric file specified. Please set scoring.rubric_path in your config file, "
                "pass --rubric to the command line, or place grading_rubric.csv in the run directory."
            )
            
    # Resolve overrides path
    manual_overrides_filename = "manual_overrides.csv"
    if getattr(config, "scoring", None) and config.scoring.manual_overrides_filename:
        manual_overrides_filename = config.scoring.manual_overrides_filename
        
    overrides_path = Path(args.overrides) if args.overrides else None
    if not overrides_path:
        run_overrides = run_dir / manual_overrides_filename
        if run_overrides.exists():
            overrides_path = run_overrides
            
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
    run_rubric_filename = "rubric_used.csv"
    copy_rubric_to_run = True
    if getattr(config, "scoring", None):
        if config.scoring.run_rubric_filename:
            run_rubric_filename = config.scoring.run_rubric_filename
        copy_rubric_to_run = config.scoring.copy_rubric_to_run
        
    rubric_sha256 = ""
    if rubric_path.exists():
        sha256_hash = hashlib.sha256()
        with open(rubric_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        rubric_sha256 = sha256_hash.hexdigest()
        
    rubric_used_dest = None
    if copy_rubric_to_run:
        rubric_used_dest = run_dir / run_rubric_filename
        with open(rubric_used_dest, "w", newline="", encoding="utf-8") as f:
            headers = ["section", "component", "scope", "object_name", "total_points", "scoring_mode", "include_statuses", "partial_policy", "notes"]
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rubric:
                out_row = {k: row.get(k, "") for k in headers}
                writer.writerow(out_row)
                
        sha_filename = Path(run_rubric_filename).stem + ".sha256"
        sha_dest = run_dir / sha_filename
        with open(sha_dest, "w", encoding="utf-8") as f:
            f.write(rubric_sha256)
    else:
        logger.info("Scoring config has copy_rubric_to_run=False. Rubric will not be copied to run directory.")
        
    # Write scoring_metadata.json
    metadata = {
        "rubric_source_path": str(rubric_source_path) if rubric_source_path else "",
        "rubric_used_path": str(rubric_used_dest) if rubric_used_dest else "",
        "rubric_sha256": rubric_sha256,
        "manual_overrides_path": str(overrides_path) if overrides_path else "",
        "scored_at": datetime.now().isoformat()
    }
    metadata_dest = run_dir / "scoring_metadata.json"
    with open(metadata_dest, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)
        
    # Write duplicate grading_rubric.csv for backward compatibility
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
