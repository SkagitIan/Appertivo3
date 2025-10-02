"""Tests covering the custom logging handlers."""

from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase

from specials.logging_handlers import DailyJsonFileHandler


class DailyJsonFileHandlerTests(SimpleTestCase):
    """Validate the custom JSON logging handler behaviour."""

    def test_handler_writes_to_env_override(self) -> None:
        """Environment overrides should determine the log output path."""

        with TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "nested" / "app-log.json"
            with mock.patch.dict(os.environ, {"APP_LOG_FILE": str(log_path)}, clear=False):
                handler = DailyJsonFileHandler(os.environ["APP_LOG_FILE"])
                record = logging.LogRecord(
                    name="appertivo.tests",  # pragma: no branch - deterministic name for record logger
                    level=logging.INFO,
                    pathname=__file__,
                    lineno=0,
                    msg="json handler env override",
                    args=(),
                    exc_info=None,
                )

                handler.emit(record)

            self.assertTrue(log_path.exists())
            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["message"], "json handler env override")

    def test_handler_degrades_when_path_unwritable(self) -> None:
        """The handler should fall back to console logging when it cannot write."""

        logger = logging.getLogger("appertivo.tests.logging")
        original_handlers = list(logger.handlers)
        original_level = logger.level
        original_propagate = logger.propagate
        fallback_stream = io.StringIO()
        fallback_handler = logging.StreamHandler(fallback_stream)

        logger.handlers = []
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(fallback_handler)

        with TemporaryDirectory() as tmp_dir:
            blocked = Path(tmp_dir) / "blocked"
            blocked.write_text("not a directory", encoding="utf-8")
            handler = DailyJsonFileHandler(str(blocked / "app-log.json"))
            logger.addHandler(handler)

            try:
                logger.info("first degraded message")
                logger.info("second degraded message")
            finally:
                logger.removeHandler(handler)
                handler.close()

        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate

        output_lines = [line for line in fallback_stream.getvalue().splitlines() if line]
        warning_lines = [line for line in output_lines if "Unable to write app logs" in line]
        self.assertEqual(len(warning_lines), 1)
        self.assertIn("first degraded message", output_lines)
        self.assertIn("second degraded message", output_lines)
