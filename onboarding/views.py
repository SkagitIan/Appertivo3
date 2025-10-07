"""Views for the onboarding pipeline."""

from __future__ import annotations

import json
import logging

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from app import models
from dotenv import load_dotenv
load_dotenv()
logger = logging.getLogger(__name__)

