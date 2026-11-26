"""Reports page in a real browser: shell consistency, language switching, navigation and downloads."""
import csv
import io

import pytest
from django.test import Client
from django.utils import timezone

from reporting_helpers import make_payment

pytestmark = pytest.mark.browser


def _login(page, live_server, user):
    client = Client()
    client.force_login(user)
    page.context.add_cookies([{
        'name': 'sessionid', 'value': client.cookies['sessionid'].value, 'url': live_server.url,
    }])
    page.set_default_timeout(8000)


@pytest.fixture
def report_data(transactional_db, restaurant, active_session, cashier_user):
    make_payment(restaurant, active_session, cashier_user, '12.50', paid_day=timezone.localdate())


def _open_reports(page, live_server, lang='en'):
    page.add_init_script(f"localStorage.setItem('celvas_lang', '{lang}')")
    page.goto(f'{live_server.url}/reports/')
    page.wait_for_selector('#rpt-body tr td.cell-primary')


def test_admin_reports_page_renders_in_the_admin_shell(page, live_server, ui_admin, report_data):
    _login(page, live_server, ui_admin)
    _open_reports(page, live_server)

    assert page.locator('body').get_attribute('data-role') == 'admin'
    assert page.locator('.app-nav-link.is-active').inner_text().strip() == 'Reports'
    assert page.locator('#reports-title').inner_text() == 'Reports'
    # Header cells are upper-cased by CSS, so compare case-insensitively.
    headers = [t.lower() for t in page.locator('#rpt-head th').all_inner_texts()]
    assert headers == ['date', 'transactions', 'total sales', 'average']
    assert page.locator('#rpt-summary .kpi-value').first.inner_text() == '$12.50'
    assert page.locator('#rpt-download-csv').inner_text().strip() == 'Download CSV'


def test_changing_language_retranslates_page_and_report(page, live_server, ui_admin, report_data):
    _login(page, live_server, ui_admin)
    _open_reports(page, live_server, lang='en')

    page.locator('.app-topbar-actions .dropdown > button').click()
    page.locator('[data-set-lang="id"]').click()
    page.wait_for_function("() => document.querySelector('#reports-title').textContent === 'Laporan'")
    page.wait_for_function("() => document.querySelector('#rpt-summary .kpi-label')?.textContent === 'Total penjualan'")

    assert page.locator('#rpt-download-csv').inner_text().strip() == 'Unduh CSV'
    assert page.locator('#rpt-type option[value="cashiers"]').inner_text() == 'Per kasir'
    assert 'lang=id' in page.locator('#rpt-download-csv').get_attribute('href')
    assert page.title().startswith('Laporan')


def test_selecting_another_report_and_period_updates_the_table(page, live_server, ui_admin, report_data):
    _login(page, live_server, ui_admin)
    _open_reports(page, live_server)

    page.select_option('#rpt-type', 'order_types')
    page.wait_for_function("() => document.querySelector('#rpt-head th')?.textContent === 'Order type'")
    assert 'Dine-in' in page.locator('#rpt-body').inner_text()

    page.locator('[data-preset="custom"]').click()
    assert page.locator('#rpt-custom').is_visible()
    page.fill('#rpt-start', '2026-02-01')
    page.fill('#rpt-end', '2026-01-01')
    page.locator('#rpt-form button[type="submit"]').click()
    page.wait_for_selector('#rpt-error:not([hidden])')
    assert 'end date' in page.locator('#rpt-error').inner_text().lower()
    assert 'disabled' in page.locator('#rpt-download-csv').get_attribute('class')


def test_portal_links_in_the_nav_return_to_their_panel(page, live_server, ui_admin, report_data):
    _login(page, live_server, ui_admin)
    _open_reports(page, live_server)

    page.locator('.app-nav-link[data-panel="users-panel"]').click()
    page.wait_for_url('**/admin-portal/#users-panel')
    page.locator('#users-panel').wait_for(state='visible')


