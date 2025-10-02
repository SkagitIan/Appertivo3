"""Management command to purge the JSON log file and sync it to Git."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Clear the application log file and push the update to Git."""

    help = "Purge logs/app-log.json and commit the change to the repository."

    def handle(self, *args, **options):  # type: ignore[override]
        log_path = Path(settings.APP_LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")

        repo_path = Path(settings.BASE_DIR)
        relative_log = log_path.relative_to(repo_path)

        self.stdout.write(f"Cleared {relative_log}")

        self._run_command(["git", "add", str(relative_log)], repo_path)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        commit_message = f"chore: purge daily logs {timestamp}"
        committed = self._run_command(
            ["git", "commit", "-m", commit_message], repo_path, allow_failure=True
        )

        if not committed:
            self.stdout.write(self.style.WARNING("No changes to commit."))
            return

        pushed = self._run_command(
            ["git", "push", "origin", "main"], repo_path, allow_failure=True
        )
        if pushed:
            self.stdout.write(self.style.SUCCESS("Pushed updated logs to origin/main."))
        else:
            self.stdout.write(
                self.style.WARNING("Unable to push logs to origin/main. Please verify remote configuration.")
            )

    def _run_command(self, command: list[str], cwd: Path, allow_failure: bool = False) -> bool:
        """Execute a subprocess command, optionally tolerating failures."""

        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
        if result.returncode == 0:
            if result.stdout.strip():
                self.stdout.write(result.stdout.strip())
            if result.stderr.strip():
                self.stdout.write(result.stderr.strip())
            return True

        if allow_failure:
            if result.stdout.strip():
                self.stdout.write(result.stdout.strip())
            if result.stderr.strip():
                self.stdout.write(result.stderr.strip())
            return False

        result.check_returncode()
        return False
