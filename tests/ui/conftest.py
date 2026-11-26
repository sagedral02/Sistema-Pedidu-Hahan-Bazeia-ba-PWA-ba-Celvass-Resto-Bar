"""
Browser test harness for admin portal modal behaviour.

Keputusan harness: pytest-playwright (Chromium nyata + ``live_server`` Django)
==========================================================================

Opsi yang dibandingkan:

1. **pytest-playwright (dipilih)**. Chromium nyata menjalankan transisi CSS
   Bootstrap 5.3.3, sehingga ``transitionend``, ``_isTransitioning``,
   ``hidden.bs.modal`` dan ``.modal-backdrop`` berperilaku seperti di produksi.
   Bug modal staff bergantung pada timing transisi ini. Harness ini cocok dengan
   pytest/pytest-django yang sudah dipakai repo dan tidak butuh Node.
   Biaya: dependensi ``pytest-playwright`` dan unduhan browser
   (``python -m playwright install chromium``).
2. **jsdom + Bootstrap bundle (ditolak)**. Lebih ringan, tetapi jsdom tidak
   menjalankan transisi CSS. ``transitionend`` tidak terpicu secara alami dan
   Bootstrap memakai fallback ``setTimeout``, sehingga status
   ``_isTransitioning`` tidak realistis dan bug bisa tidak tereproduksi.
   Juga butuh Node dan ``node_modules`` yang belum ada di repo.

Cara menjalankan:

- Hanya tes browser: ``pytest -m browser tests/ui``
- Suite lama tanpa browser: ``pytest -m "not browser"``. Bila ``playwright``
  tidak terpasang, tes browser di-skip otomatis.

Catatan teknis:

- Playwright sync API menjalankan event loop di thread tes, sehingga akses ORM
  Django dari fixture akan ditolak (``SynchronousOnlyOperation``).
  ``DJANGO_ALLOW_ASYNC_UNSAFE`` diaktifkan untuk tes ini saja.
- Service worker diblokir di context browser agar ``admin.js`` selalu
  diambil dari server tes, bukan dari cache SW.
"""
import asyncio
import os
import sys

import pytest
from django.test import Client

from apps.accounts.models import Role, User

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

try:  # pragma: no cover - depends on optional dev dependency
    import playwright  # noqa: F401
    HAS_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    HAS_PLAYWRIGHT = False

STAFF_PASSWORD = 'Sabor-Dili-2024!'


def pytest_collection_modifyitems(config, items):
    if HAS_PLAYWRIGHT:
        return
    skip = pytest.mark.skip(reason='pytest-playwright tidak terpasang')
    for item in items:
        if 'browser' in item.keywords:
            item.add_marker(skip)


# --------------------------------------------------------------------------- #
# pytest-playwright overrides
# --------------------------------------------------------------------------- #
@pytest.fixture(scope='session', autouse=True)
def _proactor_event_loop_on_windows():
    """Daphne (INSTALLED_APPS) installs Twisted's asyncio reactor, which switches
    Windows to the selector event loop. That loop cannot spawn subprocesses, so
    Playwright fails to start its driver. Restore the proactor policy before the
    session-scoped ``playwright`` fixture runs (autouse fixtures run first)."""
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    yield


@pytest.fixture(scope='session', autouse=True)
def _test_db_before_live_server(django_db_setup):
    """Create the file-backed test database before ``live_server`` starts. Otherwise the server is built while the
    DB name is still ':memory:' and every request thread shares one SQLite connection, which drops sessions."""
    yield


@pytest.fixture(scope='session')
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        'service_workers': 'block',
        'viewport': {'width': 1280, 'height': 900},
    }


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@pytest.fixture
def ui_admin(transactional_db):
    """Admin yang login: role ADMIN tanpa superuser, supaya 'akun sendiri' dan
    'superuser' adalah dua baris berbeda di tabel staff."""
    return User.objects.create_user(
        username='ui_admin', password=STAFF_PASSWORD, full_name='UI Admin',
        role=Role.ADMIN, is_staff=True,
    )


@pytest.fixture
def ui_superuser(transactional_db):
    return User.objects.create_superuser(
        username='ui_root', email='root@ui.test', password=STAFF_PASSWORD,
        full_name='UI Root',
    )


@pytest.fixture
def ui_staff(transactional_db):
    return User.objects.create_user(
        username='ui_cashier', password=STAFF_PASSWORD, full_name='UI Kaixa',
        role=Role.CASHIER, is_staff=True,
    )


@pytest.fixture
def staff_accounts(ui_admin, ui_superuser, ui_staff):
    return {'admin': ui_admin, 'superuser': ui_superuser, 'staff': ui_staff}


# --------------------------------------------------------------------------- #
# Session + navigation
# --------------------------------------------------------------------------- #
@pytest.fixture
def admin_page(page, live_server, staff_accounts):
    """Chromium page dengan sesi admin terautentikasi, belum membuka halaman."""
    client = Client()
    client.force_login(staff_accounts['admin'])
    session_cookie = client.cookies['sessionid'].value
    page.context.add_cookies([{
        'name': 'sessionid', 'value': session_cookie, 'url': live_server.url,
    }])
    page.set_default_timeout(5000)
    return page


@pytest.fixture
def open_users_panel(admin_page, live_server):
    """Buka /admin-portal/#users-panel dan tunggu Bootstrap + admin.js siap."""
    def _open():
        admin_page.goto(f'{live_server.url}/admin-portal/#users-panel')
        admin_page.wait_for_function(
            "() => window.bootstrap && typeof window.openCreateStaffModal === 'function'"
        )
        admin_page.locator('#users-panel').wait_for(state='visible')
        return admin_page
    return _open


# --------------------------------------------------------------------------- #
# Modal state helpers
# --------------------------------------------------------------------------- #
MODAL_STATE_JS = """
(id) => {
  const el = document.getElementById(id);
  const inst = el && window.bootstrap ? bootstrap.Modal.getInstance(el) : null;
  const style = el ? getComputedStyle(el) : null;
  return {
    hasInstance: !!inst,
    isTransitioning: inst ? !!inst._isTransitioning : null,
    isShown: inst ? !!inst._isShown : null,
    hasShowClass: el ? el.classList.contains('show') : false,
    visible: !!(el && style.display !== 'none' && el.classList.contains('show')),
    backdropCount: document.querySelectorAll('.modal-backdrop').length,
    bodyClasses: Array.from(document.body.classList),
  };
}
"""


def read_modal_state(page, modal_id='staffModal'):
    """Snapshot of Bootstrap modal internals plus backdrop/body state."""
    return page.evaluate(MODAL_STATE_JS, modal_id)


@pytest.fixture
def modal_state():
    return read_modal_state
