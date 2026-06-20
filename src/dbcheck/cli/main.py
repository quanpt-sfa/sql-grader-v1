import argparse
import sys
from pathlib import Path
from dbcheck.utils.logging import setup_logging, get_logger

def main():
    parser = argparse.ArgumentParser(
        description="SQL Server Schema & View Checker CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # Command: snapshot
    snap_parser = subparsers.add_parser("snapshot", help="Extract database structure snapshots")
    snap_group = snap_parser.add_mutually_exclusive_group(required=True)
    snap_group.add_argument("--answer-db", help="Existing protected answer database name")
    snap_group.add_argument("--answer-bak", help="Path to answer database backup file (.bak)")
    snap_parser.add_argument("--submissions", required=True, help="Folder containing student backup files")
    snap_parser.add_argument("--run-dir", required=True, help="Output runs directory")
    snap_parser.add_argument("--config", required=True, help="Path to configuration YAML file")

    # Command: compare-structure
    comp_parser = subparsers.add_parser("compare-structure", help="Compare student snapshots against answer snapshot")
    comp_parser.add_argument("--run-dir", required=True, help="Runs directory containing snapshots")
    comp_parser.add_argument("--config", required=True, help="Path to configuration YAML file")

    # Command: test-views
    view_parser = subparsers.add_parser("test-views", help="Test student view behavior against answer views")
    view_parser.add_argument("--run-dir", required=True, help="Runs directory containing snapshots")
    view_parser.add_argument("--test-data", required=True, help="Folder containing CSV/SQL test data files")
    view_parser.add_argument("--config", required=True, help="Path to configuration YAML file")
    view_parser.add_argument("--answer-bak", help="Optional path to answer database backup file (.bak)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Set up runs folder logging if run-dir is present
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(run_dir / "execution.log")

    logger.info(f"Starting command: {args.command}")

    try:
        if args.command == "snapshot":
            from dbcheck.cli.snapshot import run_snapshot
            run_snapshot(args)
        elif args.command == "compare-structure":
            from dbcheck.cli.compare_structure import run_compare_structure
            run_compare_structure(args)
        elif args.command == "test-views":
            from dbcheck.cli.test_views import run_test_views
            run_test_views(args)
        logger.info(f"Successfully completed command: {args.command}")
    except Exception as e:
        logger.exception(f"Command '{args.command}' failed with error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
