"""Smoke test for the browser harness: the staff modal opens on a fresh page.

Validates: Requirements 3.1
"""
import pytest

pytestmark = pytest.mark.browser


def test_create_staff_modal_opens_on_fresh_page(open_users_panel, modal_state):
    page = open_users_panel()

    page.locator('#users-panel button[onclick="openCreateStaffModal()"]').click()
    page.locator('#staffModal').wait_for(state='visible')
    page.wait_for_function(
        "() => !bootstrap.Modal.getInstance(document.getElementById('staffModal'))._isTransitioning"
    )

    state = modal_state(page)
    assert state['visible'] is True
    assert state['isShown'] is True
    assert state['isTransitioning'] is False
    assert state['backdropCount'] == 1
    assert 'modal-open' in state['bodyClasses']
    assert page.locator('#staff-form-username').input_value() == ''
