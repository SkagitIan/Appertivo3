# Appertivo

Minimal Django + HTMX prototype for generating restaurant menu ideas.

## Setup

1. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

2. **Environment variables**

   Create a `.env` file with the following keys if you plan to call external
   services:

   - `OUTSCRAPER_API_KEY`
   - `SCRAPERAPI_API_KEY`

3. **Database migrations**

   ```bash
   python manage.py migrate
   ```

4. **Running the app**

   ```bash
   python manage.py runserver
   ```

5. **Celery worker** (optional for async tasks)

   ```bash
   celery -A specials worker -l info
   ```

## Tests

Run the Django test suite to ensure everything is functioning.

```bash
python manage.py test
```
