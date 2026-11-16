from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import HttpResponse, JsonResponse, Http404
from django.shortcuts import render
from django.views.decorators.cache import cache_control

SERVICE_WORKER_PATH = 'js/sw.js'


@cache_control(max_age=3600)
def manifest_json(request):
    restaurant_name = getattr(getattr(request, 'current_restaurant', None), 'name', None) or "Celvass Resto & Bar"
    manifest = {
        "name": restaurant_name,
        "short_name": "Celvass",
        "description": "Pedidu dine-in liuhosi QR ba Celvass Resto & Bar",
        "id": "/",
        "start_url": "/",
        "scope": "/",
        "lang": "tet",
        "dir": "ltr",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#faf8f5",
        "theme_color": "#ffffff",
        "categories": ["food", "business"],
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        ],
        "shortcuts": [
            {
                "name": "Portal staff",
                "short_name": "Staff",
                "url": "/login/",
                "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"}],
            }
        ],
    }
    response = JsonResponse(manifest)
    response['Content-Type'] = 'application/manifest+json'
    return response


def _read_service_worker():
    path = finders.find(SERVICE_WORKER_PATH)
    if not path:
        candidate = Path(settings.STATIC_ROOT) / SERVICE_WORKER_PATH
        path = str(candidate) if candidate.exists() else None
    if not path:
        raise Http404("Service worker not found")
    return Path(path).read_text(encoding='utf-8')


@cache_control(no_cache=True, must_revalidate=True, max_age=0)
def service_worker(request):
    response = HttpResponse(_read_service_worker(), content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    return response


def offline_view(request):
    return render(request, 'customer/offline.html')
