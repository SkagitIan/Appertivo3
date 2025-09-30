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
- **Payment Processing**: Stripe integration for onboarding payments
- **Security**: Google reCAPTCHA v3 for spam protection
- **Frontend**: HTML templates with Tailwind CSS

## Recent Changes
- 2025-09-30: Complete onboarding flow implementation
  - Implemented email activation system with token-based confirmation
  - Added Stripe payment integration ($49 onboarding fee)
  - Created real-time onboarding status page with progress tracking
  - Integrated Google reCAPTCHA v3 on signup form
  - Added dashboard welcome banner for first-time users
  - All migrations applied successfully
  
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

## Onboarding Flow
1. User signs up with email, password, restaurant name, location
2. reCAPTCHA v3 validates signup attempt
3. Email activation link sent (24-hour expiration)
4. User clicks link to confirm email
5. Stripe checkout for $49 onboarding fee
6. Real-time status page shows background processing:
   - Outscraper data collection
   - Google reviews fetch
   - AI-powered analysis (OpenAI)
   - Customer persona generation
7. Auto-redirect to dashboard when complete
8. Welcome banner guides new users to concept generator

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
