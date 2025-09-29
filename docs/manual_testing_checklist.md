# Manual QA Feature Checklist

This checklist enumerates every page and major feature exposed by the current Django app so a human tester can cover the full experience end to end.

## Public marketing & lead funnels
- **Home page** – Verify the landing page renders demo concept/dish content and footer articles, and that signup/login entry points remain visible. 【F:app/views.py†L410-L430】
- **Privacy, terms, and contact pages** – Confirm each static policy page loads with the shared footer content. 【F:app/views.py†L433-L451】
- **Lead landing microsite** – Test `/demo/<slug>/` renders the personalized concept and dish carousel for a lead, with `/demo/<slug>/track/` recording opens before redirecting. 【F:appertivo/leads/views.py†L10-L31】

## Authentication & account creation
- **Signup flow (HTML & JSON)** – Exercise field validation, duplicate-email handling, automatic login, and Outscraper job kickoff for new restaurants. 【F:app/views.py†L454-L551】
- **Login & logout** – Ensure credentials are required, successful login redirects to the user’s first restaurant dashboard, and logout returns to the login route. 【F:app/views.py†L556-L593】
- **API signup alias** – Confirm `/api/signup/` behaves the same as the primary signup endpoint. 【F:specials/urls.py†L7-L76】【F:app/views.py†L454-L551】

## Onboarding & menu ingestion
- **Onboarding workspace** – Validate subscription messaging, menu URL submission, queued scrape jobs, and dashboard eligibility indicators on `/onboarding/`. 【F:app/views.py†L889-L995】
- **Onboarding status API** – Hit `/onboarding/status/` to confirm JSON reflects whether a subscription exists. 【F:app/views.py†L998-L1016】
- **Manual menu capture** – Test `/onboarding/manual_menu/` for text uploads, PDF ingestion, HX redirects, and error messaging when no content is provided. 【F:app/views.py†L1019-L1058】
- **Restaurant status widget & modal** – Check the HTMX status fragment, menu modal, and upload endpoint for updating ingestion state. 【F:specials/urls.py†L28-L30】【F:app/views.py†L3219-L3299】

## Dashboard, menus & search
- **Restaurant dashboard** – Review subscription banners, context checklist, recent concepts/dishes, menu summaries, and AI prompt helpers. 【F:app/views.py†L595-L748】
- **Context toggle** – Exercise the dashboard context inclusion toggles and ensure updated partials return over HTMX. 【F:app/views.py†L751-L793】
- **Dashboard redirect helper** – Log in with multiple/no restaurants to confirm automatic routing. 【F:app/views.py†L796-L807】
- **Menus workspace** – Inspect menu collections, collaboration links, feedback badges, and dish enhancement displays on `/menus/`. 【F:app/views.py†L809-L885】
- **Global tag search** – Validate `/search/` returns relevant concepts and dishes filtered by tag across the account. 【F:app/views.py†L1122-L1169】

## Concept management
- **Concept gallery** – Confirm `/concepts/` shows newest concepts with favorite badges, slider metadata, and prompt shortcuts. 【F:app/views.py†L1061-L1117】
- **Concept generation** – From `/concepts/generate/`, submit prompts, verify ideation runs, session history, and HTMX redirects when invoked from the dashboard. 【F:app/views.py†L1173-L1396】
- **Favorite toggles & lazy backgrounds** – Test concept favoriting/unfavoriting, background sketch generation, and favorites-only view at `/concepts/favorites/`. 【F:app/views.py†L1399-L1491】

## Dish ideation & curation
- **Dish generation pipeline** – Generate dishes for a concept, ensuring ideation runs, schema validation, and HTMX redirect to the detail view. 【F:app/views.py†L1815-L1981】
- **Dish detail page** – Verify grid/page rendering, favorite state, menu assignment options, and regenerate links at `/dishes/<concept_id>/`. 【F:app/views.py†L1984-L2067】
- **Dish favorites, deletion, and variations** – Toggle favorites (with enhancement cleanup), delete dishes, and request AI variations, validating payload rules. 【F:app/views.py†L2069-L2315】

## Favorites hub & menu organization
- **Favorites dashboard** – Review aggregated favorite concepts/dishes, menu assignments, uncategorized list, and move-to-menu controls. 【F:app/views.py†L2347-L2420】
- **Remove favorite action** – Confirm both concept and dish removal endpoints respond for HTMX and JSON callers. 【F:app/views.py†L2423-L2441】
- **Menu CRUD & dish placement** – Create, rename, delete collections; add dishes; and move dishes between menus or uncategorized state. 【F:app/views.py†L2443-L2537】
- **Collaboration link management** – Enable, expire, passcode-protect, or disable public collaboration links for each menu. 【F:app/views.py†L2538-L2637】
- **Public collaboration portal** – Test passcode gating, activity feed, thumb counts, and feedback submission types (thumbs, comments, edits, reorders, new ideas). 【F:app/views.py†L2666-L2847】
- **Chef feedback review & actions** – Validate pending/history lists and approval/rejection flows on `/menus/<uuid>/feedback/`. 【F:app/views.py†L2836-L2903】

## Settings & restaurant data management
- **Settings overview** – Ensure `/settings/` lists restaurant metadata, ingredients, notification prefs, and active menu info. 【F:app/views.py†L2906-L2924】
- **Restaurant info update** – Exercise URL normalization, ingredient capture, and content uploads via `/settings/info/`. 【F:app/views.py†L2927-L2975】
- **Rescrape controls** – Trigger Outscraper and menu rescrape endpoints, verifying job scheduling safeguards. 【F:app/views.py†L2978-L3031】
- **Creativity slider & notifications** – Update classic/creative slider values and toggle email preferences. 【F:app/views.py†L2990-L3039】

## Billing, subscriptions, and notifications
- **Billing dashboard** – Audit plan details, trial countdown, and call-to-action logic on `/billing/`. 【F:app/views.py†L3041-L3072】
- **Upgrade & cancellation flows** – Start Stripe Checkout sessions and cancel subscriptions at period end. 【F:app/views.py†L3075-L3158】
- **Stripe webhook** – Send representative webhook payloads to confirm subscription sync handling. 【F:app/views.py†L3161-L3205】
- **Job status & notifications list** – Hit `/jobs/<uuid>/` for JSON progress and review `/notifications/` UI rendering. 【F:app/views.py†L3208-L3217】

## Miscellaneous utilities
- **Restaurant status & menu upload** – Re-test status widget refresh after invoking `/restaurants/<uuid>/upload-menu/`. 【F:app/views.py†L3219-L3299】
- **Routes inventory** – Double-check every path defined in `specials/urls.py` is reachable as listed above (including `/logout/`, `/favorites/`, `/menus/item/move/`, and `/collab/<uuid>/feedback/`). 【F:specials/urls.py†L7-L76】
