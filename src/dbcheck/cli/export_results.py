from pathlib import Path
from dbcheck.config import load_config
from dbcheck.utils.summary import compile_summary
from dbcheck.utils.logging import get_logger
from dbcheck.utils.exporter import export_results

def run_export_results(args):
    logger = get_logger()
    
    # 1. Load config
    config = load_config(args.config)
    logger.info(f"Loaded config: {config.name}")
    
    # 2. Paths
    run_dir = Path(args.run_dir)
    manifest_path = run_dir / "manifest.csv"
    
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.csv not found in: {run_dir}. Please run snapshot/compare-structure first.")
        
    # 3. Ensure base summary is compiled
    logger.info("Compiling/updating base summary metrics...")
    compile_summary(run_dir)
    
    # 4. Run export logic
    logger.info(f"Aggregating reports and exporting results to {args.format.upper()}...")
    export_results(run_dir, config, output_format=args.format)
    
    logger.info("Export results command completed successfully.")
