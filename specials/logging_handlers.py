"""Custom logging handlers for the Appertivo project."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DailyJsonFileHandler(logging.Handler):
    """Write structured log records to a JSON lines file, resetting each day."""

    def __init__(self, filename: str) -> None:
        super().__init__()
        self.log_path = Path(filename)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._current_date = None

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - exercised via integration
        """Write the log record as JSON, rotating when the UTC date changes."""

        try:
            now = datetime.now(timezone.utc)
            if self._current_date != now.date():
                self._current_date = now.date()
                self.log_path.write_text("", encoding="utf-8")

            payload: dict[str, Any] = {
                "timestamp": now.isoformat().replace("+00:00", "Z"),
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
            }
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:  # pragma: no cover - defensive guard
            self.handleError(record)
