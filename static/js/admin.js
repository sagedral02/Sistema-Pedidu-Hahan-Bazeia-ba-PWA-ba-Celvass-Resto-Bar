/**
 * Admin portal: menu & category CRUD, stock toggles, QR rotation and staff creation.
 */
(function () {
  'use strict';

  const DEFAULT_IMG = '/static/icons/default-food.png';
  let currentActiveMenuCat = 'all';

  const $ = (id) => document.getElementById(id);
  const modal = (id) => bootstrap.Modal.getOrCreateInstance($(id));

  function reloadSoon(hash) {
    setTimeout(() => {
      if (hash) location.hash = hash;
      location.reload();
    }, 500);
  }

  function setBusy(btn, busy) {
    if (!btn) return;
    if (busy) {
      btn.dataset.label = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>';
    } else {
      btn.disabled = false;
      if (btn.dataset.label) btn.innerHTML = btn.dataset.label;
    }
  }

  // ------------------------------------------------------------------
  // Menu filter
  // ------------------------------------------------------------------
  function filterAdminMenuByCategory(catId) {
    currentActiveMenuCat = String(catId || 'all');
    sessionStorage.setItem('celvass_admin_active_cat', currentActiveMenuCat);
    document.querySelectorAll('.admin-cat-pill').forEach((b) => {
      b.classList.toggle('active', b.dataset.cat === currentActiveMenuCat);
    });
    filterAdminMenuItems();
  }

  function filterAdminMenuItems() {
    const q = ($('admin-menu-search')?.value || '').toLowerCase().trim();
    let visible = 0;
    document.querySelectorAll('.admin-menu-row').forEach((row) => {
      const show =
        (currentActiveMenuCat === 'all' || row.dataset.catId === currentActiveMenuCat) &&
        (!q || (row.dataset.search || '').includes(q));
      row.hidden = !show;
      if (show) visible += 1;
    });
    const empty = $('admin-menu-empty-filtered');
    const hasRows = document.querySelectorAll('.admin-menu-row').length > 0;
    if (empty) empty.hidden = !hasRows || visible > 0;
  }

  function goToCategoryInMenuPanel(catId) {
    showPanel('menu-panel');
    filterAdminMenuByCategory(catId);
  }

  // ------------------------------------------------------------------
  // Menu item form
  // ------------------------------------------------------------------
  function resetMenuForm() {
    ['menu-item-id', 'menu-form-name', 'menu-form-price', 'menu-form-sku', 'menu-form-desc', 'menu-form-prepnote', 'menu-form-imgurl', 'menu-form-imgfile'].forEach((id) => {
      $(id).value = '';
    });
    $('menu-form-sort').value = '0';
    $('menu-form-availability').value = 'AVAILABLE';
    $('menu-form-featured').checked = false;
    $('menu-image-preview').src = DEFAULT_IMG;
    document.querySelectorAll('#menu-item-form .is-invalid').forEach((el) => el.classList.remove('is-invalid'));
  }

  function openCreateMenuItemModal(preselectedCatId) {
    resetMenuForm();
    $('menu-modal-title').textContent = t('btn_add_menu');
    const target = preselectedCatId || (currentActiveMenuCat !== 'all' ? currentActiveMenuCat : '');
    $('menu-form-category').value = target || '';
    if (!target) $('menu-form-category').selectedIndex = 0;
    modal('menuItemModal').show();
  }

  async function openEditMenuItemModal(itemId) {
    try {
      const item = await apiGet(`/api/v1/admin/menu-items/${itemId}/`);
      resetMenuForm();
      $('menu-modal-title').textContent = `${t('btn_edit_menu')}: ${item.name}`;
      $('menu-item-id').value = item.id;
      $('menu-form-name').value = item.name;
      $('menu-form-category').value = item.category_id;
      $('menu-form-price').value = item.price;
      $('menu-form-sku').value = item.sku || '';
      $('menu-form-sort').value = item.sort_order || 0;
      $('menu-form-desc').value = item.description || '';
      $('menu-form-prepnote').value = item.preparation_note || '';
      $('menu-form-imgurl').value = item.image_url || '';
      $('menu-form-availability').value = item.availability || 'AVAILABLE';
      $('menu-form-featured').checked = Boolean(item.is_featured);
      $('menu-image-preview').src = item.display_image || DEFAULT_IMG;
      modal('menuItemModal').show();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  function updateMenuImagePreview(url) {
    const value = (url || '').trim();
    $('menu-image-preview').src = /^https?:\/\//i.test(value) ? value : DEFAULT_IMG;
  }

  function handleMenuFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      showToast(t('validation_invalid_input'), 'warning');
      event.target.value = '';
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      $('menu-image-preview').src = e.target.result;
    };
    reader.readAsDataURL(file);
  }

  async function submitMenuItemForm() {
    const itemId = $('menu-item-id').value;
    const name = $('menu-form-name').value.trim();
    const categoryId = $('menu-form-category').value;
    const price = $('menu-form-price').value;

    const invalid = [];
    if (!name) invalid.push('menu-form-name');
    if (!categoryId) invalid.push('menu-form-category');
    if (price === '' || Number(price) < 0) invalid.push('menu-form-price');
    document.querySelectorAll('#menu-item-form .is-invalid').forEach((el) => el.classList.remove('is-invalid'));
    if (invalid.length) {
      invalid.forEach((id) => $(id).classList.add('is-invalid'));
      $(invalid[0]).focus();
      showToast(t('validation_required'), 'warning');
      return;
    }

    const btn = $('btn-save-menu');
    setBusy(btn, true);
    try {
      const fd = new FormData();
      fd.append('name', name);
      fd.append('category_id', categoryId);
      fd.append('price', price);
      fd.append('sku', $('menu-form-sku').value.trim());
      fd.append('sort_order', $('menu-form-sort').value || '0');
      fd.append('description', $('menu-form-desc').value.trim());
      fd.append('preparation_note', $('menu-form-prepnote').value.trim());
      fd.append('image_url', $('menu-form-imgurl').value.trim());
      fd.append('availability', $('menu-form-availability').value);
      fd.append('is_featured', $('menu-form-featured').checked ? 'true' : 'false');
      const file = $('menu-form-imgfile').files[0];
      if (file) fd.append('image', file);

      await apiRequest(itemId ? `/api/v1/admin/menu-items/${itemId}/` : '/api/v1/admin/menu-items/', {
        method: itemId ? 'PATCH' : 'POST',
        body: fd,
      });
      showToast(itemId ? t('menu_updated_success') : t('menu_created_success'), 'success');
      modal('menuItemModal').hide();
      reloadSoon('#menu-panel');
    } catch (e) {
      showToast(e.message, 'error');
      setBusy(btn, false);
    }
  }

  async function confirmDeleteMenuItem(itemId, itemName) {
    const ok = await confirmDialog({
      title: t('btn_delete_menu'),
      message: `${t('confirm_delete_menu_msg')} ${itemName}`,
      confirmText: t('delete'),
      tone: 'danger',
    });
    if (!ok) return;
    try {
      await apiRequest(`/api/v1/admin/menu-items/${itemId}/`, { method: 'DELETE' });
      showToast(t('menu_deleted_success'), 'success');
      reloadSoon('#menu-panel');
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function toggleMenuItem(itemId, btn) {
    setBusy(btn, true);
    try {
      await apiPost(`/api/v1/admin/menu-items/${itemId}/toggle-availability/`);
      showToast(t('stock_updated'), 'success');
      reloadSoon('#menu-panel');
    } catch (e) {
      showToast(e.message, 'error');
      setBusy(btn, false);
    }
  }

  // ------------------------------------------------------------------
  // Category form
  // ------------------------------------------------------------------
  function openCreateCategoryModal() {
    $('cat-modal-title').textContent = t('btn_add_category');
    $('category-id').value = '';
    $('cat-form-name').value = '';
    $('cat-form-slug').value = '';
    $('cat-form-icon').value = 'utensils';
    $('cat-form-sort').value = '0';
    $('cat-form-active').checked = true;
    $('cat-form-desc').value = '';
    updateCatIconPreview('utensils');
    modal('categoryModal').show();
  }

  async function openEditCategoryModal(catId) {
    try {
      const cat = await apiGet(`/api/v1/admin/categories/${catId}/`);
      $('cat-modal-title').textContent = `${t('btn_edit_category')}: ${cat.name}`;
      $('category-id').value = cat.id;
      $('cat-form-name').value = cat.name;
      $('cat-form-slug').value = cat.slug;
      $('cat-form-icon').value = cat.icon_name || 'utensils';
      $('cat-form-sort').value = cat.sort_order || 0;
      $('cat-form-active').checked = Boolean(cat.is_active);
      $('cat-form-desc').value = cat.description || '';
      updateCatIconPreview(cat.icon_name || 'utensils');
      modal('categoryModal').show();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  function autoSlugCategory(value) {
    if ($('category-id').value) return;
    $('cat-form-slug').value = value
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9\s-]/g, '')
      .trim()
      .replace(/[\s_-]+/g, '-');
  }

  function updateCatIconPreview(icon) {
    const safe = String(icon || 'utensils').replace(/[^a-z0-9-]/gi, '') || 'utensils';
    $('cat-icon-preview').innerHTML = `<i class="fa-solid fa-${safe}" aria-hidden="true"></i>`;
  }

  function selectCatIconPreset(icon) {
    $('cat-form-icon').value = icon;
    updateCatIconPreview(icon);
  }

  async function submitCategoryForm() {
    const catId = $('category-id').value;
    const name = $('cat-form-name').value.trim();
    if (!name) {
      $('cat-form-name').classList.add('is-invalid');
      showToast(t('validation_required'), 'warning');
      return;
    }
    $('cat-form-name').classList.remove('is-invalid');

    const btn = $('btn-save-cat');
    setBusy(btn, true);
    try {
      await apiRequest(catId ? `/api/v1/admin/categories/${catId}/` : '/api/v1/admin/categories/', {
        method: catId ? 'PATCH' : 'POST',
        body: JSON.stringify({
          name,
          slug: $('cat-form-slug').value.trim(),
          icon_name: ($('cat-form-icon').value.trim() || 'utensils').replace(/[^a-z0-9-]/gi, ''),
          sort_order: parseInt($('cat-form-sort').value || '0', 10),
          is_active: $('cat-form-active').checked,
          description: $('cat-form-desc').value.trim(),
        }),
      });
      showToast(catId ? t('category_updated_success') : t('category_created_success'), 'success');
      modal('categoryModal').hide();
      reloadSoon('#categories-panel');
    } catch (e) {
      showToast(e.message, 'error');
      setBusy(btn, false);
    }
  }

  async function toggleCategoryStatus(catId, input) {
    if (input) input.disabled = true;
    try {
      await apiPost(`/api/v1/admin/categories/${catId}/toggle-status/`);
      showToast(t('category_updated_success'), 'success');
      reloadSoon('#categories-panel');
    } catch (e) {
      showToast(e.message, 'error');
      if (input) {
        input.checked = !input.checked;
        input.disabled = false;
      }
    }
  }

  async function confirmDeleteCategory(catId, catName, itemCount) {
    if (itemCount > 0) {
      showToast(t('category_has_items').replace('{name}', catName).replace('{count}', itemCount), 'warning');
      return;
    }
    const ok = await confirmDialog({
      title: t('btn_delete_category'),
      message: `${t('confirm_delete_cat_msg')} ${catName}`,
      confirmText: t('delete'),
      tone: 'danger',
    });
    if (!ok) return;
    try {
      await apiRequest(`/api/v1/admin/categories/${catId}/`, { method: 'DELETE' });
      showToast(t('category_deleted_success'), 'success');
      reloadSoon('#categories-panel');
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  // ------------------------------------------------------------------
  // Tables & staff
  // ------------------------------------------------------------------
  async function rotateTableQR(tableId, tableName) {
    const ok = await confirmDialog({
      title: `${t('btn_rotate_qr')} · ${tableName}`,
      message: t('rotate_qr_confirm'),
      confirmText: t('btn_rotate_qr'),
      tone: 'danger',
    });
    if (!ok) return;
    try {
      await apiPost(`/api/v1/admin/tables/${tableId}/rotate-qr/`);
      showToast(t('rotate_qr_success'), 'success');
      reloadSoon('#tables-panel');
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function submitCreateStaffUser() {
    const username = $('new-user-username').value.trim();
    const password = $('new-user-password').value;
    if (!username || !password) {
      showToast(t('validation_user_pass_required'), 'warning');
      return;
    }
    if (password.length < 8) {
      showToast(t('password_hint'), 'warning');
      return;
    }
    const btn = $('btn-create-user');
    setBusy(btn, true);
    try {
      await apiPost('/api/v1/admin/users/create/', {
        username,
        password,
        role: $('new-user-role').value,
        first_name: $('new-user-fullname').value.trim(),
      });
      showToast(t('user_created_success').replace('{name}', username), 'success');
      modal('createUserModal').hide();
      reloadSoon('#users-panel');
    } catch (e) {
      showToast(e.message, 'error');
      setBusy(btn, false);
    }
  }

  // Restore the last category filter
  document.addEventListener('DOMContentLoaded', () => {
    const saved = sessionStorage.getItem('celvass_admin_active_cat');
    if (saved && document.querySelector(`.admin-cat-pill[data-cat="${CSS.escape(saved)}"]`)) {
      filterAdminMenuByCategory(saved);
    }
  });

  Object.assign(window, {
    filterAdminMenuByCategory,
    filterAdminMenuItems,
    goToCategoryInMenuPanel,
    openCreateMenuItemModal,
    openEditMenuItemModal,
    updateMenuImagePreview,
    handleMenuFileSelect,
    submitMenuItemForm,
    confirmDeleteMenuItem,
    toggleMenuItem,
    openCreateCategoryModal,
    openEditCategoryModal,
    autoSlugCategory,
    updateCatIconPreview,
    selectCatIconPreset,
    submitCategoryForm,
    toggleCategoryStatus,
    confirmDeleteCategory,
    rotateTableQR,
    submitCreateStaffUser,
  });
})();
