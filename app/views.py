"""Application views."""

import json

from django.contrib.auth.models import User
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from app import models


@csrf_exempt
@require_POST
def signup(request):
    """Handle user signup and initial restaurant creation."""
    data = json.loads(request.body.decode("utf-8"))
    email = data["email"]
    password = data["password"]
    restaurant_name = data["restaurant_name"]
    location = data["location"]
    menu_url = data.get("menu_url")

    with transaction.atomic():
        user = User.objects.create_user(username=email, email=email, password=password)
        models.UserProfile.objects.create(user=user)
        account = models.Account.objects.create(name=restaurant_name)
        models.Membership.objects.create(
            account=account, user=user, role=models.Membership.Role.OWNER
        )
        restaurant = models.Restaurant.objects.create(
            account=account,
            name=restaurant_name,
            location_text=location,
            primary_menu_url=menu_url,
        )
        if menu_url:
            models.OutscraperPayload.objects.create(
                restaurant=restaurant,
                status=models.OutscraperPayload.Status.QUEUED,
                request_params={"menu_url": menu_url},
                discovered_menu_url=menu_url,
            )

    return JsonResponse({"status": "queued"})
