"""Bug condition exploration: staff modal must open again after a successful
staff operation in the same page session.

Property 1 (Bug Condition). EXPECTED TO FAIL on unfixed code; failures are the
counterexamples. Each failure message carries Bootstrap internals
(_isTransitioning, _isShown, backdrop count, body classes).

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4
"""
import pytest

from apps.accounts.models import Role, User
from apps.tables.models import RestaurantTable
from tests.ui.conftest import STAFF_PASSWORD, read_modal_state

pytestmark = pytest.mark.browser

CREATE_BTN = '#users-panel button[onclick="openCreateStaffModal()"]'
DELAYS_MS = [0, 100, 300, 1000]


@pytest.fixture
def ui_victim(transactional_db):
    """Staff without session/payment history, so delete really removes it."""
    return User.objects.create_user(
        username='ui_victim', password=STAFF_PASSWORD, full_name='UI Victim',
        role=Role.KITCHEN, is_staff=True,
    )


def row(page, username):
    return page.locator(f'#admin-staff-tbody tr:has-text("@{username}")')


def edit_btn(page, username):
    return row(page, username).locator('button[onclick^="openEditStaffModal"]')


def assert_staff_modal_opens(page, click, mode, expect_username=None):
    """Click, then require a visible, settled #staffModal in the right mode."""
    click()
    try:
        page.locator('#staffModal').wait_for(state='visible', timeout=3000)
        page.wait_for_function(
            "() => { const i = bootstrap.Modal.getInstance(document.getElementById('staffModal'));"
            " return i && !i._isTransitioning; }",
            timeout=3000,
        )
    except Exception as exc:  # noqa: BLE001
        state = read_modal_state(page)
        pytest.fail(f'staffModal did not open ({mode}); state={state}; cause={type(exc).__name__}')
    state = read_modal_state(page)
    assert state['visible'] and state['isShown'], f'not shown ({mode}): {state}'
    if mode == 'create':
        assert page.locator('#staff-form-username').input_value() == ''
        assert page.locator('#staff-id').input_value() == ''
    else:
        assert page.locator('#staff-form-username').input_value() == expect_username


def close_staff_modal(page):
    page.locator('#staffModal [data-bs-dismiss="modal"]').first.click()
    page.locator('#staffModal').wait_for(state='hidden')
    page.wait_for_function(
        "() => { const i = bootstrap.Modal.getInstance(document.getElementById('staffModal'));"
        " return !i._isTransitioning; }"
    )


def edit_and_save(page, username, new_name):
    edit_btn(page, username).click()
    page.locator('#staffModal').wait_for(state='visible')
    page.wait_for_function(
        "() => !bootstrap.Modal.getInstance(document.getElementById('staffModal'))._isTransitioning"
    )
    page.fill('#staff-form-fullname', new_name)
    page.locator('#btn-save-staff').click()
    # Table refresh is the signal that the save finished (no wait on the modal).
    row(page, username).locator(f'text={new_name}').wait_for()


def delete_staff(page, username):
    row(page, username).locator('button[onclick^="confirmDeleteStaff"]').click()
    page.locator('#appConfirmOk').click()
    row(page, username).wait_for(state='detached')
    page.wait_for_function(
        "() => { const i = bootstrap.Modal.getInstance(document.getElementById('appConfirmModal'));"
        " return !i || (!i._isTransitioning && !i._isShown); }"
    )


@pytest.mark.parametrize('delay_ms', DELAYS_MS)
def test_create_modal_opens_after_successful_edit(open_users_panel, ui_staff, delay_ms):
    page = open_users_panel()
    edit_and_save(page, 'ui_cashier', f'Edited {delay_ms}')
    page.wait_for_timeout(delay_ms)
    assert_staff_modal_opens(page, lambda: page.locator(CREATE_BTN).click(), 'create')


@pytest.mark.parametrize('delay_ms', DELAYS_MS)
def test_edit_modal_opens_after_successful_edit(open_users_panel, ui_staff, ui_victim, delay_ms):
    page = open_users_panel()
    edit_and_save(page, 'ui_cashier', f'Edited {delay_ms}')
    page.wait_for_timeout(delay_ms)
    assert_staff_modal_opens(
        page, lambda: edit_btn(page, 'ui_victim').click(), 'edit', 'ui_victim')


def test_create_modal_opens_after_successful_create(open_users_panel):
    page = open_users_panel()
    assert_staff_modal_opens(page, lambda: page.locator(CREATE_BTN).click(), 'create')
    page.fill('#staff-form-username', 'ui_new')
    page.fill('#staff-form-password', STAFF_PASSWORD)
    page.locator('#btn-save-staff').click()
    row(page, 'ui_new').wait_for()
    page.wait_for_timeout(1000)
    assert_staff_modal_opens(page, lambda: page.locator(CREATE_BTN).click(), 'create')


def test_modals_open_after_successful_delete(open_users_panel, ui_staff, ui_victim):
    page = open_users_panel()
    delete_staff(page, 'ui_victim')
    assert_staff_modal_opens(page, lambda: page.locator(CREATE_BTN).click(), 'create')
    close_staff_modal(page)
    assert_staff_modal_opens(
        page, lambda: edit_btn(page, 'ui_cashier').click(), 'edit', 'ui_cashier')


def test_modal_opens_on_fast_click_after_save(open_users_panel, ui_staff):
    """Click immediately after the list refresh, while the hide transition runs."""
    page = open_users_panel()
    edit_and_save(page, 'ui_cashier', 'Edited fast')
    assert_staff_modal_opens(page, lambda: page.locator(CREATE_BTN).click(), 'create')


def test_comparator_table_modal_after_successful_edit(admin_page, live_server, restaurant):
    """Comparator: the same hide()-then-refresh pattern in the Tables panel."""
    RestaurantTable.objects.create(restaurant=restaurant, table_code='T-91', display_name='Meza 91')
    page = admin_page
    page.goto(f'{live_server.url}/admin-portal/#tables-panel')
    page.wait_for_function("() => window.bootstrap && typeof window.openCreateTableModal === 'function'")
    page.locator('#tables-panel').wait_for(state='visible')

    page.locator('#admin-tables-tbody button[onclick^="openEditTableModal"]').first.click()
    page.locator('#tableModal').wait_for(state='visible')
    page.wait_for_function(
        "() => !bootstrap.Modal.getInstance(document.getElementById('tableModal'))._isTransitioning"
    )
    page.fill('#table-form-name', 'Meza 91 edit')
    page.locator('#btn-save-table').click()
    page.locator('#admin-tables-tbody').locator('text=Meza 91 edit').wait_for()
    page.wait_for_timeout(300)

    page.locator('#tables-panel button[onclick="openCreateTableModal()"]').click()
    try:
        page.locator('#tableModal').wait_for(state='visible', timeout=3000)
    except Exception:  # noqa: BLE001
        pytest.fail(f'tableModal did not open; state={read_modal_state(page, "tableModal")}')
