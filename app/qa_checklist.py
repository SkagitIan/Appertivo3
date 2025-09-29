"""Manual QA checklist definitions for the Django admin helper view."""

from __future__ import annotations

CHECKLIST_SECTIONS = [
    {
        "id": "public-marketing",
        "title": "Public marketing & lead funnels",
        "items": [
            {
                "id": "home-page",
                "label": "Home page – Verify the landing page renders demo concept/dish content and footer articles, and that signup/login entry points remain visible.",
                "url": "/",
            },
            {
                "id": "policy-pages",
                "label": "Privacy, terms, and contact pages – Confirm each static policy page loads with the shared footer content.",
                "url": "/privacy/",
            },
            {
                "id": "policy-pages-terms",
                "label": "Terms of Service page – Confirm terms view renders correctly with shared footer content.",
                "url": "/terms/",
            },
            {
                "id": "policy-pages-contact",
                "label": "Contact page – Confirm contact information appears with shared footer content.",
                "url": "/contact/",
            },
            {
                "id": "lead-microsite",
                "label": "Lead landing microsite – Test `/demo/<slug>/` renders the personalized concept and dish carousel for a lead, with `/demo/<slug>/track/` recording opens before redirecting.",
                "url": None,
            },
        ],
    },
    {
        "id": "authentication",
        "title": "Authentication & account creation",
        "items": [
            {
                "id": "signup-flow",
                "label": "Signup flow (HTML & JSON) – Exercise field validation, duplicate-email handling, automatic login, and Outscraper job kickoff for new restaurants.",
                "url": "/signup/",
            },
            {
                "id": "login-logout",
                "label": "Login & logout – Ensure credentials are required, successful login redirects to the user’s first restaurant dashboard, and logout returns to the login route.",
                "url": "/login/",
            },
            {
                "id": "api-signup",
                "label": "API signup alias – Confirm `/api/signup/` behaves the same as the primary signup endpoint.",
                "url": "/api/signup/",
            },
        ],
    },
    {
        "id": "onboarding",
        "title": "Onboarding & menu ingestion",
        "items": [
            {
                "id": "onboarding-workspace",
                "label": "Onboarding workspace – Validate subscription messaging, menu URL submission, queued scrape jobs, and dashboard eligibility indicators on `/onboarding/`.",
                "url": "/onboarding/",
            },
            {
                "id": "onboarding-status",
                "label": "Onboarding status API – Hit `/onboarding/status/` to confirm JSON reflects whether a subscription exists.",
                "url": "/onboarding/status/",
            },
            {
                "id": "manual-menu-capture",
                "label": "Manual menu capture – Test `/onboarding/manual_menu/` for text uploads, PDF ingestion, HTMX redirects, and error messaging when no content is provided.",
                "url": "/onboarding/manual_menu/",
            },
            {
                "id": "restaurant-status-widget",
                "label": "Restaurant status widget & modal – Check the HTMX status fragment, menu modal, and upload endpoint for updating ingestion state.",
                "url": None,
            },
        ],
    },
    {
        "id": "dashboard-menus",
        "title": "Dashboard, menus & search",
        "items": [
            {
                "id": "restaurant-dashboard",
                "label": "Restaurant dashboard – Review subscription banners, context checklist, recent concepts/dishes, menu summaries, and AI prompt helpers.",
                "url": "/dashboard/",
            },
            {
                "id": "context-toggle",
                "label": "Context toggle – Exercise the dashboard context inclusion toggles and ensure updated partials return over HTMX.",
                "url": None,
            },
            {
                "id": "dashboard-redirect",
                "label": "Dashboard redirect helper – Log in with multiple/no restaurants to confirm automatic routing.",
                "url": None,
            },
            {
                "id": "menus-workspace",
                "label": "Menus workspace – Inspect menu collections, collaboration links, feedback badges, and dish enhancement displays on `/menus/`.",
                "url": "/menus/",
            },
            {
                "id": "global-tag-search",
                "label": "Global tag search – Validate `/search/` returns relevant concepts and dishes filtered by tag across the account.",
                "url": "/search/",
            },
        ],
    },
    {
        "id": "concept-management",
        "title": "Concept management",
        "items": [
            {
                "id": "concept-gallery",
                "label": "Concept gallery – Confirm `/concepts/` shows newest concepts with favorite badges, slider metadata, and prompt shortcuts.",
                "url": "/concepts/",
            },
            {
                "id": "concept-generation",
                "label": "Concept generation – From `/concepts/generate/`, submit prompts, verify ideation runs, session history, and HTMX redirects when invoked from the dashboard.",
                "url": "/concepts/generate/",
            },
            {
                "id": "concept-favorites",
                "label": "Favorite toggles & lazy backgrounds – Test concept favoriting/unfavoriting, background sketch generation, and favorites-only view at `/concepts/favorites/`.",
                "url": "/concepts/favorites/",
            },
        ],
    },
    {
        "id": "dish-ideation",
        "title": "Dish ideation & curation",
        "items": [
            {
                "id": "dish-generation",
                "label": "Dish generation pipeline – Generate dishes for a concept, ensuring ideation runs, schema validation, and HTMX redirect to the detail view.",
                "url": None,
            },
            {
                "id": "dish-detail",
                "label": "Dish detail page – Verify grid/page rendering, favorite state, menu assignment options, and regenerate links at `/dishes/<concept_id>/`.",
                "url": None,
            },
            {
                "id": "dish-favorites",
                "label": "Dish favorites, deletion, and variations – Toggle favorites (with enhancement cleanup), delete dishes, and request AI variations, validating payload rules.",
                "url": None,
            },
        ],
    },
    {
        "id": "favorites-menus",
        "title": "Favorites hub & menu organization",
        "items": [
            {
                "id": "favorites-dashboard",
                "label": "Favorites dashboard – Review aggregated favorite concepts/dishes, menu assignments, uncategorized list, and move-to-menu controls.",
                "url": "/favorites/",
            },
            {
                "id": "remove-favorite",
                "label": "Remove favorite action – Confirm both concept and dish removal endpoints respond for HTMX and JSON callers.",
                "url": None,
            },
            {
                "id": "menu-crud",
                "label": "Menu CRUD & dish placement – Create, rename, delete collections; add dishes; and move dishes between menus or uncategorized state.",
                "url": None,
            },
            {
                "id": "collaboration-links",
                "label": "Collaboration link management – Enable, expire, passcode-protect, or disable public collaboration links for each menu.",
                "url": None,
            },
            {
                "id": "collaboration-portal",
                "label": "Public collaboration portal – Test passcode gating, activity feed, thumb counts, and feedback submission types (thumbs, comments, edits, reorders, new ideas).",
                "url": None,
            },
            {
                "id": "chef-feedback-review",
                "label": "Chef feedback review & actions – Validate pending/history lists and approval/rejection flows on `/menus/<uuid>/feedback/`.",
                "url": None,
            },
        ],
    },
    {
        "id": "settings",
        "title": "Settings & restaurant data management",
        "items": [
            {
                "id": "settings-overview",
                "label": "Settings overview – Ensure `/settings/` lists restaurant metadata, ingredients, notification prefs, and active menu info.",
                "url": "/settings/",
            },
            {
                "id": "restaurant-info-update",
                "label": "Restaurant info update – Exercise URL normalization, ingredient capture, and content uploads via `/settings/info/`.",
                "url": "/settings/info/",
            },
            {
                "id": "rescrape-controls",
                "label": "Rescrape controls – Trigger Outscraper and menu rescrape endpoints, verifying job scheduling safeguards.",
                "url": None,
            },
            {
                "id": "creativity-slider",
                "label": "Creativity slider & notifications – Update classic/creative slider values and toggle email preferences.",
                "url": None,
            },
        ],
    },
    {
        "id": "billing",
        "title": "Billing, subscriptions, and notifications",
        "items": [
            {
                "id": "billing-dashboard",
                "label": "Billing dashboard – Audit plan details, trial countdown, and call-to-action logic on `/billing/`.",
                "url": "/billing/",
            },
            {
                "id": "upgrade-cancellation",
                "label": "Upgrade & cancellation flows – Start Stripe Checkout sessions and cancel subscriptions at period end.",
                "url": None,
            },
            {
                "id": "stripe-webhook",
                "label": "Stripe webhook – Send representative webhook payloads to confirm subscription sync handling.",
                "url": None,
            },
            {
                "id": "job-status",
                "label": "Job status & notifications list – Hit `/jobs/<uuid>/` for JSON progress and review `/notifications/` UI rendering.",
                "url": None,
            },
        ],
    },
    {
        "id": "misc",
        "title": "Miscellaneous utilities",
        "items": [
            {
                "id": "restaurant-status-upload",
                "label": "Restaurant status & menu upload – Re-test status widget refresh after invoking `/restaurants/<uuid>/upload-menu/`.",
                "url": None,
            },
            {
                "id": "routes-inventory",
                "label": "Routes inventory – Double-check every path defined in `specials/urls.py` is reachable as listed above (including `/logout/`, `/favorites/`, `/menus/item/move/`, and `/collab/<uuid>/feedback/`).",
                "url": None,
            },
        ],
    },
]

__all__ = ["CHECKLIST_SECTIONS"]
