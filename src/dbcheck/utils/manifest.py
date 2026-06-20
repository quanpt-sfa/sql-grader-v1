import csv
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from dbcheck.utils.logging import get_logger

class ManifestManager:
    FIELDS = [
        "submission_id",
        "source_path",
        "status",
        "error_code",
        "error_message",
        "temp_database",
        "started_at",
        "finished_at"
    ]

    def __init__(self, run_dir: Path):
        self.filepath = run_dir / "manifest.csv"
        self.logger = get_logger()
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        """Load existing entries from manifest.csv if it exists."""
        if not self.filepath.exists():
            return
            
        try:
            with open(self.filepath, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sub_id = row.get("submission_id")
                    if sub_id:
                        self.entries[sub_id] = row
        except Exception as e:
            self.logger.warning(f"Failed to load manifest.csv: {e}")

    def save(self) -> None:
        """Write current entries to manifest.csv."""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.FIELDS)
                writer.writeheader()
                # Sort entries by submission_id for clean layout
                for sub_id in sorted(self.entries.keys()):
                    writer.writerow(self.entries[sub_id])
        except Exception as e:
            self.logger.error(f"Failed to save manifest.csv: {e}")

    def update(
        self,
        submission_id: str,
        source_path: Path,
        status: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        temp_database: Optional[str] = None,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None
    ) -> None:
        """Add or update an entry in the manifest."""
        now_str = datetime.now().isoformat()
        
        # Get existing or init
        entry = self.entries.get(submission_id, {field: "" for field in self.FIELDS})
        
        entry["submission_id"] = submission_id
        entry["source_path"] = str(source_path)
        entry["status"] = status
        
        if error_code is not None:
            entry["error_code"] = error_code
        if error_message is not None:
            entry["error_message"] = error_message
        if temp_database is not None:
            entry["temp_database"] = temp_database
            
        if started_at:
            entry["started_at"] = started_at.isoformat()
        elif not entry["started_at"]:
            entry["started_at"] = now_str
            
        if finished_at:
            entry["finished_at"] = finished_at.isoformat()
        elif status in ["OK", "ERROR", "QUARANTINED", "SKIPPED"]:
            entry["finished_at"] = now_str

        self.entries[submission_id] = entry
        self.save()
