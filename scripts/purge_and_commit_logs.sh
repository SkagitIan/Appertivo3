#!/bin/bash
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$APP_DIR/logs/app-log.json"

cd "$APP_DIR"

: > "$LOG_FILE"

git add "logs/app-log.json"
git commit -m "chore: purge daily logs $(date -u +'%Y-%m-%d %H:%M:%S UTC')" || echo "No changes to commit"
git push origin main || echo "Unable to push logs"
