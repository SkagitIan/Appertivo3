"""Celery tasks for the post-payment onboarding pipeline."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Callable
from urllib.parse import urlparse
from uuid import UUID
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app import llm, models
from onboarding.services import menu as menu_service
from onboarding.services import outscraper as outscraper_service
from onboarding.services import web_profile as web_profile_service
from specials.celery import app

logger = logging.getLogger(__name__)

