# Appertivo - Django Restaurant Application

## Project Overview
A Django web application for restaurant management and menu generation using AI. The application includes features for restaurant onboarding, concept generation, dish creation, menu management, and billing.

## Architecture
- **Framework**: Django 5.2.6
- **Database**: SQLite (development) 
- **Static Files**: WhiteNoise for serving static files
- **Email**: Django Anymail with Brevo backend
- **Background Tasks**: Celery (configured for memory broker in development)
- **Authentication**: Django built-in auth system with custom forms
- **Frontend**: HTML templates with Tailwind CSS

## Recent Changes
- 2025-09-19: Initial Replit environment setup
  - Installed Python 3.11 and all dependencies
  - Configured Django settings for Replit (DEBUG=True, ALLOWED_HOSTS=['*'])
  - Ran database migrations successfully
  - Set up Django development server workflow on port 5000
  - Configured deployment with gunicorn for production

## Project Structure
- `app/`: Main Django application containing models, views, templates
- `specials/`: Django project configuration
- `static/`: Static files (CSS, JS)
- `tests/`: Comprehensive test suite
- `whitenoise/`: Custom WhiteNoise middleware

## User Preferences
- Development environment uses SQLite database
- API keys should be managed through Replit secrets (BREVO_API_KEY, GOOGLE_CLIENT_ID, etc.)
- Application configured to work with Replit's proxy environment

## Development Workflow
1. Django server runs on port 5000 via workflow
2. Database migrations are up to date
3. Static files served via WhiteNoise
4. Application fully functional for development

## Deployment Configuration
- **Target**: Autoscale deployment
- **Build**: `python manage.py collectstatic --noinput`
- **Run**: `gunicorn --bind=0.0.0.0:5000 --reuse-port specials.wsgi:application`