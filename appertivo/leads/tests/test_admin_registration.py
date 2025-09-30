"""Tests for the Django admin registrations in the leads app."""

from importlib import import_module, reload

import django
from django.apps import apps
from django.contrib import admin
from django.test import SimpleTestCase

if not apps.ready:  # pragma: no cover - guard for pytest without django.setup()
    django.setup()

from appertivo.leads.models import Concept, DishIdea, Lead


class LeadAdminRegistrationTests(SimpleTestCase):
    """Ensure admin registrations remain stable across reloads."""

    def test_admin_module_can_be_reloaded(self):
        """Reloading the admin module should not raise AlreadyRegistered."""

        module = import_module("appertivo.leads.admin")
        self.assertTrue(admin.site.is_registered(Lead))
        self.assertTrue(admin.site.is_registered(Concept))
        self.assertTrue(admin.site.is_registered(DishIdea))

        # A second import previously raised AlreadyRegistered. Reload to confirm stability.
        reload(module)

        self.assertTrue(admin.site.is_registered(Lead))
        self.assertTrue(admin.site.is_registered(Concept))
        self.assertTrue(admin.site.is_registered(DishIdea))
