"""Admin overview in a real browser: KPI cards, interactive SVG charts, click-through, languages, motion, limits."""
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.catalog.models import Category, MenuItem
from apps.ordering.models import OrderStatus
from apps.payments.models import PaymentMethod
from reporting_helpers import add_item, make_order, make_payment

pytestmark = pytest.mark.browser

CHART_HOSTS = ['ov-trend', 'ov-methods', 'ov-hours', 'ov-statuses', 'ov-top', 'ov-categories', 'ov-cashiers']


def _login(page, live_server, user):
    client = Client()
    client.force_login(user)
    page.context.add_cookies([{'name': 'sessionid', 'value': client.cookies['sessionid'].value, 'url': live_server.url}])
    page.set_default_timeout(8000)


@pytest.fixture
def errors(page):
    collected = []
    page.on('pageerror', lambda exc: collected.append(str(exc)))
    return collected


@pytest.fixture
def overview_data(transactional_db, restaurant, active_session, session_two, cashier_user, cashier_two, category_main):
    today = timezone.localdate()
    drinks = Category.objects.create(restaurant=restaurant, name='Minuman', slug='drinks', is_active=True)
    fish = MenuItem.objects.create(restaurant=restaurant, category=category_main, name='Ikan Saboko', slug='ikan',
                                   price='6.50')
    tea = MenuItem.objects.create(restaurant=restaurant, category=drinks, name='Teh', slug='teh', price='1.00')
    order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '25.00', day=today, hour=10,
                       confirmed_by=cashier_user)
    add_item(order, 'Ikan Saboko', 3, '6.50', fish)
    add_item(order, 'Teh', 5, '1.00', tea)
    rejected = make_order(restaurant, session_two, OrderStatus.REJECTED, '9.00', day=today, hour=11)
    rejected.rejection_reason = 'Stok habis'
    rejected.save()
    make_payment(restaurant, active_session, cashier_user, '20.00', paid_day=today, hour=12)
    make_payment(restaurant, session_two, cashier_two, '30.00', paid_day=today, hour=13,
                 method=PaymentMethod.MANUAL_OTHER)
    make_payment(restaurant, active_session, cashier_user, '10.00', paid_day=today - timedelta(days=2), hour=12)
    make_payment(restaurant, active_session, cashier_user, '8.00', paid_day=today - timedelta(days=9), hour=12)
    return {'today': today, 'order': order, 'rejected': rejected}


def _open(page, live_server, lang='en', ready='#ov-trend svg'):
    page.add_init_script(f"localStorage.setItem('celvas_lang', '{lang}')")
    page.goto(f'{live_server.url}/admin-portal/')
    page.wait_for_selector(ready)


def _state(page, selector):
    return page.locator(selector).get_attribute('aria-pressed')


def _click_ring(page, host, fraction):
    """Click the donut ring at `fraction` (0..1) of the way round from 12 o'clock, like a person would.

    Playwright's own click aims at the middle of the element, which for a ring is the empty hole.
    """
    import math
    svg = page.locator(f'#{host} svg').first
    svg.scroll_into_view_if_needed()  # mouse coordinates are viewport coordinates
    box = svg.bounding_box()
    size = float(svg.get_attribute('width'))
    radius = (size - round(size * 0.17)) / 2 - 2
    angle = -math.pi / 2 + fraction * 2 * math.pi
    page.mouse.click(box['x'] + size / 2 + radius * math.cos(angle), box['y'] + size / 2 + radius * math.sin(angle))


# ----------------------------------------------------------------------------- content

def test_overview_shows_kpis_and_every_chart(page, live_server, ui_admin, overview_data, errors):
    _login(page, live_server, ui_admin)
    _open(page, live_server)

    kpi = lambda name: page.locator(f'#ov-kpis [data-kpi="{name}"] [data-value]').inner_text()  # noqa: E731
    assert kpi('sales') == '$50.00'
    assert kpi('transactions') == '2'
    assert kpi('average') == '$25.00'
    assert kpi('orders') == '2'
    assert kpi('rejection_rate') == '50.0%'
    assert '/' in kpi('tables')

    for host in CHART_HOSTS:
        assert page.locator(f'#{host} svg').count() >= 1, host
    assert page.locator('#ov-top .chart-row').first.get_attribute('aria-label').startswith('Teh')
    assert 'No category' not in page.locator('#ov-categories').inner_text()
    assert 'Minuman' in page.locator('#ov-categories').inner_text()
    assert 'Waiting for cashier' in page.locator('#ov-statuses').inner_text()
    assert page.locator('#ov-recent-payments li').count() == 4  # latest payments, whatever the period
    assert 'Stok habis' in page.locator('#ov-recent-rejections').inner_text()
    assert page.locator('#ov-status').inner_text().startswith('Updated')
    assert errors == []


