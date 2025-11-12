"""Celery application for the project."""
import os
from celery import Celery
from celery.schedules import crontab
from kombu import Queue   # ← needed for proper queue declaration

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "specials.settings")

app = Celery("specials")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# ---------------------------------------------------------------------
# Scheduled tasks (beat)
# ---------------------------------------------------------------------
app.conf.beat_schedule = {
    "seed-places-weekly": {
        "task": "gastronet.tasks.seed_places",
        "schedule": crontab(hour=3, minute=0, day_of_week="mon"),
        "args": ("restaurants in Seattle, WA", 500),
    },
    "refresh-schedule-daily": {
        "task": "gastronet.tasks.schedule_refresh",
        "schedule": crontab(hour=3, minute=15),
    },
    "fetch-reviews-daily": {
        "task": "gastronet.tasks.fetch_reviews",
        "schedule": crontab(hour=3, minute=30),
        "args": (60, 10),
    },
    "heartbeat": {
        "task": "gastronet.tasks.heartbeat",
        "schedule": crontab(minute="*/30"),
    },
}

# ---------------------------------------------------------------------
# Queue definitions
# ---------------------------------------------------------------------
app.conf.task_queues = (
    Queue("default"),   # lightweight workers
    Queue("render"),    # heavy headless Playwright workers
)

# Optionally: set default route
app.conf.task_default_queue = "default"
app.conf.task_default_exchange = "default"
app.conf.task_default_routing_key = "default"

# ---------------------------------------------------------------------
# (optional) Routes for specific heavy tasks
# ---------------------------------------------------------------------
app.conf.task_routes = {
    "gastronet.tasks_render.render_menu_page": {"queue": "render"},
}
