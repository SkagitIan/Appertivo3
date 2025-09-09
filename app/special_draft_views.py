"""Views for the SpecialDraft modal wizard."""
from django.http import Http404, JsonResponse, HttpResponseRedirect
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from app.ai import *
from app.models import SpecialDraft


def get_concepts_for_today(request):
    """Fetches concepts and renders a template."""
    # Get the JsonResponse object from the function
    concepts_response = get_special_concepts()

    # Get the JSON content as a string
    json_string = concepts_response.content.decode('utf-8')

    # Convert the JSON string to a Python dictionary
    concepts_data = json.loads(json_string)

    # Pass the 'names' list from the dictionary to the template context
    context = {
        'concepts': concepts_data['names']
    }
    
    return render(request, 'app/special_draft/_step0_modal.html', context)

def generate_special_ideas(concept: str):
    """Stub idea generator."""
    return [f"{concept} Idea {i}" for i in range(1, 11)]

def special_draft_step(request, step: int):
    if step not in {0, 1, 2, 3, 4}:
        raise Http404
    template = f"app/special_draft/_step{step}_modal.html"
    context = {}
    if step == 0:
        context["concepts"] = get_concepts_for_today()
    return render(request, template, context)

def special_draft_ideas(request):
    concept = request.GET.get("concept", "")
    ideas = generate_special_ideas(concept)
    return JsonResponse({"ideas": ideas})

def special_draft_select(request, draft_id: int):
    if request.method != "POST":
        raise Http404
    concept = request.POST.get("concept", "")
    idea = request.POST.get("idea", "")
    draft = get_object_or_404(SpecialDraft, id=draft_id)
    draft.concept = concept
    draft.title = idea
    draft.description_user = idea
    draft.current_step = 1
    draft.save()
    return HttpResponseRedirect(reverse("special_draft_step", args=[1]))
