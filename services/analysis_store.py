"""
JSON-file-backed store for async analysis jobs.

Each job is keyed by UUID and persisted to data/analyses.json.
Thread-safe via threading.Lock (works across the main event loop and background threads).
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Optional


class AnalysisStore:
    def __init__(self, file_path: Optional[str] = None):
        if file_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            file_path = os.path.join(base_dir, "data", "analyses.json")
        self._file_path = file_path
        self._lock = threading.Lock()
        # Ensure the directory exists
        os.makedirs(os.path.dirname(self._file_path), exist_ok=True)

    def _read_all(self) -> dict:
        if not os.path.exists(self._file_path):
            return {}
        with open(self._file_path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}

    def _write_all(self, data: dict) -> None:
        with open(self._file_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def create(self, analysis_id: str, query: str, url: str) -> dict:
        """Create a new in_progress analysis entry."""
        entry = {
            "id": analysis_id,
            "status": "in_progress",
            "query": query,
            "url": url,
            "analysis": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }
        with self._lock:
            data = self._read_all()
            data[analysis_id] = entry
            self._write_all(data)
        return entry

    def update(
        self,
        analysis_id: str,
        status: str,
        analysis: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update an existing analysis entry with status, result, or error."""
        with self._lock:
            data = self._read_all()
            if analysis_id not in data:
                return
            data[analysis_id]["status"] = status
            if analysis is not None:
                data[analysis_id]["analysis"] = analysis
            if error is not None:
                data[analysis_id]["error"] = error
            if status in ("completed", "failed"):
                data[analysis_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
            self._write_all(data)

    def get(self, analysis_id: str) -> Optional[dict]:
        """Retrieve a single analysis entry by ID, or None if not found."""
        with self._lock:
            data = self._read_all()
            return data.get(analysis_id)
