from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import Http404, HttpResponse, HttpResponseNotModified, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from apps.restaurants.branding import ICON_NAMES, icon_url

SERVICE_WORKER_PATH = 'js/sw.js'


def _restaurant():
    from apps.restaurants.models import Restaurant
    return Restaurant.objects.filter(is_active=True).first()


@cache_control(max_age=3600)
def manifest_json(request):
    restaurant = _restaurant()
    name = (restaurant.name if restaurant else '') or "Celvass Resto & Bar"
    short_name = restaurant.app_short_name if restaurant else "Celvass"
    description = (restaurant.description if restaurant else '') or f"Pedidu dine-in liuhosi QR ba {name}"
    manifest = {
        "name": name,
        "short_name": short_name,
        "description": description,
        "id": "/",
        "start_url": "/",
        "scope": "/",
        "lang": "tet",
        "dir": "ltr",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#faf8f5",
        "theme_color": "#c2410c",
        "categories": ["food", "business"],
        "icons": [
            {"src": icon_url(restaurant, 'icon-192.png'), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": icon_url(restaurant, 'icon-512.png'), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": icon_url(restaurant, 'maskable-512.png'), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {
                "name": "Portal staff",
                "short_name": "Staff",
                "url": "/login/",
                "icons": [{"src": icon_url(restaurant, 'icon-192.png'), "sizes": "192x192", "type": "image/png"}],
            }
        ],
    }
    response = JsonResponse(manifest)
    response['Content-Type'] = 'application/manifest+json'
    return response


@require_GET
def brand_icon(request, name):
    """Icons made from the uploaded logo; without a logo the static fork-and-knife icon is used."""
    if name not in ICON_NAMES:
        raise Http404("Unknown icon")
    restaurant = _restaurant()
    if restaurant is None or not restaurant.logo_version:
        return HttpResponseRedirect(icon_url(None, name))
    icon = restaurant.icons.filter(name=name).first()
    if icon is None:
        return HttpResponseRedirect(icon_url(None, name))
    etag = f'"{restaurant.logo_version}-{name}"'
    if request.headers.get('If-None-Match') == etag:
        return HttpResponseNotModified()
    response = HttpResponse(bytes(icon.data), content_type=icon.content_type)
    response['ETag'] = etag
    if request.GET.get('v') == restaurant.logo_version:
        response['Cache-Control'] = 'public, max-age=31536000, immutable'
    else:
        response['Cache-Control'] = 'public, max-age=300'
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
