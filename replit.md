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
- **Payment Processing**: Stripe integration for self-serve subscriptions
- **Security**: Google reCAPTCHA v3 for spam protection
- **Frontend**: HTML templates with Tailwind CSS

## Recent Changes
- 2025-09-30: Getting started hub and pricing redirect
  - Added `/getting-started/` checklist for new teams
  - Added `/pricing/` endpoint that launches Stripe Checkout (303 redirect)
  - Replaced onboarding workspace with streamlined setup instructions
  - Signup emails now point to `/check-email/` and activation lands on the getting started hub
  
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
- API keys should be managed through Replit secrets
- Application configured to work with Replit's proxy environment
- Appertivo purple branding (#5C008B) used throughout

## Getting Started Flow
1. User signs up with email, password, restaurant name, and location (with reCAPTCHA v3 validation)
2. Application sends an activation email directing the user to `/check-email/`
3. User clicks the activation link and lands on `/getting-started/`
4. Optional `/pricing/` endpoint launches Stripe Checkout for subscriptions
5. Getting started page highlights concept generation, menu workspace, and sharing steps
6. Dashboard and menus remain available once the restaurant is provisioned

## Required Environment Variables
- `STRIPE_SECRET_KEY`: Stripe API secret key
- `STRIPE_PUBLISHABLE_KEY`: Stripe publishable key
- `STRIPE_WEBHOOK_SECRET`: Stripe webhook signing secret
- `RECAPTCHA_SITE_KEY`: Google reCAPTCHA site key
- `RECAPTCHA_SECRET_KEY`: Google reCAPTCHA secret key
- `BREVO_API_KEY`: Email service API key (optional)
- `OPENAI_API_KEY`: OpenAI API for AI features
- `OUTSCRAPER_API_KEY`: Outscraper for restaurant data

## Development Workflow
1. Django server runs on port 5000 via workflow
2. Database migrations are up to date
3. Static files served via WhiteNoise
4. Application fully functional for development

## Deployment Configuration
- **Target**: Autoscale deployment
- **Build**: `python manage.py collectstatic --noinput`
- **Run**: `gunicorn --bind=0.0.0.0:5000 --reuse-port specials.wsgi:application`