def test_kpi_delta_compares_with_the_previous_period(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    page.locator('#ov-range [data-range="7d"]').click()
    page.wait_for_function("() => document.querySelector('#ov-kpis [data-kpi=\"sales\"] [data-value]').textContent === '$60.00'")
    delta = page.locator('#ov-kpis [data-kpi="sales"] [data-delta]')
    assert delta.get_attribute('data-tone') == 'good'
    text = delta.inner_text()
    assert '+650.0%' in text and 'vs previous period' in text
    # rejections rising is bad news, so the same arrow direction uses the opposite tone
    rate = page.locator('#ov-kpis [data-kpi="rejection_rate"] [data-delta]')
    assert rate.get_attribute('data-tone') in ('bad', 'flat')


def test_range_switch_reloads_and_updates_controls(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    requests = []
    page.on('request', lambda r: requests.append(r.url) if '/api/v1/admin/overview/' in r.url else None)
    _open(page, live_server)
    assert any('range=today' in u for u in requests)

    page.locator('#ov-range [data-range="30d"]').click()
    page.wait_for_function("() => document.querySelector('#ov-range [data-range=\"30d\"]').getAttribute('aria-pressed') === 'true'")
    page.wait_for_function("() => document.querySelectorAll('#ov-trend svg circle').length > 24")
    assert any('range=30d' in u for u in requests)
    assert _state(page, '#ov-range [data-range="today"]') == 'false'


def test_refresh_button_reloads_without_duplicating_charts(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    requests = []
    page.on('request', lambda r: requests.append(r.url) if '/api/v1/admin/overview/' in r.url else None)
    _open(page, live_server)
    before = len(requests)
    page.locator('#ov-refresh').click()
    page.wait_for_function('(n) => window.performance.getEntriesByType("resource").filter(e => e.name.includes("/admin/overview/")).length > n', arg=before - 1)
    assert len(requests) == before + 1
    for host in CHART_HOSTS:
        assert page.locator(f'#{host} > svg, #{host} > .chart-donut > svg').count() == 1, host


# ----------------------------------------------------------------------------- interaction

def test_trend_tooltip_follows_the_pointer_and_the_keyboard(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    page.locator('#ov-range [data-range="7d"]').click()
    page.wait_for_function("() => document.querySelectorAll('#ov-trend svg circle').length > 6")
    page.wait_for_function("() => !document.querySelector('#ov-trend .chart-fade') || getComputedStyle(document.querySelector('#ov-trend .chart-fade')).opacity === '1'")
    svg = page.locator('#ov-trend svg')
    svg.scroll_into_view_if_needed()
    box = svg.bounding_box()
    assert box is not None

    page.mouse.move(box['x'] + box['width'] - 20, box['y'] + 100)
    tip = page.locator('#ov-trend .chart-tooltip')
    page.wait_for_function("() => !document.querySelector('#ov-trend .chart-tooltip').hidden")
    assert '$50.00' in tip.inner_text() and 'This period' in tip.inner_text()

    page.mouse.move(box['x'] + 5, box['y'] + box['height'] + 60)
    page.wait_for_function("() => document.querySelector('#ov-trend .chart-tooltip').hidden")

    svg.focus()
    page.wait_for_function("() => !document.querySelector('#ov-trend .chart-tooltip').hidden")
    last = tip.inner_text()
    page.keyboard.press('ArrowLeft')
    page.keyboard.press('ArrowLeft')
    assert tip.inner_text() != last
    assert '$10.00' in tip.inner_text()
    assert page.locator('#ov-trend [aria-live]').inner_text() != ''
    page.keyboard.press('Escape')
    assert tip.is_hidden()


def test_legend_toggles_series_and_keeps_one_visible(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    legend = page.locator('#ov-trend .chart-legend-item')
    assert legend.count() == 2
    dashed = "#ov-trend svg path[stroke-dasharray]"
    assert page.locator(dashed).count() == 1

    legend.nth(1).click()
    assert page.locator('#ov-trend .chart-legend-item').nth(1).get_attribute('aria-pressed') == 'false'
    assert page.locator(dashed).count() == 0

    page.locator('#ov-trend .chart-legend-item').nth(0).click()  # would hide the last visible series
    assert page.locator('#ov-trend .chart-legend-item').nth(0).get_attribute('aria-pressed') == 'true'

    page.locator('#ov-trend .chart-legend-item').nth(1).click()
    assert page.locator(dashed).count() == 1


def test_donut_legend_hides_a_slice_and_updates_the_total(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    assert page.locator('#ov-methods .chart-slice').count() == 2
    assert page.locator('#ov-methods .chart-center-value').text_content() == '$50.00'

    page.locator('#ov-methods .chart-legend-item').first.click()  # hide Cash
    assert page.locator('#ov-methods .chart-slice').count() == 1
    assert page.locator('#ov-methods .chart-center-value').text_content() == '$30.00'
    assert page.locator('#ov-methods .chart-legend-item').first.get_attribute('aria-pressed') == 'false'


def test_hbar_tooltip_and_keyboard_activation(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    row = page.locator('#ov-top .chart-row').first
    row.focus()
    page.wait_for_function("() => !document.querySelector('#ov-top .chart-tooltip').hidden")
    tip = page.locator('#ov-top .chart-tooltip').inner_text()
    assert 'Teh' in tip and 'Quantity' in tip and '$5.00' in tip
    page.keyboard.press('Escape')
    assert page.locator('#ov-top .chart-tooltip').is_hidden()

    page.locator('#ov-top-mode [data-mode="revenue"]').click()
    assert page.locator('#ov-top .chart-row').first.get_attribute('aria-label').startswith('Ikan Saboko')
    assert _state(page, '#ov-top-mode [data-mode="revenue"]') == 'true'


def test_clicking_the_trend_opens_that_days_transactions(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    page.locator('#ov-range [data-range="7d"]').click()
    page.wait_for_function("() => document.querySelectorAll('#ov-trend svg circle').length > 6")
    box = page.locator('#ov-trend svg').bounding_box()

    page.mouse.click(box['x'] + box['width'] - 20, box['y'] + 100)
    page.wait_for_url('**/reports/#history/transactions**')
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 2")
    assert page.locator('#rpt-custom').is_visible()
    assert page.locator('#rpt-start').input_value() == overview_data['today'].isoformat()
    assert page.locator('#rpt-h-status').input_value() == 'COMPLETED'


def test_clicking_a_payment_method_filters_transactions(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    page.wait_for_function("() => !document.querySelector('#ov-methods .chart-pop') || getComputedStyle(document.querySelector('#ov-methods .chart-pop')).opacity === '1'")
    _click_ring(page, 'ov-methods', 0.2)  # the Cash slice covers the first 40%
    page.wait_for_url('**/reports/#history/transactions**')
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 1")
    assert page.locator('#rpt-h-method').input_value() == 'CASH'
    assert '$20.00' in page.locator('#rpt-h-body').inner_text()


def test_clicking_a_menu_item_searches_orders(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    page.locator('#ov-top .chart-row').first.click()
    page.wait_for_url('**/reports/#history/orders**')
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 1")
    assert page.locator('#rpt-h-search').input_value() == 'Teh'


def test_clicking_a_category_opens_the_menu_sales_report(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    page.wait_for_function("() => !document.querySelector('#ov-categories .chart-pop') || getComputedStyle(document.querySelector('#ov-categories .chart-pop')).opacity === '1'")
    _click_ring(page, 'ov-categories', 0.4)
    page.wait_for_url('**/reports/#summary**')
    page.wait_for_function("() => document.querySelector('#rpt-type').value === 'menu_sales'")
    page.wait_for_function("() => document.querySelectorAll('#rpt-body tr').length >= 2")
    assert 'Ikan Saboko' in page.locator('#rpt-body').inner_text()


def test_status_bar_and_recent_links_open_the_history(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    page.locator('#ov-statuses .chart-row').nth(3).click()  # rejected
    page.wait_for_url('**/reports/#history/orders**')
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 1")
    assert page.locator('#rpt-h-status').input_value() == 'REJECTED'

    page.go_back()
    page.wait_for_selector('#ov-recent-rejections a')
    page.locator('#ov-recent-rejections a').first.click()
    page.wait_for_url('**/reports/#history/orders**')
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 1")
    assert page.locator('#rpt-h-search').input_value() == overview_data['rejected'].order_code


# ----------------------------------------------------------------------------- language, motion, size, safety

def test_language_change_retranslates_cards_charts_and_legends(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    page.evaluate("() => setLanguage('id')")
    page.wait_for_function("() => document.querySelector('#ov-kpis [data-kpi=\"sales\"] .kpi-label').textContent === 'Penjualan'")
    page.wait_for_function("() => document.querySelector('#ov-trend .chart-legend-item').textContent.includes('Periode ini')")
    assert 'Tunai' in page.locator('#ov-methods').inner_text()
    assert 'Menunggu kasir' in page.locator('#ov-statuses').inner_text()
    assert page.locator('#ov-trend svg').get_attribute('aria-label').startswith('Tren penjualan')
    assert 'Penjualan per kategori' in page.locator('#overview-panel').inner_text()

    page.evaluate("() => setLanguage('pt')")
    page.wait_for_function("() => document.querySelector('#ov-trend .chart-legend-item').textContent.includes('Período atual')")
    assert 'Dinheiro' in page.locator('#ov-methods').inner_text()


def test_charts_respect_reduced_motion(browser, live_server, ui_admin, overview_data):
    for reduced, expected in (('reduce', 0), ('no-preference', 1)):
        context = browser.new_context(viewport={'width': 1280, 'height': 900}, service_workers='block',
                                      reduced_motion=reduced)
        page = context.new_page()
        try:
            _login(page, live_server, ui_admin)
            _open(page, live_server)
            assert page.locator('#ov-trend .chart-fade').count() == expected
            assert page.locator('#ov-methods .chart-pop').count() == expected
        finally:
            context.close()


def test_charts_redraw_when_the_container_is_resized(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    before = page.locator('#ov-trend svg').get_attribute('width')
    page.set_viewport_size({'width': 700, 'height': 900})
    page.wait_for_function("(w) => document.querySelector('#ov-trend svg').getAttribute('width') !== w", arg=before)
    width = int(page.locator('#ov-trend svg').get_attribute('width'))
    assert width <= 700
    assert page.locator('#ov-trend svg').get_attribute('viewBox').split()[2] == str(width)


def test_phone_layout_has_no_horizontal_scroll(browser, live_server, ui_admin, overview_data):
    context = browser.new_context(viewport={'width': 390, 'height': 800}, service_workers='block')
    page = context.new_page()
    try:
        _login(page, live_server, ui_admin)
        _open(page, live_server)
        page.wait_for_selector('#ov-cashiers svg')
        assert page.evaluate('() => document.documentElement.scrollWidth <= window.innerWidth')
        assert page.locator('.app-tabbar').is_visible()
        assert int(page.locator('#ov-trend svg').get_attribute('width')) <= 390
    finally:
        context.close()


def test_data_is_never_interpreted_as_markup(page, live_server, ui_admin, overview_data, restaurant, active_session,
                                             errors):
    evil = '<img src=x onerror="window.__pwned=1">'
    order = make_order(restaurant, active_session, OrderStatus.COMPLETED, '99.00', day=overview_data['today'], hour=9)
    add_item(order, evil, 40, '2.00')
    _login(page, live_server, ui_admin)
    _open(page, live_server)

    assert page.locator('#overview-panel img').count() == 0
    assert evil in page.locator('#ov-top').inner_text() or '<img' in page.locator('#ov-top').inner_text()
    page.locator('#ov-top .chart-row').first.focus()
    page.wait_for_function("() => !document.querySelector('#ov-top .chart-tooltip').hidden")
    assert page.locator('#ov-top .chart-tooltip img').count() == 0
    assert page.evaluate('() => window.__pwned') is None
    assert errors == []


def test_an_empty_restaurant_shows_empty_states_not_errors(page, live_server, ui_admin, restaurant, errors):
    _login(page, live_server, ui_admin)
    page.add_init_script("localStorage.setItem('celvas_lang', 'en')")
    page.goto(f'{live_server.url}/admin-portal/')
    page.wait_for_selector('#ov-status:not(:empty)')
    page.wait_for_selector('#ov-trend .chart-empty')
    for host in CHART_HOSTS:
        assert page.locator(f'#{host} .chart-empty').count() == 1, host
    assert page.locator('#ov-kpis [data-kpi="sales"] [data-value]').inner_text() == '$0.00'
    assert page.locator('#ov-kpis [data-kpi="rejection_rate"] [data-value]').inner_text() == '\u2014'
    assert 'No payments yet.' in page.locator('#ov-recent-payments').inner_text()
    assert errors == []


def test_a_failed_refresh_keeps_the_last_data_on_screen(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    page.route('**/api/v1/admin/overview/**', lambda route: route.fulfill(status=500, body='{}'))
    page.locator('#ov-refresh').click()
    page.wait_for_function("() => document.getElementById('ov-status').dataset.state === 'error'")
    assert 'Could not refresh the data.' in page.locator('#ov-status').inner_text()
    assert page.locator('#ov-kpis [data-kpi="sales"] [data-value]').inner_text() == '$50.00'
    assert page.locator('#ov-trend svg').count() == 1


# ----------------------------------------------------------------------------- the chart module on its own

def test_chart_module_handles_degenerate_data(page, live_server, ui_admin, restaurant, errors):
    _login(page, live_server, ui_admin)
    _open(page, live_server, ready='#ov-trend .chart-empty')
    result = page.evaluate("""() => {
      const host = document.createElement('div');
      host.style.width = '400px';
      document.getElementById('overview-panel').append(host);
      const out = {};
      const sizeOf = () => host.querySelector('svg') ? host.querySelector('svg').getAttribute('width') : null;

      Charts.line(host, { labels: ['a'], series: [{ key: 's', label: 'S', values: [5], color: '--accent' }] });
      out.singlePoint = !!host.querySelector('svg path');

      Charts.line(host, { labels: ['a', 'b', 'c'], series: [{ key: 's', label: 'S', values: [7, 7, 7], color: '--accent' }] });
      out.constant = !!host.querySelector('svg path') && !/NaN|Infinity/.test(host.innerHTML);

      Charts.line(host, { labels: ['a', 'b'], series: [{ key: 's', label: 'S', values: [0, 0] }], emptyText: 'nothing' });
      out.zeros = host.textContent.includes('nothing') && !host.querySelector('svg');

      Charts.bar(host, { labels: [], series: [], emptyText: 'none' });
      out.noLabels = host.textContent.includes('none');

      Charts.hbar(host, { items: [{ key: 'a', label: 'A', value: 0 }], emptyText: 'none' });
      out.hbarZero = host.textContent.includes('none');

      Charts.donut(host, { items: [{ key: 'a', label: 'A', value: 5 }] });
      out.donutSingle = host.querySelectorAll('.chart-slice').length === 1 && !/NaN/.test(host.innerHTML);

      Charts.bar(host, { labels: ['x', 'y'], series: [{ key: 's', label: 'S', values: ['abc', null], color: '#123456' }, ] });
      out.garbage = host.textContent.includes('') && !/NaN|Infinity/.test(host.innerHTML);

      Charts.hbar(host, { items: [{ key: 'a', label: 'Very long label that must be shortened to fit', value: 3 }] });
      out.truncated = host.querySelector('.chart-label').textContent.endsWith('\\u2026');
      out.fullNameKept = host.querySelector('.chart-row').getAttribute('aria-label').startsWith('Very long label that must');

      Charts.destroy(host);
      out.destroyed = host.children.length === 0;
      return out;
    }""")
    assert result == {
        'singlePoint': True, 'constant': True, 'zeros': True, 'noLabels': True, 'hbarZero': True,
        'donutSingle': True, 'garbage': True, 'truncated': True, 'fullNameKept': True, 'destroyed': True,
    }
    assert errors == []


def test_chart_ships_a_hidden_data_table_for_screen_readers(page, live_server, ui_admin, overview_data):
    _login(page, live_server, ui_admin)
    _open(page, live_server)
    table = page.locator('#ov-methods .visually-hidden table')
    assert table.count() == 1
    text = table.inner_text()
    assert 'Cash' in text and '$20.00' in text and 'Card / Transfer' in text
    assert page.locator('#ov-hours .visually-hidden table tbody tr').count() == 24