def test_csv_and_pdf_downloads_work_from_the_page(page, live_server, ui_admin, report_data):
    _login(page, live_server, ui_admin)
    _open_reports(page, live_server)

    with page.expect_download() as info:
        page.locator('#rpt-download-csv').click()
    download = info.value
    assert download.suggested_filename.startswith('laporan-daily_sales-')
    rows = list(csv.reader(io.StringIO(open(download.path(), encoding='utf-8-sig').read())))
    assert rows[0] == ['Date', 'Transactions', 'Total sales', 'Average']
    assert rows[1][1:] == ['1', '12.50', '12.50']

    with page.expect_download() as info:
        page.locator('#rpt-download-pdf').click()
    pdf = info.value
    assert pdf.suggested_filename.endswith('.pdf')
    assert open(pdf.path(), 'rb').read(4) == b'%PDF'


def test_cashier_sees_cashier_shell_and_scope_notice(page, live_server, cashier_user, report_data):
    _login(page, live_server, cashier_user)
    _open_reports(page, live_server)

    assert page.locator('body').get_attribute('data-role') == 'cashier'
    assert page.locator('#reports-title').inner_text() == 'Reports'
    assert page.locator('.app-nav-link[data-panel="menu-panel"]').count() == 0

    page.select_option('#rpt-type', 'cashiers')
    page.wait_for_function("() => document.querySelector('#rpt-head th')?.textContent === 'Cashier'")
    assert page.locator('#rpt-scope').is_visible()


def test_mobile_layout_uses_tabbar_and_stacked_rows(browser, live_server, ui_admin, report_data):
    context = browser.new_context(viewport={'width': 390, 'height': 800}, service_workers='block')
    page = context.new_page()
    try:
        _login(page, live_server, ui_admin)
        _open_reports(page, live_server)

        assert page.locator('.app-tabbar').is_visible()
        assert not page.locator('.app-nav').is_visible()
        assert not page.locator('#rpt-head').is_visible()
        assert page.locator('#rpt-body td[data-label="Transactions"]').is_visible()
    finally:
        context.close()



# ============================================================================ Riwayat (history) tab

from apps.ordering.models import OrderStatus  # noqa: E402
from apps.payments.models import PaymentMethod, PaymentStatus  # noqa: E402
from reporting_helpers import add_item, make_history, make_order  # noqa: E402


@pytest.fixture
def history_data(transactional_db, restaurant, active_session, session_two, cashier_user, cashier_two, admin_user):
    today = timezone.localdate()
    mine = make_order(restaurant, active_session, OrderStatus.COMPLETED, '20.00', day=today, hour=10,
                      confirmed_by=cashier_user)
    add_item(mine, 'Ikan Saboko', 2, '10.00')
    make_history(mine, OrderStatus.CONFIRMED, cashier_user, day=today)
    theirs = make_order(restaurant, session_two, OrderStatus.REJECTED, '9.00', day=today, hour=11,
                        confirmed_by=cashier_two)
    add_item(theirs, "Batar Da'an", 1, '9.00')
    make_history(theirs, OrderStatus.REJECTED, cashier_two, 'Stok habis', day=today)
    pay_mine = make_payment(restaurant, active_session, cashier_user, '20.00', paid_day=today, hour=12)
    pay_theirs = make_payment(restaurant, session_two, cashier_two, '30.00', paid_day=today, hour=13,
                              method=PaymentMethod.MANUAL_OTHER)
    return {'mine': mine, 'theirs': theirs, 'pay_mine': pay_mine, 'pay_theirs': pay_theirs,
            'session': active_session}


def _open_history(page, live_server, hash_='#history', lang='en'):
    page.add_init_script(f"localStorage.setItem('celvas_lang', '{lang}')")
    page.goto(f'{live_server.url}/reports/{hash_}')
    page.wait_for_selector('#rpt-h-body tr[data-id], #rpt-h-body .empty-state')


def test_history_tab_lists_orders_and_filters(page, live_server, ui_admin, history_data):
    _login(page, live_server, ui_admin)
    _open_history(page, live_server)

    assert page.locator('#rpt-tab-history').get_attribute('aria-selected') == 'true'
    assert page.locator('#rpt-pane-summary').is_hidden()
    assert page.locator('#rpt-h-body tr[data-id]').count() == 2
    headers = [t.lower() for t in page.locator('#rpt-h-head th').all_inner_texts()]
    assert headers == ['time', 'code', 'table', 'status', 'items', 'total', 'staff']
    assert page.locator('#rpt-h-kpi .kpi').count() == 4
    assert page.url.endswith('#history/orders')

    page.select_option('#rpt-h-status', 'REJECTED')
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 1")
    assert history_data['theirs'].order_code in page.locator('#rpt-h-body').inner_text()

    page.select_option('#rpt-h-status', '')
    page.fill('#rpt-h-search', 'saboko')
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 1")
    assert history_data['mine'].order_code in page.locator('#rpt-h-body').inner_text()

    page.fill('#rpt-h-search', 'zzzz')
    # The loading placeholder is also an .empty-state, so wait for the actual message.
    page.wait_for_function("() => document.getElementById('rpt-h-body').textContent.includes('No results match these filters.')")


