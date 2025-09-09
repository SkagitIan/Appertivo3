import datetime
import json
import os
from dotenv import load_dotenv
load_dotenv()  # take environment variables
from django.http import JsonResponse
import logging
logger = logging.getLogger(__name__)
from django.template.loader import render_to_string
from typing import List
from openai import OpenAI
from pydantic import BaseModel, Field, constr
from django.http import HttpResponse, HttpResponseBadRequest

class SpecialConcept(BaseModel):
    name: str

class NamesList(BaseModel):
    names: list[SpecialConcept] = Field(..., description="A list of 9 names")

class MenuIdea(BaseModel):
    title: constr(min_length=1, max_length=80)
    description: constr(min_length=1, max_length=240)
    tags: List[constr(min_length=1, max_length=24)] = []

class IdeasList(BaseModel):
    concept: constr(min_length=1, max_length=120)
    ideas: List[MenuIdea]

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))


def get_special_concepts():
    response = client.responses.parse(
        model="gpt-4o",
        input=[
            {
                "role": "user",
                "content": "Create exactly 9 unique concepts for my restaurant specials for today, Monday September 8, 2025. Do not list specific dishes—focus on broad themes or event-style ideas (e.g., “Taco Tuesday,” “Game Night,” “Family Pack,” “Seasonal Harvest Dinner”). Consider the date, seasonality, and the Pacific Northwest location. Return only the 9 concept names as plain text, with no numbering, bullets, or extra formatting.",
            },
        ],
        text_format=NamesList,
    )
    
    # .model_dump() on the NamesList object will produce the correct dictionary
    data_to_serialize = response.output_parsed.model_dump()
    logger.info(data_to_serialize)
    return JsonResponse(data_to_serialize, safe=False)

def get_concept_ideas(request):
    """
    HTMX endpoint. Expects ?concept=<name>
    Returns rendered HTML partial (ideas list).
    """
    concept = request.GET.get("concept")
    if not concept:
        return HttpResponseBadRequest("Missing 'concept' parameter.")

    # System/user prompts tuned for crisp, parsable output
    system_msg = (
        "You are a seasoned restaurant menu developer. "
        "Generate on-brand, saleable menu ideas for daily specials. "
        "Always return exactly 10 ideas tailored to the concept, "
        "Pacific Northwest seasonality, and everyday kitchen practicality. "
        "Avoid price points and avoid alcohol. Keep descriptions one sentence."
    )
    user_msg = (
        f"Concept: {concept}\n"
        "Return 10 distinct menu ideas with fields: title, description, and 1–3 short tags."
    )

    # Parse directly into our Pydantic schema for safety
    # If your SDK version supports `.responses.parse` this is ideal.
    try:
        resp = client.responses.parse(
            model="gpt-4o",
            input=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            text_format=IdeasList,  # <- maps to our schema
        )
        ideas_obj: IdeasList = resp.output_parsed
    except Exception as e:
        logger.exception("AI parsing failed for concept='%s': %s", concept, e)
        return HttpResponse(
            "<div class='p-4 rounded bg-red-50 text-red-700'>"
            "Sorry—couldn’t generate ideas just now. Try again.</div>"
        )

    html = render_to_string(
        "app/partials/ideas_list.html",
        {"ideas": ideas_obj.ideas, "concept": ideas_obj.concept},
    )
    return HttpResponse(html)


def enhance_special_content(special):
    """Use OpenAI to enhance textual content for a Special.

    Sends the current title, description, price, start_date, and end_date
    to the OpenAI API and updates the instance with any returned values.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return special
    client = OpenAI(api_key=api_key)
    prompt = (
        "Enhance the following restaurant special. "
        "Return JSON with keys: title, description, price, start_date, end_date.\n"
        f"Title: {special.title}\n"
        f"Description: {special.description}\n"
        f"Price: {special.price}\n"
        f"Start Date: {special.start_date}\n"
        f"End Date: {special.end_date}"
    )
    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        text={"format": {"type": "json_object"}}
    )
    print("OpenAI response:", response )
    try:
        content = response.output[0].content[0].text
        print(content)
        data = json.loads(content)
    except Exception:
        return special
    for field in ["title", "description", "price", "start_date", "end_date"]:
        value = data.get(field)
        if not value:
            continue
        if field in {"start_date", "end_date"}:
            try:
                value = datetime.date.fromisoformat(value)
            except Exception:
                continue
        setattr(special, field, value)
    special.save()
    return special
