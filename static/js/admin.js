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

  // Any busy button inside a modal is restored when that modal opens again, so a form can
  // never stay disabled after a previous save.
  document.addEventListener('show.bs.modal', (e) => {
    e.target.querySelectorAll('button[data-label]').forEach((b) => setBusy(b, false));
  });

  function setBusy(btn, busy) {
    if (!btn) return;
    if (busy) {
      btn.dataset.label = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>';
    } else {
      btn.disabled = false;
      if (btn.dataset.label) btn.innerHTML = btn.dataset.label;
      delete btn.dataset.label;
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
    ['menu-item-id', 'menu-form-name', 'menu-form-price', 'menu-form-sku', 'menu-form-desc', 'menu-form-prepnote', 'menu-form-imgurl', 'menu-form-imgfile', 'menu-form-quota'].forEach((id) => {
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
      $('menu-form-quota').value = item.daily_quota || '';
      $('menu-form-featured').checked = Boolean(item.is_featured);
      $('menu-image-preview').src = item.display_image || DEFAULT_IMG;
      $('menu-form-reviews').hidden = true;
      modal('menuItemModal').show();
      if (item.rating_count) {
        loadMenuReviews(item.id, $('menu-form-reviews-list')).then((ok) => {
          if (ok && $('menu-item-id').value === String(item.id)) $('menu-form-reviews').hidden = false;
        });
      }
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  // ------------------------------------------------------------------
  // Customer ratings
  // ------------------------------------------------------------------
  function starsHtml(value) {
    const full = Math.round(Number(value) || 0);
    let out = '';
    for (let i = 1; i <= 5; i += 1) {
      out += `<i class="fa-${i <= full ? 'solid' : 'regular'} fa-star" aria-hidden="true"></i>`;
    }
    return `<span class="review-stars" aria-label="${escapeHtml(String(value))}/5">${out}</span>`;
  }

  function renderReviews(data) {
    const count = Number(data.rating_count) || 0;
    const head = `<div class="review-summary">${starsHtml(data.rating_avg)}<strong class="tabular">${Number(data.rating_avg || 0).toFixed(1)}</strong><span class="text-tertiary tabular">(${count})</span></div>`;
    if (!data.reviews || !data.reviews.length) {
      return `${head}<div class="text-tertiary fs-8">${escapeHtml(t('menu_no_rating'))}</div>`;
    }
    const rows = data.reviews.map((r) => `
      <li class="review-item">
        <div class="d-flex align-items-center justify-content-between gap-2">${starsHtml(r.stars)}<span class="fs-8 text-tertiary tabular">${escapeHtml(r.date)}</span></div>
        ${r.comment ? `<div class="review-comment">${escapeHtml(r.comment)}</div>` : ''}
      </li>`).join('');
    return `${head}<div class="fs-8 text-tertiary mb-1">${escapeHtml(t('menu_reviews_latest'))}</div><ul class="review-list">${rows}</ul>`;
  }

  async function loadMenuReviews(itemId, target) {
    target.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>';
    try {
      const data = await apiGet(`/api/v1/admin/menu-items/${itemId}/reviews/`);
      target.innerHTML = renderReviews(data);
      return true;
    } catch (e) {
      target.innerHTML = '';
      showToast(e.message, 'error');
      return false;
    }
  }

  function openMenuReviews(itemId, itemName) {
    $('menu-reviews-modal-title').textContent = `${t('menu_reviews_title')}: ${itemName || ''}`;
    modal('menuReviewsModal').show();
    loadMenuReviews(itemId, $('menu-reviews-modal-list'));
  }

  // ------------------------------------------------------------------
  // Inline daily quota
  // ------------------------------------------------------------------
  function markQuotaDirty(input) {
    const form = input.closest('form');
    const save = form && form.querySelector('.quota-inline-save');
    if (save) save.hidden = input.value.trim() === (input.dataset.initial || '');
    input.classList.remove('is-invalid');
  }

  async function saveInlineQuota(itemId, form) {
    const input = form.querySelector('input[name="daily_quota"]');
    const btn = form.querySelector('.quota-inline-save');
    const raw = input.value.trim();
    if (raw !== '' && (!/^\d+$/.test(raw) || Number(raw) < 1)) {
      input.classList.add('is-invalid');
      input.focus();
      showToast(t('stock_quota_invalid'), 'warning');
      return;
    }
    setBusy(btn, true);
    try {
      const res = await apiRequest(`/api/v1/admin/menu-items/${itemId}/quota/`, {
        method: 'PATCH',
        body: JSON.stringify({ daily_quota: raw === '' ? null : Number(raw) }),
      });
      const av = (res && res.availability) || {};
      input.dataset.initial = raw;
      const row = form.closest('tr');
      if (row) {
        const left = row.querySelector('.js-quota-left');
        if (left) left.textContent = av.remaining === null || av.remaining === undefined ? '-' : av.remaining;
        const sold = row.querySelector('.js-quota-sold');
        if (sold && av.sold !== undefined) sold.textContent = av.sold;
      }
      setBusy(btn, false);
      btn.hidden = true;
      showToast(t('stock_quota_saved'), 'success');
      // The stock badge and close/reopen button depend on the new quota.
      if (av.state && row && row.dataset.state !== av.state) reloadSoon('#menu-panel');
    } catch (e) {
      setBusy(btn, false);
      input.classList.add('is-invalid');
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
      fd.append('daily_quota', $('menu-form-quota').value.trim());
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

  async function closeMenuItemToday(itemId, btn) {
    setBusy(btn, true);
    try {
      const res = await apiPost(`/api/v1/admin/menu-items/${itemId}/close-today/`, {});
      const cancelled = res && res.cancelled_items ? ` (${res.cancelled_items} ${t('stock_cancelled_portions')})` : '';
      showToast(t('stock_closed_toast') + cancelled, 'success');
      reloadSoon('#menu-panel');
    } catch (e) {
      showToast(e.message, 'error');
      setBusy(btn, false);
    }
  }

  async function reopenMenuItem(itemId, btn) {
    setBusy(btn, true);
    try {
      await apiPost(`/api/v1/admin/menu-items/${itemId}/reopen/`, {});
      showToast(t('stock_reopened_toast'), 'success');
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
      await refreshTablesPanel();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  const TABLE_STATUS_KEYS = {
    AVAILABLE: 'table_status_available',
    OCCUPIED: 'table_status_occupied',
    CLEANING: 'table_status_unavailable',
    MAINTENANCE: 'table_status_unavailable',
  };

  function renderTableRow(tbl) {
    const id = escapeHtml(tbl.id);
    const name = escapeHtml(tbl.display_name);
    const L = (k) => escapeHtml(t(k));
    const status = tbl.status === 'AVAILABLE' ? 'available' : tbl.status === 'OCCUPIED' ? 'occupied' : 'maintenance';
    const openLinks = tbl.is_active
      ? `<a class="btn btn-sm btn-ghost btn-icon" href="/t/${encodeURIComponent(tbl.qr_token)}/" target="_blank" rel="noopener" aria-label="${L('open_table_menu')}" title="${L('open_table_menu')}"><i class="fa-solid fa-arrow-up-right-from-square"></i></a>
         <button type="button" class="btn btn-sm btn-ghost btn-icon" onclick="rotateTableQR('${id}', this.dataset.name)" data-name="${name}" aria-label="${L('btn_rotate_qr')}" title="${L('btn_rotate_qr')}"><i class="fa-solid fa-arrows-rotate"></i></button>`
      : '';
    return `
      <tr${tbl.is_active ? '' : ' class="opacity-75"'}>
        <td class="cell-primary">
          <div class="d-flex justify-content-between align-items-start gap-2">
            <div class="min-w-0">
              <div class="fw-bold">${name}</div>
              <div class="fs-8 text-tertiary">${escapeHtml(tbl.table_code)}${tbl.sort_order ? ` &middot; ${L('category_sort')} ${escapeHtml(tbl.sort_order)}` : ''}</div>
            </div>
            ${tbl.is_active ? '' : `<span class="badge-soft">${L('category_inactive')}</span>`}
          </div>
        </td>
        <td data-label="${L('th_capacity')}" data-i18n-label="th_capacity" class="tabular">${tbl.capacity ? escapeHtml(tbl.capacity) : '-'}</td>
        <td data-label="${L('th_current_status')}" data-i18n-label="th_current_status">
          <span class="status status-${status}">${L(TABLE_STATUS_KEYS[tbl.status] || 'status')}</span>
          ${tbl.has_active_session ? `<span class="badge-soft tone-info ms-1">${L('active_session')}</span>` : ''}
        </td>
        <td data-label="QR" data-i18n-label="th_qr_token"><span class="code-text">${escapeHtml(String(tbl.qr_token).slice(0, 12))}…</span></td>
        <td class="cell-actions text-end">
          <div class="d-inline-flex gap-1 flex-wrap justify-content-end">
            ${openLinks}
            <button type="button" class="btn btn-sm btn-ghost btn-icon" onclick="openEditTableModal('${id}')" aria-label="${L('btn_edit_table')}" title="${L('btn_edit_table')}"><i class="fa-solid fa-pen"></i></button>
            <button type="button" class="btn btn-sm btn-ghost btn-icon text-danger" onclick="confirmDeleteTable('${id}', this.dataset.name, ${tbl.has_active_session ? 'true' : 'false'}, ${Number(tbl.sessions_count) || 0})" data-name="${name}" aria-label="${L('btn_delete_table')}" title="${L('btn_delete_table')}"${tbl.has_active_session ? ' disabled' : ''}><i class="fa-solid fa-trash-can"></i></button>
          </div>
        </td>
      </tr>`;
  }

  async function refreshTablesPanel() {
    const tbody = $('admin-tables-tbody');
    if (!tbody) return;
    try {
      const tables = (await apiGet('/api/v1/admin/tables/')) || [];
      tbody.innerHTML = tables.length
        ? tables.map(renderTableRow).join('')
        : `<tr><td colspan="5" class="cell-empty"><div class="empty-state"><i class="fa-solid fa-chair" aria-hidden="true"></i><div>${escapeHtml(t('no_tables_found'))}</div></div></td></tr>`;
    } catch (e) {
      // Fall back to a full reload so the list is never left stale.
      reloadSoon('#tables-panel');
    }
  }

  function fillTableForm(tbl) {
    $('table-id').value = tbl?.id || '';
    $('table-form-code').value = tbl?.table_code || '';
    $('table-form-name').value = tbl?.display_name || '';
    $('table-form-capacity').value = tbl?.capacity ?? '';
    $('table-form-sort').value = tbl?.sort_order ?? 0;
    $('table-form-active').checked = tbl ? Boolean(tbl.is_active) : true;

    const status = $('table-form-status');
    const busy = Boolean(tbl?.has_active_session);
    status.value = tbl && (tbl.status === 'MAINTENANCE' || tbl.status === 'CLEANING') ? 'MAINTENANCE' : 'AVAILABLE';
    status.disabled = busy;
    $('table-form-active').disabled = busy;
    $('table-form-status-hint').textContent = busy ? t('table_busy_hint') : t('table_status_hint');
    document.querySelectorAll('#table-form .is-invalid').forEach((el) => el.classList.remove('is-invalid'));
  }

  function openCreateTableModal() {
    $('table-modal-title').textContent = t('btn_add_table');
    fillTableForm(null);
    modal('tableModal').show();
  }

  async function openEditTableModal(tableId) {
    try {
      const tbl = await apiGet(`/api/v1/admin/tables/${tableId}/`);
      $('table-modal-title').textContent = `${t('btn_edit_table')}: ${tbl.display_name}`;
      fillTableForm(tbl);
      modal('tableModal').show();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async function submitTableForm() {
    const tableId = $('table-id').value;
    const code = $('table-form-code').value.trim().toUpperCase();
    const name = $('table-form-name').value.trim();
    const capRaw = $('table-form-capacity').value.trim();
    const cap = capRaw === '' ? null : parseInt(capRaw, 10);

    const invalid = [];
    if (!code) invalid.push('table-form-code');
    if (!name) invalid.push('table-form-name');
    if (cap !== null && (!Number.isInteger(cap) || cap < 1 || cap > 50)) invalid.push('table-form-capacity');
    document.querySelectorAll('#table-form .is-invalid').forEach((el) => el.classList.remove('is-invalid'));
    if (invalid.length) {
      invalid.forEach((id) => $(id).classList.add('is-invalid'));
      $(invalid[0]).focus();
      showToast(invalid.includes('table-form-capacity') && invalid.length === 1 ? t('table_capacity_hint') : t('validation_required'), 'warning');
      return;
    }

    const payload = {
      table_code: code,
      display_name: name,
      capacity: cap,
      sort_order: parseInt($('table-form-sort').value || '0', 10),
    };
    if (!$('table-form-active').disabled) payload.is_active = $('table-form-active').checked;
    if (!$('table-form-status').disabled) payload.status = $('table-form-status').value;

    const btn = $('btn-save-table');
    setBusy(btn, true);
    try {
      await apiRequest(tableId ? `/api/v1/admin/tables/${tableId}/` : '/api/v1/admin/tables/', {
        method: tableId ? 'PATCH' : 'POST',
        body: JSON.stringify(payload),
      });
      showToast(tableId ? t('table_updated_success') : t('table_created_success'), 'success');
      modal('tableModal').hide();
      await refreshTablesPanel();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  async function confirmDeleteTable(tableId, tableName, hasActiveSession, sessionsCount) {
    if (hasActiveSession) {
      showToast(t('table_busy_hint'), 'warning');
      return;
    }
    const ok = await confirmDialog({
      title: `${t('btn_delete_table')} · ${tableName}`,
      message: sessionsCount > 0 ? t('table_delete_history_msg') : t('table_delete_msg'),
      confirmText: sessionsCount > 0 ? t('table_deactivate') : t('delete'),
      tone: 'danger',
    });
    if (!ok) return;
    try {
      const res = await apiRequest(`/api/v1/admin/tables/${tableId}/`, { method: 'DELETE' });
      showToast(res.deleted ? t('table_deleted_success') : t('table_deactivated_success'), 'success');
      await refreshTablesPanel();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  // ------------------------------------------------------------------
  // Staff
  // ------------------------------------------------------------------
  const ROLE_TONE = { ADMIN: 'tone-accent', CASHIER: 'tone-success', KITCHEN: 'tone-info' };
  const ROLE_KEY = { ADMIN: 'role_admin', CASHIER: 'role_cashier', KITCHEN: 'role_kitchen' };

  function fmtDate(value, withTime) {
    if (!value) return '-';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return '-';
    const p = (n) => String(n).padStart(2, '0');
    const day = p(d.getDate()) + '/' + p(d.getMonth() + 1) + '/' + d.getFullYear();
    return withTime ? day + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) : day;
  }

  function renderStaffRow(u) {
    const L = (k) => escapeHtml(t(k));
    const id = escapeHtml(u.id);
    const name = escapeHtml(u.display_name || u.username);
    const role = u.is_superuser ? 'ADMIN' : u.role;
    const locked = u.is_self || u.is_superuser;
    const you = u.is_self ? ' <span class="badge-soft tone-accent">' + L('you_badge') + '</span>' : '';
    const status = u.is_active
      ? '<span class="badge-soft tone-success">' + L('category_active') + '</span>'
      : '<span class="badge-soft">' + L('category_inactive') + '</span>';
    return `
      <tr class="${u.is_active ? '' : 'opacity-75'}">
        <td class="cell-primary">
          <div class="fw-bold">${name}${you}</div>
          <div class="fs-8 text-tertiary">@${escapeHtml(u.username)}</div>
        </td>
        <td data-label="${L('label_role')}" data-i18n-label="label_role"><span class="badge-soft ${ROLE_TONE[role] || ''}">${L(ROLE_KEY[role] || 'label_role')}</span></td>
        <td data-label="${L('status')}" data-i18n-label="status">${status}</td>
        <td data-label="${L('last_login')}" data-i18n-label="last_login" class="tabular fs-7">${fmtDate(u.last_login, true)}</td>
        <td data-label="${L('joined')}" data-i18n-label="joined" class="tabular fs-7">${fmtDate(u.created_at)}</td>
        <td class="cell-actions text-end">
          <div class="d-inline-flex gap-1">
            <button type="button" class="btn btn-sm btn-ghost btn-icon" onclick="openEditStaffModal('${id}')" aria-label="${L('btn_edit_staff')}" title="${L('btn_edit_staff')}"><i class="fa-solid fa-pen"></i></button>
            <button type="button" class="btn btn-sm btn-ghost btn-icon text-danger" onclick="confirmDeleteStaff('${id}', this.dataset.name, this.dataset.history === 'true')" data-name="${name}" data-history="${u.has_history ? 'true' : 'false'}" aria-label="${L('btn_delete_staff')}" title="${L('btn_delete_staff')}"${locked ? ' disabled' : ''}><i class="fa-solid fa-trash-can"></i></button>
          </div>
        </td>
      </tr>`;
  }

  async function refreshStaffPanel() {
    const tbody = $('admin-staff-tbody');
    if (!tbody) return;
    try {
      const users = (await apiGet('/api/v1/admin/users/')) || [];
      tbody.innerHTML = users.map(renderStaffRow).join('');
    } catch (e) {
      reloadSoon('#users-panel');
    }
  }

  function fillStaffForm(u) {
    const editing = Boolean(u);
    const locked = editing && (u.is_self || u.is_superuser);
    $('staff-id').value = editing ? u.id : '';
    $('staff-form-fullname').value = editing ? (u.full_name || '') : '';
    $('staff-form-username').value = editing ? u.username : '';
    $('staff-form-password').value = '';
    $('staff-form-password').type = 'password';
    $('staff-form-password').required = !editing;
    $('staff-password-required').hidden = editing;
    $('staff-form-password-hint').textContent = editing ? t('password_keep_hint') : t('password_hint');
    $('staff-form-role').value = editing ? (u.is_superuser ? 'ADMIN' : u.role) : 'CASHIER';
    $('staff-form-role').disabled = locked;
    $('staff-active-wrap').hidden = !editing;
    $('staff-form-active').checked = editing ? Boolean(u.is_active) : true;
    $('staff-form-active').disabled = locked;
    $('staff-self-hint').hidden = !(editing && u.is_self);
    document.querySelectorAll('#staff-form .is-invalid').forEach((el) => el.classList.remove('is-invalid'));
  }

  function openCreateStaffModal() {
    $('staff-modal-title').textContent = t('modal_create_staff_title');
    fillStaffForm(null);
    modal('staffModal').show();
  }

  async function openEditStaffModal(userId) {
    try {
      const u = await apiGet('/api/v1/admin/users/' + encodeURIComponent(userId) + '/');
      $('staff-modal-title').textContent = t('btn_edit_staff') + ': ' + (u.display_name || u.username);
      fillStaffForm(u);
      modal('staffModal').show();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  function toggleStaffPassword(btn) {
    const input = $('staff-form-password');
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    const icon = btn.querySelector('i');
    icon.classList.toggle('fa-eye', !show);
    icon.classList.toggle('fa-eye-slash', show);
  }

  async function submitStaffForm() {
    const userId = $('staff-id').value;
    const username = $('staff-form-username').value.trim();
    const password = $('staff-form-password').value;

    const invalid = [];
    if (!username) invalid.push('staff-form-username');
    if ((!userId && !password) || (password && password.length < 8)) invalid.push('staff-form-password');
    document.querySelectorAll('#staff-form .is-invalid').forEach((el) => el.classList.remove('is-invalid'));
    if (invalid.length) {
      invalid.forEach((id) => $(id).classList.add('is-invalid'));
      $(invalid[0]).focus();
      showToast(invalid[0] === 'staff-form-password' ? t('password_hint') : t('validation_required'), 'warning');
      return;
    }

    const payload = { username, full_name: $('staff-form-fullname').value.trim() };
    if (password) payload.password = password;
    if (!$('staff-form-role').disabled) payload.role = $('staff-form-role').value;
    if (userId && !$('staff-form-active').disabled) payload.is_active = $('staff-form-active').checked;

    const btn = $('btn-save-staff');
    setBusy(btn, true);
    try {
      await apiRequest(userId ? '/api/v1/admin/users/' + encodeURIComponent(userId) + '/' : '/api/v1/admin/users/', {
        method: userId ? 'PATCH' : 'POST',
        body: JSON.stringify(payload),
      });
      showToast(userId ? t('staff_updated_success') : t('user_created_success').replace('{name}', username), 'success');
      modal('staffModal').hide();
      await refreshStaffPanel();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  async function confirmDeleteStaff(userId, name, hasHistory) {
    const ok = await confirmDialog({
      title: t('btn_delete_staff') + ' · ' + name,
      message: hasHistory ? t('staff_delete_history_msg') : t('staff_delete_msg'),
      confirmText: hasHistory ? t('table_deactivate') : t('delete'),
      tone: 'danger',
    });
    if (!ok) return;
    try {
      const res = await apiRequest('/api/v1/admin/users/' + encodeURIComponent(userId) + '/', { method: 'DELETE' });
      showToast(res.deleted ? t('staff_deleted_success') : t('staff_deactivated_success'), 'success');
      await refreshStaffPanel();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  // Restore the last category filter and lock delete on tables that are in use
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('button[data-busy="true"]').forEach((b) => {
      b.disabled = true;
    });
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
    openMenuReviews,
    markQuotaDirty,
    saveInlineQuota,
    updateMenuImagePreview,
    handleMenuFileSelect,
    submitMenuItemForm,
    confirmDeleteMenuItem,
    toggleMenuItem,
    closeMenuItemToday,
    reopenMenuItem,
    openCreateCategoryModal,
    openEditCategoryModal,
    autoSlugCategory,
    updateCatIconPreview,
    selectCatIconPreset,
    submitCategoryForm,
    toggleCategoryStatus,
    confirmDeleteCategory,
    rotateTableQR,
    openCreateTableModal,
    openEditTableModal,
    submitTableForm,
    confirmDeleteTable,
    openCreateStaffModal,
    openEditStaffModal,
    toggleStaffPassword,
    submitStaffForm,
    confirmDeleteStaff,
  });
})();