def test_history_switches_to_transactions_and_back(page, live_server, ui_admin, history_data):
    _login(page, live_server, ui_admin)
    _open_history(page, live_server)

    page.locator('[data-kind="transactions"]').click()
    page.wait_for_function("() => document.querySelector('#rpt-h-head th')?.textContent.trim().toLowerCase() === 'time'")
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 2")
    assert page.url.endswith('#history/transactions')
    assert page.locator('#rpt-h-type-wrap').is_visible()
    assert page.locator('#rpt-h-source-wrap').is_hidden()

    page.select_option('#rpt-h-type', 'TAKEAWAY')
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 0")
    page.select_option('#rpt-h-type', 'DINE_IN')
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 2")

    # Switching list resets the filters; both lists show 2 rows, so wait for the reset itself
    page.locator('[data-kind="orders"]').click()
    page.wait_for_function("() => document.getElementById('rpt-h-type').value === ''")
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 2")
    assert page.locator('#rpt-h-type').input_value() == ''


def test_history_sorting_and_pagination(page, live_server, ui_admin, restaurant, active_session):
    today = timezone.localdate()
    for i in range(25):
        make_order(restaurant, active_session, OrderStatus.COMPLETED, f'{i + 1}.00', day=today, hour=8, minute=i)
    _login(page, live_server, ui_admin)
    _open_history(page, live_server)

    assert page.locator('#rpt-h-body tr[data-id]').count() == 20
    assert 'Page 1 of 2' in page.locator('#rpt-h-pageinfo').inner_text()
    assert page.locator('#rpt-h-prev').is_disabled()

    page.locator('#rpt-h-next').click()
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 5")
    assert 'Page 2 of 2' in page.locator('#rpt-h-pageinfo').inner_text()
    assert page.locator('#rpt-h-next').is_disabled()

    page.locator('#rpt-h-prev').click()
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 20")

    page.locator('[data-sort="total"]').click()
    page.wait_for_function("() => document.querySelector('#rpt-h-head th[aria-sort=\"ascending\"]') !== null")
    first = page.locator('#rpt-h-body tr[data-id] td[data-label="Total"]').first.inner_text()
    assert first == '$1.00'


def test_order_detail_opens_closes_and_reopens_repeatedly(page, live_server, ui_admin, history_data):
    _login(page, live_server, ui_admin)
    _open_history(page, live_server)
    code = history_data['mine'].order_code

    for _ in range(3):
        page.locator(f'#rpt-h-body tr:has-text("{code}")').click()
        page.wait_for_selector('#rptDetail.show')
        page.wait_for_selector('#rpt-detail-body .rpt-timeline li')
        body = page.locator('#rpt-detail-body').inner_text()
        assert code in body and 'Ikan Saboko' in body and '$20.00' in body
        assert page.locator('#rpt-detail-body .rpt-timeline li').count() == 1
        page.locator('#rptDetail .btn-close').click()
        page.wait_for_function("() => !document.getElementById('rptDetail').classList.contains('show')")
        page.wait_for_function("() => document.querySelectorAll('.offcanvas-backdrop').length === 0")

    other = history_data['theirs'].order_code
    page.locator(f'#rpt-h-body tr:has-text("{other}")').click()
    page.wait_for_selector('#rptDetail.show')
    assert 'Stok habis' in page.locator('#rpt-detail-body').inner_text()


def test_detail_opens_with_the_keyboard(page, live_server, ui_admin, history_data):
    _login(page, live_server, ui_admin)
    _open_history(page, live_server)
    page.locator('#rpt-h-body tr[data-id]').first.focus()
    page.keyboard.press('Enter')
    page.wait_for_selector('#rptDetail.show')
    assert page.locator('#rpt-detail-body h3').count() == 1


