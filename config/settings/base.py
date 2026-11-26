import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'insecure-django-dev-secret-key-change-in-production-celvas-99')

DEBUG = True

ALLOWED_HOSTS = ['*']

# Application definition
DJANGO_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'channels',
]

LOCAL_APPS = [
    'apps.accounts.apps.AccountsConfig',
    'apps.restaurants.apps.RestaurantsConfig',
    'apps.tables.apps.TablesConfig',
    'apps.catalog.apps.CatalogConfig',
    'apps.ordering.apps.OrderingConfig',
    'apps.kitchen.apps.KitchenConfig',
    'apps.payments.apps.PaymentsConfig',
    'apps.shifts.apps.ShiftsConfig',
    'apps.audit.apps.AuditConfig',
    'apps.outbox.apps.OutboxConfig',
    'apps.reporting.apps.ReportingConfig',
    'apps.pwa.apps.PwaConfig',  # also Web Push subscriptions (models)
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

AUTH_USER_MODEL = 'accounts.User'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.restaurants.context_processors.restaurant_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization & Currency
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Dili'
# Hour at which a business day starts (0 = midnight). Daily quotas and take-away numbers reset then.
BUSINESS_DAY_START_HOUR = int(os.environ.get('BUSINESS_DAY_START_HOUR', '0'))
# A cashier must have an open shift (opening cash) before taking a payment.
REQUIRE_SHIFT_FOR_PAYMENT = True
# Where a guest lands after the thank-you page (production: https://celvass-pwa.vercel.app/).
CUSTOMER_HOME_URL = os.environ.get('CUSTOMER_HOME_URL', '/')
# Web Push (VAPID). Leave empty to switch push notifications off. Generate with: manage.py generate_vapid_keys
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_SUBJECT = os.environ.get('VAPID_SUBJECT', 'mailto:admin@celvass.tl')
# Send pushes from a background thread so requests never wait for the push services.
PUSH_ASYNC = os.environ.get('PUSH_ASYNC', '1') == '1'
USE_I18N = True
USE_TZ = True

DEFAULT_CURRENCY = 'USD'
DEFAULT_CURRENCY_SYMBOL = '$'

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser',
    ],
    'DATETIME_FORMAT': '%Y-%m-%dT%H:%M:%S%z',
    'NON_FIELD_ERRORS_KEY': 'error',
}

# Login URL
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'
