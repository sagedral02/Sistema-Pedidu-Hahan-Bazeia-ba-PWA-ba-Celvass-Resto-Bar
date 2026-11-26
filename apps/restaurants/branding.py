"""App icons: the default fork-and-knife mark, or icons made from the logo the admin uploaded.

Default icons are static files rendered once with `manage.py generate_brand_icons`. Logo icons are rendered
with Pillow on upload and stored in RestaurantIcon (the serverless disk is read-only), then served from
/pwa/icons/<name> with the logo hash in the query string so browsers and installed PWAs pick up a new logo.
"""
import hashlib
import io
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.templatetags.static import static

from .models import Restaurant, RestaurantIcon

ACCENT = (194, 65, 12, 255)      # --accent #c2410c
WHITE = (255, 255, 255, 255)
UTENSILS_GLYPH = '\uf2e7'          # Font Awesome 6 solid "utensils"
DEFAULT_ICON_VERSION = 'fk1'       # bump when the default artwork changes
MAX_LOGO_BYTES = 5 * 1024 * 1024
LOGO_FORMATS = {'PNG', 'JPEG', 'WEBP', 'GIF'}

# name -> (size, style). style: any (transparent margin), solid (opaque), maskable (safe zone), mark (header)
ICON_SPECS = {
    'icon-192.png': (192, 'any'),
    'icon-512.png': (512, 'any'),
    'maskable-512.png': (512, 'maskable'),
    'apple-touch-icon.png': (180, 'solid'),
    'favicon-32.png': (32, 'any'),
    'logo.png': (160, 'mark'),
}
ICO_SIZES = (16, 32, 48)


def _font(size):
    from PIL import ImageFont
    path = Path(settings.BASE_DIR) / 'static' / 'vendor' / 'fontawesome' / 'webfonts' / 'fa-solid-900.ttf'
    return ImageFont.truetype(str(path), size)


def _rounded(size, radius, fill):
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=fill)
    return img


def _draw_glyph(img, glyph_ratio):
    from PIL import ImageDraw
    size = img.width
    font = _font(int(size * glyph_ratio))
    draw = ImageDraw.Draw(img)
    left, top, right, bottom = draw.textbbox((0, 0), UTENSILS_GLYPH, font=font)
    x = (size - (right - left)) / 2 - left
    y = (size - (bottom - top)) / 2 - top
    draw.text((x, y), UTENSILS_GLYPH, font=font, fill=WHITE)
    return img


def render_default(size, style):
    """The fork-and-knife mark of the header: white glyph on the accent colour."""
    from PIL import Image
    if style == 'maskable':
        img = Image.new('RGBA', (size, size), ACCENT)   # full bleed; the glyph stays inside the safe zone
        return _draw_glyph(img, 0.42)
    if style == 'solid':
        img = Image.new('RGBA', (size, size), ACCENT)
        return _draw_glyph(img, 0.56)
    radius = int(size * 0.22)
    return _draw_glyph(_rounded(size, radius, ACCENT), 0.58)


def _contain(logo, box):
    from PIL import Image
    copy = logo.copy()
    copy.thumbnail((box, box), Image.LANCZOS)
    return copy


def render_logo(logo, size, style):
    """The uploaded logo fitted into a square without cropping it."""
    from PIL import Image
    if style in ('maskable', 'solid'):
        canvas = Image.new('RGBA', (size, size), WHITE)
        inner = int(size * (0.66 if style == 'maskable' else 0.84))
    else:
        canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        inner = int(size * (0.92 if style == 'mark' else 0.9))
    fitted = _contain(logo, inner)
    canvas.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    return canvas


def _png(img):
    out = io.BytesIO()
    img.save(out, format='PNG', optimize=True)
    return out.getvalue()


def _ico(images):
    out = io.BytesIO()
    biggest = images[-1]
    biggest.save(out, format='ICO', sizes=[(s, s) for s in ICO_SIZES])
    return out.getvalue()


