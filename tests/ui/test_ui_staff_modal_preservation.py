"""Preservation: behaviour that must NOT change when the staff modal bug is fixed.

Property 2 (Preservation). Observation-first: these tests describe what the
UNFIXED code already does for inputs where ``isBugCondition`` is false (no
successful staff operation precedes the modal open). They must pass before and
after the fix.

The property test draws random action sequences from {open-create, open-edit,
save-invalid, cancel, cancel-delete, switch-panel}, optionally ended by one
valid save. ``random.Random(seed)`` is used instead of Hypothesis, which is not
installed in the repo; seeds are fixed so runs are reproducible.

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7
"""
import random

import pytest

from apps.accounts.models import User
from tests.ui.conftest import STAFF_PASSWORD, read_modal_state

pytestmark = pytest.mark.browser

CREATE_BTN = '#users-panel button[onclick="openCreateStaffModal()"]'
SETTLED_JS = (
    "(id) => { const e = document.getElementById(id);"
    " const i = e && bootstrap.Modal.getInstance(e);"
    " return !i || !i._isTransitioning; }"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def row(page, username):
    return page.locator(f'#admin-staff-tbody tr:has-text("@{username}")')


def edit_btn(page, username):
    return row(page, username).locator('button[onclick^="openEditStaffModal"]')


def delete_btn(page, username):
    return row(page, username).locator('button[onclick^="confirmDeleteStaff"]')


def wait_settled(page, modal_id):
    page.wait_for_function(SETTLED_JS, arg=modal_id)


def clear_toasts(page):
    page.evaluate("() => document.querySelectorAll('#toast-container .app-toast').forEach(e => e.remove())")


def toast(page, tone):
    loc = page.locator(f'#toast-container .app-toast.tone-{tone}')
    loc.first.wait_for()
    return loc.first.inner_text()


def open_staff_modal(page, mode, username=None):
    if mode == 'create':
        page.locator(CREATE_BTN).click()
    else:
        edit_btn(page, username).click()
    page.locator('#staffModal').wait_for(state='visible')
    wait_settled(page, 'staffModal')
    state = read_modal_state(page)
    assert state['visible'] and state['isShown'], state
    assert state['backdropCount'] == 1 and 'modal-open' in state['bodyClasses'], state
    if mode == 'create':
        assert page.locator('#staff-id').input_value() == ''
        assert page.locator('#staff-form-username').input_value() == ''
    else:
        assert page.locator('#staff-form-username').input_value() == username


def cancel_staff_modal(page):
    page.locator('#staffModal [data-bs-dismiss="modal"]').first.click()
    page.locator('#staffModal').wait_for(state='hidden')
    wait_settled(page, 'staffModal')
    assert_no_leftovers(page)


def assert_no_leftovers(page):
    # Bootstrap removes the backdrop only after its own fade, which ends after the modal stops transitioning.
    try:
        page.wait_for_function(
            "() => !document.querySelector('.modal-backdrop') && !document.body.classList.contains('modal-open')",
            timeout=3000,
        )
    except Exception:
        pass  # fall through to the assertion so the failure shows the full state
    state = read_modal_state(page)
    assert state['backdropCount'] == 0, state
    assert 'modal-open' not in state['bodyClasses'], state


def save_staff(page):
    page.locator('#btn-save-staff').click()


def assert_save_button_ready(page):
    page.wait_for_function(
        "() => { const b = document.getElementById('btn-save-staff');"
        " return !b.disabled && !b.querySelector('.spinner-border'); }"
    )


def fill_create(page, username, password):
    page.fill('#staff-form-username', username)
    page.fill('#staff-form-password', password)


# invalid submit variants: (username, password, expected toast tone)
INVALID_CREATES = {
    'empty_username': ('', STAFF_PASSWORD, 'warning'),
    'short_password': ('ui_short', 'abc', 'warning'),
    'missing_password': ('ui_nopass', '', 'warning'),
    'duplicate_username': ('ui_cashier', STAFF_PASSWORD, 'error'),
}


def submit_invalid_create(page, kind):
    username, password, tone = INVALID_CREATES[kind]
    clear_toasts(page)
    fill_create(page, username, password)
    save_staff(page)
    toast(page, tone)
    assert read_modal_state(page)['visible'], f'modal closed after invalid submit ({kind})'
    assert_save_button_ready(page)


# --------------------------------------------------------------------------- #
# 3.1 Fresh page
# --------------------------------------------------------------------------- #
def test_fresh_page_create_and_edit_open(open_users_panel, ui_staff):
    page = open_users_panel()
    open_staff_modal(page, 'create')
    cancel_staff_modal(page)
    open_staff_modal(page, 'edit', 'ui_cashier')
    assert page.locator('#staff-form-fullname').input_value() == 'UI Kaixa'
    assert page.locator('#staff-form-role').input_value() == 'CASHIER'


# --------------------------------------------------------------------------- #
# 3.3 Invalid submits keep the modal open
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('kind', list(INVALID_CREATES))
def test_invalid_create_keeps_modal_open(open_users_panel, ui_staff, kind):
    page = open_users_panel()
    open_staff_modal(page, 'create')
    submit_invalid_create(page, kind)
    assert not User.objects.filter(username__in=['ui_short', 'ui_nopass']).exists()
    assert User.objects.filter(username='ui_cashier').count() == 1


def test_invalid_edit_short_password_keeps_modal_open(open_users_panel, ui_staff):
    page = open_users_panel()
    open_staff_modal(page, 'edit', 'ui_cashier')
    clear_toasts(page)
    page.fill('#staff-form-password', 'abc')
    save_staff(page)
    toast(page, 'warning')
    assert read_modal_state(page)['visible']
    assert_save_button_ready(page)


# --------------------------------------------------------------------------- #
# 3.2 Valid save, 3.5 locked delete buttons after refresh
# --------------------------------------------------------------------------- #
def test_valid_edit_persists_closes_and_refreshes(open_users_panel, ui_staff, staff_accounts):
    page = open_users_panel()
    open_staff_modal(page, 'edit', 'ui_cashier')
    clear_toasts(page)
    page.fill('#staff-form-fullname', 'Kaixa Foun')
    save_staff(page)
    toast(page, 'success')
    page.locator('#staffModal').wait_for(state='hidden')
    row(page, 'ui_cashier').locator('text=Kaixa Foun').wait_for()
    wait_settled(page, 'staffModal')
    assert_no_leftovers(page)
    ui_staff.refresh_from_db()
    assert ui_staff.full_name == 'Kaixa Foun'

    # 3.5: after the list refresh own account and superuser stay locked.
    assert delete_btn(page, 'ui_admin').is_disabled()
    assert delete_btn(page, 'ui_root').is_disabled()
    assert delete_btn(page, 'ui_cashier').is_enabled()


def test_valid_create_persists_closes_and_lists(open_users_panel):
    page = open_users_panel()
    open_staff_modal(page, 'create')
    clear_toasts(page)
    fill_create(page, 'ui_new', STAFF_PASSWORD)
    save_staff(page)
    toast(page, 'success')
    page.locator('#staffModal').wait_for(state='hidden')
    row(page, 'ui_new').wait_for()
    assert User.objects.filter(username='ui_new').exists()
    assert delete_btn(page, 'ui_admin').is_disabled()


# --------------------------------------------------------------------------- #
# 3.4 Delete: confirmation first, delete vs deactivate
# --------------------------------------------------------------------------- #
def test_delete_asks_confirmation_and_cancel_keeps_row(open_users_panel, ui_staff):
    page = open_users_panel()
    delete_btn(page, 'ui_cashier').click()
    page.locator('#appConfirmModal').wait_for(state='visible')
    wait_settled(page, 'appConfirmModal')
    page.locator('#appConfirmCancel').click()
    page.locator('#appConfirmModal').wait_for(state='hidden')
    wait_settled(page, 'appConfirmModal')
    assert row(page, 'ui_cashier').count() == 1
    assert User.objects.filter(username='ui_cashier').exists()
    assert_no_leftovers(page)


def test_delete_without_history_removes_account(open_users_panel, ui_staff):
    page = open_users_panel()
    delete_btn(page, 'ui_cashier').click()
    page.locator('#appConfirmOk').click()
    row(page, 'ui_cashier').wait_for(state='detached')
    assert not User.objects.filter(username='ui_cashier').exists()


def test_delete_with_history_deactivates_account(open_users_panel, active_session):
    """active_session was opened by cashier_test, so that account has history."""
    page = open_users_panel()
    clear_toasts(page)
    delete_btn(page, 'cashier_test').click()
    page.locator('#appConfirmOk').click()
    toast(page, 'success')
    page.wait_for_function(
        "() => document.querySelector('#admin-staff-tbody tr.opacity-75') !== null"
    )
    user = User.objects.get(username='cashier_test')
    assert user.is_active is False
    assert row(page, 'cashier_test').count() == 1


# --------------------------------------------------------------------------- #
# 3.6 Other panels' modals
# --------------------------------------------------------------------------- #
def goto_panel(page, panel_id):
    if panel_id == 'categories-panel':
        # Categories is a tab of the Katalog menu, not a menu of its own.
        goto_panel(page, 'menu-panel')
        page.locator('#menu-panel .segmented [data-panel="categories-panel"]').click()
    else:
        page.locator(f'nav.app-nav [data-panel="{panel_id}"]').click()
    page.locator(f'#{panel_id}').wait_for(state='visible')


@pytest.mark.parametrize('panel_id,button,modal_id', [
    ('menu-panel', '#menu-panel button[onclick="openCreateMenuItemModal()"]', 'menuItemModal'),
    ('categories-panel', '#categories-panel button[onclick="openCreateCategoryModal()"]', 'categoryModal'),
    ('tables-panel', '#tables-panel button[onclick="openCreateTableModal()"]', 'tableModal'),
])
def test_other_panel_modals_open_and_close(open_users_panel, panel_id, button, modal_id):
    page = open_users_panel()
    goto_panel(page, panel_id)
    page.locator(button).click()
    page.locator(f'#{modal_id}').wait_for(state='visible')
    wait_settled(page, modal_id)
    assert read_modal_state(page, modal_id)['isShown']
    page.locator(f'#{modal_id} [data-bs-dismiss="modal"]').first.click()
    page.locator(f'#{modal_id}').wait_for(state='hidden')
    wait_settled(page, modal_id)
    assert_no_leftovers(page)


def test_table_modal_saves_and_closes(open_users_panel, restaurant):
    page = open_users_panel()
    goto_panel(page, 'tables-panel')
    page.locator('#tables-panel button[onclick="openCreateTableModal()"]').click()
    page.locator('#tableModal').wait_for(state='visible')
    wait_settled(page, 'tableModal')
    clear_toasts(page)
    page.fill('#table-form-code', 'T-77')
    page.fill('#table-form-name', 'Meza 77')
    page.locator('#btn-save-table').click()
    toast(page, 'success')
    page.locator('#tableModal').wait_for(state='hidden')
    page.locator('#admin-tables-tbody').locator('text=Meza 77').wait_for()


# --------------------------------------------------------------------------- #
# 3.7 Panel navigation and URL hash
# --------------------------------------------------------------------------- #
def test_panel_switch_shows_panel_and_updates_hash(open_users_panel):
    page = open_users_panel()
    for panel_id in ['menu-panel', 'tables-panel', 'categories-panel', 'users-panel']:
        goto_panel(page, panel_id)
        assert page.evaluate('() => location.hash') == f'#{panel_id}'
        visible = page.evaluate(
            "() => Array.from(document.querySelectorAll('.app-panel'))"
            ".filter(p => !p.hidden).map(p => p.id)"
        )
        assert visible == [panel_id]


# --------------------------------------------------------------------------- #
# Property: random non-bug-condition action sequences
# --------------------------------------------------------------------------- #
CLOSED_ACTIONS = ['open_create', 'open_edit', 'cancel_delete', 'switch_panel']
OPEN_ACTIONS = ['save_invalid', 'cancel']
SWITCH_TARGETS = ['menu-panel', 'tables-panel', 'categories-panel']


def build_sequence(seed):
    """3-6 actions with no successful staff operation; may end with one valid save
    (nothing is opened afterwards, so isBugCondition stays false throughout)."""
    rng = random.Random(seed)
    steps, is_open = [], False
    for _ in range(rng.randint(3, 6)):
        if is_open:
            action = rng.choice(OPEN_ACTIONS)
            is_open = action == 'save_invalid'
        else:
            action = rng.choice(CLOSED_ACTIONS)
            is_open = action.startswith('open_')
        steps.append((action, rng.choice(list(INVALID_CREATES)), rng.choice(SWITCH_TARGETS)))
    return steps, is_open, rng.random() < 0.5


@pytest.mark.parametrize('seed', range(8))
def test_random_sequences_without_prior_success_behave_as_observed(open_users_panel, ui_staff, seed):
    steps, ends_open, finish_with_save = build_sequence(seed)
    page = open_users_panel()
    mode = None  # 'create' | 'edit' while the modal is open

    for action, invalid_kind, switch_target in steps:
        if action == 'open_create':
            open_staff_modal(page, 'create')
            mode = 'create'
        elif action == 'open_edit':
            open_staff_modal(page, 'edit', 'ui_cashier')
            mode = 'edit'
        elif action == 'save_invalid':
            if mode == 'create':
                submit_invalid_create(page, invalid_kind)
            else:
                clear_toasts(page)
                page.fill('#staff-form-password', 'abc')
                save_staff(page)
                toast(page, 'warning')
                assert read_modal_state(page)['visible']
                assert_save_button_ready(page)
        elif action == 'cancel':
            cancel_staff_modal(page)
            mode = None
        elif action == 'cancel_delete':
            delete_btn(page, 'ui_cashier').click()
            page.locator('#appConfirmModal').wait_for(state='visible')
            wait_settled(page, 'appConfirmModal')
            page.locator('#appConfirmCancel').click()
            page.locator('#appConfirmModal').wait_for(state='hidden')
            wait_settled(page, 'appConfirmModal')
            assert row(page, 'ui_cashier').count() == 1
            assert_no_leftovers(page)
        elif action == 'switch_panel':
            goto_panel(page, switch_target)
            assert page.evaluate('() => location.hash') == f'#{switch_target}'
            goto_panel(page, 'users-panel')
            assert page.evaluate('() => location.hash') == '#users-panel'

    # Optional terminal valid save, only when the staff modal is still open in create mode.
    if finish_with_save and mode == 'create':
        clear_toasts(page)
        fill_create(page, f'ui_seq{seed}', STAFF_PASSWORD)
        save_staff(page)
        toast(page, 'success')
        page.locator('#staffModal').wait_for(state='hidden')
        row(page, f'ui_seq{seed}').wait_for()
        assert User.objects.filter(username=f'ui_seq{seed}').exists()
    elif mode is not None:
        cancel_staff_modal(page)

    assert User.objects.filter(username='ui_cashier').count() == 1