def test_transaction_detail_prints_the_receipt(page, live_server, ui_admin, history_data):
    _login(page, live_server, ui_admin)
    _open_history(page, live_server, '#history/transactions')
    code = history_data['pay_mine'].payment_code
    page.evaluate("""() => {
      Receipt.printer = (win) => { window.__printed = win.document.body.innerText; win.close(); };
    }""")

    page.locator(f'#rpt-h-body tr:has-text("{code}")').click()
    page.wait_for_selector('#rpt-print-receipt')
    page.locator('#rpt-print-receipt').click()
    page.wait_for_function('() => window.__printed')
    printed = page.evaluate('() => window.__printed')
    assert code in printed and 'Ikan Saboko' in printed and '20.00' in printed


def test_voided_transaction_detail_has_no_receipt_button(page, live_server, ui_admin, history_data, admin_user,
                                                         restaurant):
    void = make_payment(restaurant, history_data['session'], admin_user, '5.00',
                        paid_day=timezone.localdate(), hour=15, status=PaymentStatus.VOIDED,
                        void_reason='Salah input', voided_by=admin_user)
    _login(page, live_server, ui_admin)
    _open_history(page, live_server, '#history/transactions')
    page.locator(f'#rpt-h-body tr:has-text("{void.payment_code}")').click()
    page.wait_for_selector('#rptDetail.show')
    page.wait_for_selector('#rpt-detail-body h3')
    body = page.locator('#rpt-detail-body').inner_text()
    assert 'Salah input' in body and 'Voided payments have no receipt.' in body
    assert page.locator('#rpt-print-receipt').count() == 0


def test_cashier_sees_only_their_history_and_no_cashier_filter(page, live_server, cashier_user, history_data):
    _login(page, live_server, cashier_user)
    _open_history(page, live_server)

    assert page.locator('#rpt-h-body tr[data-id]').count() == 1
    assert history_data['mine'].order_code in page.locator('#rpt-h-body').inner_text()
    assert page.locator('#rpt-h-cashier').count() == 0
    assert page.locator('#rpt-scope').is_visible()
    assert page.locator('#rpt-h-status option[value="WAITING_CASHIER_CONFIRMATION"]').count() == 0


def test_cashier_reaches_history_through_the_reports_tab(page, live_server, cashier_user, history_data):
    _login(page, live_server, cashier_user)
    page.add_init_script("localStorage.setItem('celvas_lang', 'en')")
    page.goto(f'{live_server.url}/cashier/')
    page.wait_for_selector('#pos-panel')
    # The portal polls several endpoints on load; leaving mid-flight can trip the in-memory SQLite test database.
    page.wait_for_load_state('networkidle')

    # Five menus: POS, Service desk, Menu & Stock, Shift, Reports. History is a tab inside Reports.
    assert page.locator('.app-nav-link[data-nav="history"]').count() == 0
    assert page.locator('.app-nav .app-nav-link').count() == 5
    assert page.locator('#orders-history-panel').count() == 0

    page.locator('.app-nav-link[data-nav="reports"]').click()
    page.wait_for_selector('#rpt-pane-summary:not([hidden])')
    page.locator('#rpt-tab-history').click()
    page.wait_for_selector('#rpt-h-body tr[data-id]')
    assert page.locator('#rpt-tab-audit').count() == 0


def test_old_history_bookmark_is_redirected(page, live_server, cashier_user, history_data):
    _login(page, live_server, cashier_user)
    page.goto(f'{live_server.url}/cashier/#orders-history-panel')
    page.wait_for_url('**/reports/#history**')
    page.wait_for_selector('#rpt-h-body tr[data-id], #rpt-h-body .empty-state')
    assert page.locator('#rpt-tab-history').get_attribute('aria-selected') == 'true'


