"""Supporting views that live inside the custom admin site."""

from django.template.response import TemplateResponse

from .admin_site import appertivo_admin_site
from .qa_checklist import CHECKLIST_SECTIONS


def manual_testing_checklist_view(request):
    """Render the manual QA checklist inside the Django admin."""

    context = appertivo_admin_site.each_context(request)
    context.update(
        {
            "title": "Manual QA Checklist",
            "checklist_sections": CHECKLIST_SECTIONS,
            "checklist_storage_key": "manual-qa-checklist-v1",
        }
    )
    return TemplateResponse(request, "admin/testing_checklist.html", context)

