"""Test configuration helpers."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "specials.settings")

import django
from django.core.management import call_command

django.setup()
call_command("migrate", run_syncdb=True, interactive=False, verbosity=0)
