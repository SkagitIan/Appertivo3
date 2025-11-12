"""Custom logging handlers for the Appertivo project."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import SysLogHandler
from pathlib import Path
from typing import Any


def _first_non_matching_handler(
    *,
    record: logging.LogRecord,
    current: logging.Handler,
) -> logging.Handler | None:
    """Return the first handler on the originating or root logger that isn't the current one."""

    logger = logging.getLogger(record.name)
    for handler in logger.handlers:
        if handler is not current:
            return handler

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if handler is not current:
            return handler

    return None


class DailyJsonFileHandler(logging.Handler):
    """Write structured log records to a JSON lines file, appending entries."""

    def __init__(self, filename: str) -> None:
        super().__init__()
        self.log_path = Path(filename)
        self._fallback_handler: logging.Handler | None = None
        self._warning_logged = False
        self._use_fallback = False

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - exercised via integration
        """Write the log record as JSON."""

        try:
            if self._use_fallback:
                self._emit_via_fallback(record)
                return

            self._write_record(record)
        except OSError as error:  # pragma: no cover - exercised via tests
            self._handle_unwritable(record, error)
        except Exception:  # pragma: no cover - defensive guard
            self.handleError(record)

    def _write_record(self, record: logging.LogRecord) -> None:
        """Write a single record to disk, ensuring the log file exists."""

        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "timestamp": now.isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _emit_via_fallback(self, record: logging.LogRecord) -> None:
        fallback = self._fallback_handler
        if fallback is None:
            fallback = _first_non_matching_handler(record=record, current=self)
            self._fallback_handler = fallback

        if fallback is not None:
            fallback.handle(record)
        else:  # pragma: no cover - defensive guard
            self.handleError(record)

    def _handle_unwritable(self, record: logging.LogRecord, error: OSError) -> None:
        """Switch to a console fallback when writing fails."""

        self._fallback_handler = _first_non_matching_handler(record=record, current=self)
        if self._fallback_handler is None:
            self.handleError(record)
            return

        if not self._warning_logged:
            warning_record = logging.LogRecord(
                name=__name__,
                level=logging.WARNING,
                pathname=__file__,
                lineno=0,
                msg=(
                    "Unable to write app logs to %s (%s). Falling back to console output."
                ),
                args=(str(self.log_path), error),
                exc_info=None,
            )
            self._fallback_handler.handle(warning_record)
            self._warning_logged = True

        self._use_fallback = True
        self._emit_via_fallback(record)


class SystemLogHandler(logging.Handler):
    """Mirror log records to the system logger when available."""

    DEFAULT_ADDRESSES: tuple[str | tuple[str, int], ...] = (
        "/dev/log",           # Linux
        "/var/run/syslog",    # macOS
        ("localhost", 514),   # UDP fallback
    )

    def __init__(self, address: str | tuple[str, int] | None = None) -> None:
        super().__init__()
        env_address = os.getenv("SYSLOG_ADDRESS")
        self._addresses: list[str | tuple[str, int]] = []
        for candidate in (address, env_address):
            if candidate:
                self._addresses.append(candidate)
        self._addresses.extend(self.DEFAULT_ADDRESSES)
        self._handler = self._connect()

    def _connect(self) -> SysLogHandler | None:
        for addr in self._addresses:
            try:
                return SysLogHandler(address=addr)
            except OSError:
                continue
        return None

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - integration behaviour
        if self._handler is None:
            return
        try:
            self._handler.emit(record)
        except OSError:
            self._handler = self._connect()