def test_pos_receipt_printing_still_works_after_extraction(page, live_server, cashier_user, history_data):
    _login(page, live_server, cashier_user)
    page.add_init_script("localStorage.setItem('celvas_lang', 'en')")
    page.goto(f'{live_server.url}/cashier/')
    page.wait_for_load_state('networkidle')
    denied = []
    page.on('response', lambda r: denied.append((r.url, r.status)) if r.status in (401, 403) else None)
    # `cashierApp` is a top-level const in the portal script, so it is not a property of window.
    page.wait_for_function("() => typeof cashierApp !== 'undefined' && typeof Receipt !== 'undefined'")
    page.evaluate("""() => {
      Receipt.printer = (win) => { window.__printed = win.document.body.innerText; win.close(); };
    }""")
    session_id = str(history_data['session'].id)

    page.evaluate(f"() => cashierApp.viewReceiptForSession('{session_id}')")
    page.wait_for_selector('#receiptModal.show #printable-receipt')
    assert history_data['pay_mine'].payment_code in page.locator('#printable-receipt').inner_text()

    page.locator('#receiptModal .btn-primary').click()
    page.wait_for_function('() => window.__printed')
    assert history_data['pay_mine'].payment_code in page.evaluate('() => window.__printed')
    assert denied == []


def test_summary_rows_drill_down_into_history(page, live_server, ui_admin, history_data):
    _login(page, live_server, ui_admin)
    _open_reports(page, live_server)

    assert page.locator('#rpt-drill-hint').is_visible()
    page.locator('#rpt-body tr[data-drill]').first.click()
    page.wait_for_url('**/reports/#history/transactions**')
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 2")
    assert page.locator('#rpt-tab-history').get_attribute('aria-selected') == 'true'
    assert page.locator('#rpt-h-status').input_value() == 'COMPLETED'
    assert page.locator('#rpt-custom').is_visible()

    page.go_back()
    page.wait_for_selector('#rpt-pane-summary:not([hidden])')


def test_history_follows_language_changes(page, live_server, ui_admin, history_data):
    _login(page, live_server, ui_admin)
    _open_history(page, live_server, lang='en')
    page.locator('#rpt-h-body tr[data-id]').first.click()
    page.wait_for_selector('#rpt-detail-body h3')

    # The open panel's backdrop covers the account menu, so switch the language the way the menu does.
    page.evaluate("() => setLanguage('pt')")
    page.wait_for_function("() => document.querySelector('#rpt-tab-history').textContent.includes('Histórico')")
    page.wait_for_function("() => document.querySelector('#rpt-h-head th')?.textContent.trim().toLowerCase() === 'hora'")
    assert page.locator('#rpt-sub-orders, [data-kind="orders"]').first.inner_text().strip() == 'Pedidos'
    assert 'Concluído' in page.locator('#rpt-h-body').inner_text()
    assert page.locator('#rpt-h-status option[value="COMPLETED"]').inner_text() == 'Concluído'
    # The open detail panel is reloaded in the new language.
    page.wait_for_function("() => document.querySelector('#rpt-detail-body')?.textContent.includes('Itens do pedido')")


def test_history_download_links_follow_filters(page, live_server, ui_admin, history_data):
    _login(page, live_server, ui_admin)
    _open_history(page, live_server)
    page.select_option('#rpt-h-status', 'COMPLETED')
    page.wait_for_function("() => document.querySelectorAll('#rpt-h-body tr[data-id]').length === 1")
    href = page.locator('#rpt-download-csv').get_attribute('href')
    assert 'type=orders_history' in href and 'status=COMPLETED' in href and 'lang=en' in href

    with page.expect_download() as info:
        page.locator('#rpt-download-csv').click()
    path = info.value.path()
    rows = list(csv.reader(io.StringIO(open(path, encoding='utf-8-sig').read())))
    assert rows[0][:3] == ['Time', 'Code', 'Table']
    assert [r[1] for r in rows[1:]] == [history_data['mine'].order_code]


def test_history_on_a_phone_stacks_rows_and_opens_detail(browser, live_server, ui_admin, history_data):
    context = browser.new_context(viewport={'width': 390, 'height': 800}, service_workers='block')
    page = context.new_page()
    try:
        _login(page, live_server, ui_admin)
        _open_history(page, live_server)
        assert page.locator('.app-tabbar').is_visible()
        assert not page.locator('#rpt-h-head').is_visible()
        assert page.locator('#rpt-h-body td[data-label="Status"]').first.is_visible()
        page.locator('#rpt-h-body tr[data-id]').first.click()
        page.wait_for_selector('#rptDetail.show')
        box = page.locator('#rptDetail').bounding_box()
        assert box['width'] <= 390
    finally:
        context.close()
