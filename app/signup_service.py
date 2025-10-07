"""Signup bootstrap helpers for Appertivo."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import transaction

from . import models

logger = logging.getLogger(__name__)

_SIGNER = TimestampSigner()


@dataclass
class SignupResult:
    """Container for the records created during signup."""

    user: AbstractBaseUser
    account: models.Account
    restaurant: models.Restaurant
    activation_token: str
    onboarding: models.onboarding


def start_signup(*, email: str, password: str, restaurant_name: str, location: str) -> SignupResult:
    """Create a user, account, and restaurant for a new signup."""

    User = get_user_model()
    with transaction.atomic():
        user = User.objects.create_user(username=email, email=email, password=password)
        models.UserProfile.objects.create(user=user)
        account = models.Account.objects.create(name=restaurant_name)
        models.Membership.objects.create(account=account, user=user, role=models.Membership.Role.OWNER)
        restaurant = models.Restaurant.objects.create(account=account,name=restaurant_name,location_text=location)
        activation_token = generate_activation_token(str(user.id))
        onboarding = models.Onboarding.objects.create(user=user,restaurant=restaurant,activation_token=activation_token)
        

    logger.info(
        "Signup created", extra={"user": str(user.id), "account": str(account.id)}
    )

    return SignupResult(
        user=user,
        account=account,
        restaurant=restaurant,
        activation_token=activation_token,
        onboarding=onboarding,    )


def generate_activation_token(user_id: str) -> str:
    """Return a signed activation token for the provided user id."""

    return _SIGNER.sign(str(user_id))


def verify_activation_token(token: str, *, max_age: int = 60 * 60 * 24) -> Optional[str]:
    """Return the user id embedded in the activation token if valid."""

    try:
        value = _SIGNER.unsign(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    return str(value)


def sign_restaurant_token(restaurant_id: uuid.UUID) -> str:
    """Sign a webhook token for the given restaurant id."""

    return _SIGNER.sign(str(restaurant_id))


def verify_restaurant_token(token: str, restaurant_id: uuid.UUID) -> bool:
    """Validate a restaurant webhook token."""

    try:
        value = _SIGNER.unsign(token, max_age=60 * 60 * 24)
    except (BadSignature, SignatureExpired):
        return False
    return str(value) == str(restaurant_id)
