from .base import *

DEBUG = False
TESTING = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

CELERY_TASK_ALWAYS_EAGER = True

# Push is off unless a test turns it on with fake keys; when on, send inline so tests can assert it.
VAPID_PUBLIC_KEY = ''
VAPID_PRIVATE_KEY = ''
PUSH_ASYNC = False