def write_default_icons(target_dir=None):
    """Render the default icons into static/icons (used by the management command)."""
    target = Path(target_dir or Path(settings.BASE_DIR) / 'static' / 'icons')
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (size, style) in ICON_SPECS.items():
        (target / name).write_bytes(_png(render_default(size, style)))
        written.append(name)
    (target / 'favicon.ico').write_bytes(_ico([render_default(s, 'any') for s in ICO_SIZES]))
    written.append('favicon.ico')
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        '<rect width="512" height="512" rx="112" fill="#c2410c"/>'
        '<text x="256" y="256" fill="#fff" font-family="Font Awesome 6 Free" font-weight="900" font-size="300" '
        'text-anchor="middle" dominant-baseline="central">&#xf2e7;</text></svg>'
    )
    (target / 'icon.svg').write_text(svg, encoding='utf-8')
    written.append('icon.svg')
    return written


# --------------------------------------------------------------------------- uploaded logo

def _open_logo(upload):
    from PIL import Image, UnidentifiedImageError
    raw = upload.read() if hasattr(upload, 'read') else bytes(upload)
    if not raw:
        raise ValidationError("The logo file is empty.")
    if len(raw) > MAX_LOGO_BYTES:
        raise ValidationError("The logo must be 5 MB or smaller.")
    try:
        probe = Image.open(io.BytesIO(raw))
        fmt = probe.format
        probe.verify()
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ValidationError("The logo must be a PNG, JPG, WEBP or GIF image.")
    if fmt not in LOGO_FORMATS:
        raise ValidationError("The logo must be a PNG, JPG, WEBP or GIF image.")
    if min(image.size) < 64:
        raise ValidationError("The logo must be at least 64 pixels wide and high.")
    return raw, image.convert('RGBA')


def set_logo(*, restaurant: Restaurant, upload, actor_user=None) -> Restaurant:
    """Validate the image, render every icon variant and switch the app icons to the logo."""
    from apps.audit.services import log_audit_event
    raw, logo = _open_logo(upload)
    version = hashlib.sha1(raw).hexdigest()[:12]
    files = {name: _png(render_logo(logo, size, style)) for name, (size, style) in ICON_SPECS.items()}
    files['favicon.ico'] = _ico([render_logo(logo, s, 'any') for s in ICO_SIZES])
    with transaction.atomic():
        locked = Restaurant.objects.select_for_update().get(id=restaurant.id)
        RestaurantIcon.objects.filter(restaurant=locked).delete()
        RestaurantIcon.objects.bulk_create([
            RestaurantIcon(restaurant=locked, name=name, data=data,
                           content_type='image/x-icon' if name.endswith('.ico') else 'image/png')
            for name, data in files.items()
        ])
        locked.logo_version = version
        locked.save(update_fields=['logo_version', 'updated_at'])
        log_audit_event(actor_user=actor_user, actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
                        action='RESTAURANT_LOGO_SET', entity_type='Restaurant', entity_id=str(locked.id),
                        after_data={'logo_version': version})
        return locked


def clear_logo(*, restaurant: Restaurant, actor_user=None) -> Restaurant:
    from apps.audit.services import log_audit_event
    with transaction.atomic():
        locked = Restaurant.objects.select_for_update().get(id=restaurant.id)
        RestaurantIcon.objects.filter(restaurant=locked).delete()
        old = locked.logo_version
        locked.logo_version = ''
        locked.save(update_fields=['logo_version', 'updated_at'])
        log_audit_event(actor_user=actor_user, actor_role=getattr(actor_user, 'role', 'ADMIN') if actor_user else 'ADMIN',
                        action='RESTAURANT_LOGO_CLEAR', entity_type='Restaurant', entity_id=str(locked.id),
                        before_data={'logo_version': old})
        return locked


# --------------------------------------------------------------------------- URLs used by templates and the manifest

ICON_NAMES = tuple(ICON_SPECS) + ('favicon.ico',)


def icon_url(restaurant, name):
    if restaurant is not None and getattr(restaurant, 'logo_version', ''):
        return f"/pwa/icons/{name}?v={restaurant.logo_version}"
    return f"{static('icons/' + name)}?v={DEFAULT_ICON_VERSION}"


def brand_context(restaurant):
    urls = {name.replace('.', '_').replace('-', '_'): icon_url(restaurant, name) for name in ICON_NAMES}
    return {
        'has_logo': bool(getattr(restaurant, 'logo_version', '')),
        'version': getattr(restaurant, 'logo_version', '') or DEFAULT_ICON_VERSION,
        **urls,
    }
