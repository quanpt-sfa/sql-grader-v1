import csv
from pathlib import Path
from dbcheck.config import load_config
from dbcheck.structure.structure_reporter import run_structure_comparison
from dbcheck.utils.summary import compile_summary
from dbcheck.utils.logging import get_logger

def run_compare_structure(args):
    logger = get_logger()
    
    # 1. Load config
    config = load_config(args.config)
    logger.info(f"Loaded config: {config.name}")
    
    # 2. Paths
    run_dir = Path(args.run_dir)
    manifest_path = run_dir / "manifest.csv"
    
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.csv not found in: {run_dir}. Please run snapshot first.")
        
    answer_dir = run_dir / "answer_snapshot"
    if not answer_dir.exists():
        raise FileNotFoundError(f"Answer snapshot folder not found at: {answer_dir}")
        
    # 3. Read submissions from manifest
    submissions = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            submissions.append(row)
            
    ok_count = 0
    total_count = len(submissions)
    
    for sub in submissions:
        sub_id = sub["submission_id"]
        status = sub["status"]
        
        if status != "OK":
            logger.info(f"Skipping structure comparison for '{sub_id}' because snapshot status is '{status}'")
            continue
            
        student_dir = run_dir / "submissions" / sub_id / "snapshot"
        report_path = run_dir / "submissions" / sub_id / "reports" / "structure_report.csv"
        
        if not student_dir.exists():
            logger.warning(f"Snapshot directory missing for '{sub_id}' even though status is 'OK'. Skipping.")
            continue
            
        logger.info(f"Comparing structure for student '{sub_id}'...")
        try:
            run_structure_comparison(answer_dir, student_dir, report_path, config)
            ok_count += 1
        except Exception as e:
            logger.error(f"Failed to compare structure for student '{sub_id}': {e}")
            
    logger.info(f"Structure comparison complete. Processed {ok_count}/{total_count} submissions.")
    
    # 4. Generate summary.csv
    compile_summary(run_dir)
