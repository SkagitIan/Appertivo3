"""Signals that connect leads to real restaurants."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from app.models import Account, Membership, Restaurant

from .models import Lead

User = get_user_model()


@receiver(post_save, sender=User)
def convert_lead_to_restaurant(sender, instance, created, **kwargs) -> None:
    """When a user signs up, convert the matching lead into a restaurant."""

    if not created or not instance.email:
        return

    with transaction.atomic():
        try:
            lead = Lead.objects.select_for_update().get(email__iexact=instance.email, converted=False)
        except Lead.DoesNotExist:
            return

        account = Account.objects.create(name=lead.name)
        Membership.objects.create(account=account, user=instance, role=Membership.Role.OWNER)
        restaurant = Restaurant.objects.create(
            account=account,
            name=lead.name,
            location_text=lead.city or lead.json_data.get("formatted_address", ""),
            phone=lead.phone,
            about_json=lead.json_data,
            context_json=lead.json_data,
        )
        lead.restaurant = restaurant
        lead.converted = True
        lead.save(update_fields=["restaurant", "converted"])
