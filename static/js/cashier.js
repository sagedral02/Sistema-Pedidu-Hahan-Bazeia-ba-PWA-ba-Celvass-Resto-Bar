/**
 * Cashier portal controller.
 *   POS: verification queue, tables (request bill, pay) and take-away orders (hand over, pay on pick-up).
 *   Service desk: requested bills and kitchen notices ("out of stock" -> mark sold out).
 *   Menu & Stock: close a dish for today or reopen it (nothing else).
 *   Shift: opening cash, live expected cash, closing count and Z-report.
 * The order screen lives in pos-order.js. History and reports live in /reports/.
 * All server values are escaped before they are inserted into the DOM.
 */

function setCount(name, value) {
  document.querySelectorAll(`[data-count-for="${name}"]`).forEach((el) => {
    el.textContent = value > 0 ? value : '';
    el.dataset.count = value;
  });
}

function emptyState(icon, title, desc = '') {
  return `
    <div class="empty-state">
      <i class="fa-solid ${icon}" aria-hidden="true"></i>
      <div class="empty-state-title">${escapeHtml(title)}</div>
      ${desc ? `<div class="fs-7">${escapeHtml(desc)}</div>` : ''}
    </div>`;
}

const ITEM_TONE = { QUEUED: 'tone-warning', COOKING: 'tone-accent', READY: 'tone-success', SERVED: '', UNAVAILABLE: 'tone-danger' };

function newKey(prefix) {
  return crypto?.randomUUID ? crypto.randomUUID() : `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

class CashierApp {
  constructor() {
    this.pendingOrders = [];
    this.tables = [];
    this.takeaways = [];
    this.notices = [];
    this.stock = [];
    this.stockCategory = 'all';
    this.activationRequests = [];
    this.tableFilter = 'ALL';
    this.expandedTables = new Set();
    this.idleAlerted = new Set();
    this.selectedTableId = null;
    this.currentPaymentSessionId = null;
    this.currentBillAmount = 0;
    this.paymentIdempotencyKey = null;
    this.shift = null;
    this.lastShift = null;

    this.knownActivationIds = null;
    this.knownOrderIds = null;
    this.knownBillSessionIds = null;
    this.knownNoticeIds = null;

    this.init();
  }

  init() {
    this.refreshAll();
    this.loadShift();
    this.initWebSocket();

    window.addEventListener('languageChanged', () => {
      this.renderPendingOrdersUI();
      this.renderActivationRequestsUI();
      this.renderTablesUI();
      this.updateBillAlertsUI();
      this.renderTakeaways();
      this.renderNotices();
      this.renderStock();
      this.renderShift();
    });

    window.addEventListener('app:panel', (e) => {
      const id = e.detail.id;
      if (id === 'shift-panel') this.loadShift();
      if (id === 'service-panel') {
        this.loadTables();
        this.loadNotices();
      }
      if (id === 'stock-panel') this.loadStock();
      if (id === 'pos-panel' && e.detail.view === 'takeaway') this.loadTakeaways();
    });

    setInterval(() => {
      if (document.hidden) return;
      this.loadPendingOrders();
      this.loadTables();
      this.loadActivationRequests();
      this.loadTakeaways();
      this.loadNotices();
    }, 8000);
  }

  refreshAll() {
    this.loadPendingOrders();
    this.loadTables();
    this.loadActivationRequests();
    this.loadTakeaways();
    this.loadNotices();
  }

  updateServiceCount() {
    const bills = this.tables.filter((tb) => tb.active_session?.status === 'BILL_REQUESTED').length
      + this.takeaways.filter((o) => o.session_status === 'BILL_REQUESTED').length;
    setCount('bills', bills);
    setCount('notices', this.notices.length);
    const idle = this.idleTables().length;
    setCount('idle', idle);
    setCount('service', bills + this.notices.length + idle);
  }

  idleTables() {
    return (this.tables || []).filter((tb) => tb.active_session && tb.active_session.is_idle);
  }

  /** Atendimentu > Meja tidak aktif: unpaid tables without activity past the admin's limit. */
  renderIdleTables() {
    const el = document.getElementById('idle-tables-container');
    const idle = this.idleTables();
    const fresh = idle.filter((tb) => !this.idleAlerted.has(String(tb.active_session.id)));
    if (this.idleAlertsPrimed && fresh.length) {
      SoundEffects.playBell();
      showToast(`${t('csh_idle_toast')} ${fresh.map((tb) => tb.display_name).join(', ')}`, 'warning');
    }
    this.idleAlerted = new Set(idle.map((tb) => String(tb.active_session.id)));
    this.idleAlertsPrimed = true;
    if (!el) return;
    if (!idle.length) {
      el.innerHTML = `<div style="grid-column:1/-1">${emptyState('fa-mug-hot', t('csh_no_idle'))}</div>`;
      return;
    }
    el.innerHTML = idle.map((tb) => {
      const s = tb.active_session;
      const sid = escapeHtml(JSON.stringify(String(s.id)));
      const nameJs = escapeHtml(JSON.stringify(String(tb.display_name)));
      const idJs = escapeHtml(JSON.stringify(String(tb.id)));
      const remaining = parseFloat(s.remaining_balance ?? s.bill_total) || 0;
      const billBtn = s.status === 'OPEN'
        ? `<button type="button" class="btn btn-sm btn-primary" onclick='cashierApp.requestBill(${sid}, ${nameJs})' ${remaining <= 0 ? 'disabled' : ''}><i class="fa-solid fa-file-invoice-dollar" aria-hidden="true"></i>${escapeHtml(t('csh_btn_request_bill'))}</button>`
        : `<button type="button" class="btn btn-sm btn-primary" onclick='cashierApp.openPaymentModal(${sid}, ${nameJs})'><i class="fa-solid fa-cash-register" aria-hidden="true"></i>${escapeHtml(t('csh_btn_pay'))}</button>`;
      return `
        <article class="table-tile is-idle">
          <div class="table-tile-head">
            <div class="min-w-0">
              <div class="table-tile-name truncate">${escapeHtml(tb.display_name)}</div>
              <div class="table-tile-meta">${escapeHtml(tb.table_code)} &middot; ${escapeHtml(s.guest_count)} ${escapeHtml(t('people'))}</div>
            </div>
            <span class="badge-soft tone-warning">${escapeHtml(s.idle_minutes)} ${escapeHtml(t('csh_minutes'))}</span>
          </div>
          <div class="table-idle-flag"><i class="fa-solid fa-hourglass-half" aria-hidden="true"></i>${escapeHtml(t('csh_idle_flag'))}</div>
          <div class="table-tile-stats"><span>${escapeHtml(t('remaining_bill'))}</span><strong class="tabular">${formatMoney(remaining)}</strong></div>
          <div class="table-tile-actions">
            ${billBtn}
            <button type="button" class="btn btn-sm btn-secondary-app" onclick='cashierApp.showTableInPos(${idJs})'><i class="fa-solid fa-list" aria-hidden="true"></i>${escapeHtml(t('csh_show_detail'))}</button>
            <button type="button" class="btn btn-sm btn-ghost btn-icon" onclick='cashierApp.handleCloseSession(${sid}, ${nameJs}, ${remaining <= 0 ? 'true' : 'false'}, ${escapeHtml(JSON.stringify(remaining))})' title="${escapeHtml(t('close_session'))}" aria-label="${escapeHtml(t('close_session'))}"><i class="fa-solid fa-lock"></i></button>
          </div>
        </article>`;
    }).join('');
  }

  /** Open the POS tables view with this table's detail expanded. */
  showTableInPos(tableId) {
    this.expandedTables.add(String(tableId));
    this.tableFilter = 'ALL';
    location.hash = '#pos-panel/tables';
    this.renderTablesUI();
  }

  // ------------------------------------------------------------------
  // Queue: activation requests + pending orders
  // ------------------------------------------------------------------
  async loadPendingOrders() {
    try {
      this.pendingOrders = (await apiGet('/api/v1/cashier/orders/')) || [];
      const ids = new Set(this.pendingOrders.map((o) => String(o.id)));
      if (this.knownOrderIds && [...ids].some((id) => !this.knownOrderIds.has(id))) SoundEffects.playBell();
      this.knownOrderIds = ids;
      this.renderPendingOrdersUI();
    } catch (e) {
      console.error('pending orders', e);
    }
  }

  async loadActivationRequests() {
    try {
      this.activationRequests = (await apiGet('/api/v1/cashier/activation-requests/')) || [];
      const ids = new Set(this.activationRequests.map((r) => String(r.id)));
      if (this.knownActivationIds && [...ids].some((id) => !this.knownActivationIds.has(id))) SoundEffects.playBell();
      this.knownActivationIds = ids;
      this.renderActivationRequestsUI();
    } catch (e) {
      console.error('activation requests', e);
    }
  }

  updatePendingCount() {
    const total = this.pendingOrders.length + this.activationRequests.length;
    setCount('pending', total);
    const label = document.getElementById('pending-total-label');
    if (label) label.textContent = total;
  }

  renderActivationRequestsUI() {
    this.updatePendingCount();
    const el = document.getElementById('activation-requests-container');
    if (!el) return;
    el.innerHTML = this.activationRequests
      .map(
        (req) => `
        <article class="queue-card is-activation mb-2">
          <div class="d-flex justify-content-between align-items-start gap-2">
            <div class="min-w-0">
              <div class="fw-bold">${escapeHtml(req.table_name || req.table_code)}</div>
              <div class="fs-7 text-tertiary">${escapeHtml(t('table_activation_requested_title'))}</div>
              <div class="fs-7 mt-1"><i class="fa-solid fa-user-group text-tertiary" aria-hidden="true"></i> <strong class="tabular">${escapeHtml(req.guest_count)}</strong> ${escapeHtml(t('people'))}${req.table_capacity ? ` <span class="text-tertiary">&middot; ${escapeHtml(t('csh_capacity'))} ${escapeHtml(req.table_capacity)}</span>` : ''}</div>
            </div>
            <span class="badge-soft tone-info">${escapeHtml(t('badge_open_request'))}</span>
          </div>
          <div class="d-flex gap-2 mt-2">
            <button type="button" class="btn btn-sm btn-primary flex-grow-1" onclick="cashierApp.approveActivation('${escapeHtml(req.id)}')"><i class="fa-solid fa-check" aria-hidden="true"></i>${escapeHtml(t('btn_approve_activation'))}</button>
            <button type="button" class="btn btn-sm btn-secondary-app btn-icon" onclick="cashierApp.approveActivation('${escapeHtml(req.id)}', true)" title="${escapeHtml(t('csh_fix_guests'))}" aria-label="${escapeHtml(t('csh_fix_guests'))}"><i class="fa-solid fa-user-pen" aria-hidden="true"></i></button>
            <button type="button" class="btn btn-sm btn-outline-danger" onclick="cashierApp.rejectActivation('${escapeHtml(req.id)}')">${escapeHtml(t('btn_reject'))}</button>
          </div>
        </article>`
      )
      .join('');
    if (!this.pendingOrders.length) this.renderPendingOrdersUI();
  }

  renderPendingOrdersUI() {
    this.updatePendingCount();
    const el = document.getElementById('pending-orders-container');
    if (!el) return;

    if (!this.pendingOrders.length) {
      el.innerHTML = this.activationRequests.length ? '' : emptyState('fa-circle-check', t('all_verified'), t('new_orders_appear_here'));
      return;
    }

    el.innerHTML = this.pendingOrders
      .map((order) => {
        const items = (order.items || [])
          .map(
            (i) => `
            <li>
              <span class="min-w-0"><strong class="tabular">${escapeHtml(i.quantity)}×</strong> ${escapeHtml(i.menu_name_snapshot)}
                ${i.note ? `<span class="item-note">${escapeHtml(i.note)}</span>` : ''}
              </span>
              <span class="tabular text-secondary-app">${formatMoney(i.subtotal)}</span>
            </li>`
          )
          .join('');
        return `
          <article class="queue-card">
            <div class="d-flex justify-content-between align-items-start gap-2">
              <div class="min-w-0">
                <div class="fw-bold">${escapeHtml(order.table_name || order.table_code)}</div>
                <div class="fs-7 text-tertiary">${escapeHtml(order.order_code)} &middot; ${escapeHtml(formatTime(order.submitted_at || order.created_at))}</div>
              </div>
              <span class="price">${formatMoney(order.grand_total)}</span>
            </div>
            <ul class="queue-items">${items}</ul>
            ${order.customer_note ? `<div class="callout tone-warning py-2 mb-2 fs-7"><i class="fa-solid fa-note-sticky" aria-hidden="true"></i><div>${escapeHtml(order.customer_note)}</div></div>` : ''}
            <div class="d-flex gap-2">
              <button type="button" class="btn btn-sm btn-outline-danger" onclick="cashierApp.promptReject('${escapeHtml(order.id)}', '${escapeHtml(order.order_code)}')">${escapeHtml(t('btn_reject'))}</button>
              <button type="button" class="btn btn-sm btn-primary flex-grow-1" onclick="cashierApp.confirmOrder('${escapeHtml(order.id)}', this)"><i class="fa-solid fa-check" aria-hidden="true"></i>${escapeHtml(t('btn_confirm_send'))}</button>
            </div>
          </article>`;
      })
      .join('');
  }

  findActivationRequest(requestId) {
    const fromQueue = (this.activationRequests || []).find((r) => String(r.id) === String(requestId));
    if (fromQueue) return fromQueue;
    const table = (this.tables || []).find((tb) => tb.pending_activation && String(tb.pending_activation.id) === String(requestId));
    return table ? { ...table.pending_activation, guest_limit: table.guest_limit, table_capacity: table.capacity, table_name: table.display_name } : null;
  }

  async approveActivation(requestId, correctGuests) {
    const req = this.findActivationRequest(requestId) || {};
    const body = {};
    if (correctGuests) {
      const limit = Number(req.guest_limit) || 25;
      const value = await confirmDialog({
        title: `${t('csh_fix_guests')} · ${req.table_name || ''}`,
        message: t('csh_fix_guests_msg'),
        confirmText: t('btn_approve_activation'),
        input: { type: 'number', min: 1, max: limit, value: Math.min(Number(req.guest_count) || 1, limit),
                 label: t('guest_count_label'), hint: `${t('csh_guest_range')} 1–${limit}` },
      });
      if (value === false) return;
      body.guest_count = value;
    }
    try {
      await apiPost(`/api/v1/cashier/activation-requests/${requestId}/approve/`, body);
      SoundEffects.playSuccess();
      showToast(t('session_opened_success'), 'success');
      this.loadActivationRequests();
      this.loadTables();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async rejectActivation(requestId) {
    const ok = await confirmDialog({
      title: t('reject_activation_title'),
      message: t('reject_activation_msg'),
      confirmText: t('btn_reject'),
      tone: 'danger',
    });
    if (!ok) return;
    try {
      await apiPost(`/api/v1/cashier/activation-requests/${requestId}/reject/`, { reason: 'Rekuza husi kaixa' });
      showToast(t('activation_rejected_toast'), 'info');
      this.loadActivationRequests();
      this.loadTables();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async confirmOrder(orderId, btn) {
    if (btn) btn.disabled = true;
    try {
      await apiPost(`/api/v1/cashier/orders/${orderId}/confirm/`);
      showToast(t('order_confirmed_success'), 'success');
      SoundEffects.playSuccess();
      this.loadPendingOrders();
      this.loadTables();
    } catch (e) {
      showToast(e.message, 'error');
      if (btn) btn.disabled = false;
    }
  }

  async promptReject(orderId, orderCode) {
    const reason = await confirmDialog({
      title: `${t('reject_modal_title')} ${orderCode}`,
      message: t('reject_modal_desc'),
      confirmText: t('btn_confirm_reject'),
      tone: 'danger',
      input: { label: t('reject_reason_label'), required: true },
    });
    if (!reason) return;
    try {
      await apiPost(`/api/v1/cashier/orders/${orderId}/reject/`, { reason });
      showToast(t('order_rejected_success'), 'info');
      SoundEffects.playError();
      this.loadPendingOrders();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  // ------------------------------------------------------------------
  // Tables
  // ------------------------------------------------------------------
  setTableFilter(filter) {
    this.tableFilter = filter;
    const map = { ALL: 'filter-table-all', OPEN: 'filter-table-open', AVAILABLE: 'filter-table-avail' };
    Object.entries(map).forEach(([key, id]) => document.getElementById(id)?.classList.toggle('is-active', key === filter));
    this.renderTablesUI();
  }

  async loadTables() {
    try {
      this.tables = (await apiGet('/api/v1/cashier/tables/')) || [];
      const bills = new Set(
        this.tables.filter((tb) => tb.active_session?.status === 'BILL_REQUESTED').map((tb) => String(tb.active_session.id))
      );
      if (this.knownBillSessionIds && [...bills].some((id) => !this.knownBillSessionIds.has(id))) SoundEffects.playBell();
      this.knownBillSessionIds = bills;
      this.renderTablesUI();
      this.renderIdleTables();
      this.updateBillAlertsUI();
    } catch (e) {
      console.error('tables', e);
    }
  }

  /** free -> pending (QR request) -> open (ordering / kitchen busy) -> ready_bill (all served) -> bill -> paid */
  sessionState(table) {
    const s = table.active_session;
    if (!s) return table.pending_activation ? 'pending' : 'free';
    if (s.status === 'PAID') return 'paid';
    if (s.status === 'BILL_REQUESTED' || s.status === 'PAYMENT_PENDING') return 'bill';
    return s.all_delivered ? 'ready_bill' : 'open';
  }

  toggleTableDetail(tableId) {
    const key = String(tableId);
    if (this.expandedTables.has(key)) this.expandedTables.delete(key);
    else this.expandedTables.add(key);
    this.renderTablesUI();
  }

  renderTableProgress(s) {
    const orders = s.progress || [];
    if (!orders.length) return `<div class="fs-7 text-tertiary">${escapeHtml(t('csh_no_orders_yet'))}</div>`;
    return orders.map((o) => {
      const items = (o.items || []).map((i) => {
        const label = i.status === 'WAITING' ? t('csh_item_waiting') : t(`kds_item_${i.status}`);
        const tone = i.status === 'WAITING' ? 'tone-info' : (ITEM_TONE[i.status] || '');
        const name = i.status === 'UNAVAILABLE' ? `<s>${escapeHtml(i.name)}</s>` : escapeHtml(i.name);
        return `
          <li>
            <span class="min-w-0"><strong class="tabular">${escapeHtml(i.quantity)}×</strong> ${name}${i.note ? `<span class="item-note">${escapeHtml(i.note)}</span>` : ''}</span>
            <span class="badge-soft ${tone}">${escapeHtml(label)}</span>
          </li>`;
      }).join('');
      return `
        <div class="table-order">
          <div class="table-order-head"><span class="code-text">${escapeHtml(o.code)}</span><span class="fs-8 text-tertiary">${escapeHtml(formatTime(o.created_at))}</span></div>
          <ul class="queue-items">${items}</ul>
        </div>`;
    }).join('');
  }

  renderTablesUI() {
    const el = document.getElementById('tables-grid-container');
    if (!el) return;

    let list = this.tables;
    if (this.tableFilter === 'OPEN') list = list.filter((tb) => tb.active_session);
    if (this.tableFilter === 'AVAILABLE') list = list.filter((tb) => !tb.active_session);

    if (!list.length) {
      el.innerHTML = `<div style="grid-column:1/-1">${emptyState('fa-chair', t('no_tables_found'))}</div>`;
      return;
    }

    el.innerHTML = list.map((table) => this.renderTableTile(table)).join('');
  }

  renderTableTile(table) {
    const s = table.active_session;
    const state = this.sessionState(table);
    const name = escapeHtml(table.display_name);
    const nameJs = escapeHtml(JSON.stringify(String(table.display_name)));
    const idJs = escapeHtml(JSON.stringify(String(table.id)));
    const qrLink = `<a class="btn btn-sm btn-ghost btn-icon" href="/t/${encodeURIComponent(table.qr_token)}/" target="_blank" rel="noopener" title="${escapeHtml(t('open_table_menu'))}" aria-label="${escapeHtml(t('open_table_menu'))}"><i class="fa-solid fa-qrcode"></i></a>`;
    const L = (key) => escapeHtml(t(key));
    // Ordering for a table always goes through an open session ("Loke sesaun" first).
    const orderMain = `<button type="button" class="btn btn-sm btn-primary" onclick='posOrder.open({ destination: "TABLE", tableId: ${idJs} })'><i class="fa-solid fa-utensils" aria-hidden="true"></i>${L('csh_btn_order')}</button>`;
    const orderPlus = `<button type="button" class="btn btn-sm btn-secondary-app btn-icon js-order-more" onclick='posOrder.open({ destination: "TABLE", tableId: ${idJs} })' title="${L('csh_btn_order_more')}" aria-label="${L('csh_btn_order_more')}"><i class="fa-solid fa-plus" aria-hidden="true"></i></button>`;

    const badge = {
      free: `<span class="badge-soft">${L('filter_avail_tables')}</span>`,
      pending: `<span class="badge-soft tone-info">${L('badge_open_request')}</span>`,
      open: `<span class="badge-soft tone-info">${L('active_session')}</span>`,
      ready_bill: `<span class="badge-soft tone-success">${L('csh_all_served')}</span>`,
      bill: `<span class="badge-soft tone-danger">${L('request_bill')}</span>`,
      paid: `<span class="badge-soft tone-success">${L('csh_bill_paid_waiting')}</span>`,
    }[state];

    let body = '';
    let actions = '';
    let detail = '';
    const idle = Boolean(s && s.is_idle);
    if (s) {
      const sid = escapeHtml(JSON.stringify(String(s.id)));
      const remaining = parseFloat(s.remaining_balance ?? s.bill_total) || 0;
      const expanded = this.expandedTables.has(String(table.id));
      const kitchen = [];
      if (s.waiting_items) kitchen.push(`${escapeHtml(s.waiting_items)} ${L('csh_item_waiting')}`);
      if (s.pending_items) kitchen.push(`${escapeHtml(s.pending_items)} ${L('kds_item_COOKING')}`);
      if (s.ready_items) kitchen.push(`${escapeHtml(s.ready_items)} ${L('kds_item_READY')}`);
      body = `
        <div class="table-tile-stats">
          <span>${escapeHtml(s.guest_count)} ${L('people')} &middot; ${escapeHtml(s.orders_count)} ${L('orders_short')}</span>
          <strong class="tabular">${formatMoney(state === 'paid' ? s.bill_total : remaining)}</strong>
        </div>
        ${kitchen.length ? `<div class="fs-8 text-secondary-app">${kitchen.join(' &middot; ')}</div>` : ''}
        ${idle ? `<div class="table-idle-flag" role="status"><i class="fa-solid fa-hourglass-half" aria-hidden="true"></i>${L('csh_idle_flag')} &middot; ${escapeHtml(s.idle_minutes)} ${L('csh_minutes')}</div>` : ''}
        <button type="button" class="table-tile-toggle" aria-expanded="${expanded ? 'true' : 'false'}" aria-controls="table-detail-${escapeHtml(table.id)}" onclick='cashierApp.toggleTableDetail(${idJs})'>
          <span>${L(expanded ? 'csh_hide_detail' : 'csh_show_detail')}</span><i class="fa-solid fa-chevron-${expanded ? 'up' : 'down'}" aria-hidden="true"></i>
        </button>`;
      detail = `<div class="table-tile-detail" id="table-detail-${escapeHtml(table.id)}"${expanded ? '' : ' hidden'}>${this.renderTableProgress(s)}</div>`;

      if (state === 'paid') {
        actions = `<button type="button" class="btn btn-sm btn-secondary-app" onclick='cashierApp.viewReceiptForSession(${sid})'><i class="fa-solid fa-receipt" aria-hidden="true"></i>${L('receipt_title')}</button>`;
      } else if (state === 'bill') {
        // Payment is taken in Atendimentu only: the button stays visible but cannot be pressed.
        actions = `<button type="button" class="btn btn-sm btn-secondary-app js-bill-requested" disabled aria-disabled="true" title="${L('csh_pay_in_service')}"><i class="fa-solid fa-file-invoice-dollar" aria-hidden="true"></i>${L('csh_bill_requested_wait')}</button>${orderPlus}`;
      } else if (state === 'ready_bill') {
        actions = `<button type="button" class="btn btn-sm btn-primary" onclick='cashierApp.requestBill(${sid}, ${nameJs})' ${remaining <= 0 ? 'disabled' : ''}><i class="fa-solid fa-file-invoice-dollar" aria-hidden="true"></i>${L('csh_btn_request_bill')}</button>${orderPlus}`;
      } else {
        actions = orderMain;
      }
      actions += qrLink;
      const settled = state === 'paid' || remaining <= 0;
      actions += `<button type="button" class="btn btn-sm btn-ghost btn-icon" onclick='cashierApp.handleCloseSession(${sid}, ${nameJs}, ${settled ? 'true' : 'false'}, ${escapeHtml(JSON.stringify(remaining))})' title="${L('close_session')}" aria-label="${L('close_session')}"><i class="fa-solid fa-lock"></i></button>`;
    } else if (state === 'pending') {
      body = `<div class="fs-7 text-secondary-app">${escapeHtml(table.pending_activation.guest_count || 2)} ${L('people')} &middot; ${L('waiting_verification')}</div>`;
      actions = `<button type="button" class="btn btn-sm btn-primary" onclick="cashierApp.approveActivation('${escapeHtml(table.pending_activation.id)}')"><i class="fa-solid fa-check" aria-hidden="true"></i>${L('btn_approve_activation')}</button>${qrLink}`;
    } else {
      body = `<div class="fs-7 text-tertiary">${L('table_empty')}</div>`;
      actions = `<button type="button" class="btn btn-sm btn-primary" onclick='cashierApp.openSessionModal(${idJs}, ${nameJs})'><i class="fa-solid fa-door-open" aria-hidden="true"></i>${L('btn_open_session')}</button>${qrLink}`;
    }

    return `
      <article class="table-tile is-${state === 'ready_bill' ? 'open' : state}${idle ? ' is-idle' : ''}">
        <div class="table-tile-head">
          <div class="min-w-0">
            <div class="table-tile-name truncate">${name}</div>
            <div class="table-tile-meta">${escapeHtml(table.table_code)}${table.capacity ? ` &middot; ${escapeHtml(table.capacity)} ${L('seats')}` : ''}</div>
          </div>
          ${badge}
        </div>
        ${body}
        ${detail}
        <div class="table-tile-actions">${actions}</div>
      </article>`;
  }

  // ------------------------------------------------------------------
  // Bills (service desk) and bill requests
  // ------------------------------------------------------------------
  updateBillAlertsUI() {
    this.updateServiceCount();
    const el = document.getElementById('bill-alerts-container');
    if (!el) return;
    const tableBills = this.tables
      .filter((tb) => tb.active_session?.status === 'BILL_REQUESTED')
      .map((tb) => ({ sessionId: tb.active_session.id, name: tb.display_name, meta: `${tb.table_code} · ${tb.active_session.guest_count} ${t('people')}`,
        remaining: tb.active_session.remaining_balance ?? tb.active_session.bill_total, at: tb.active_session.bill_requested_at }));
    const takeawayBills = this.takeaways
      .filter((o) => o.session_status === 'BILL_REQUESTED')
      .map((o) => ({ sessionId: o.session_id, name: o.table_name, meta: o.order_code, remaining: o.bill?.remaining_balance, at: null }));
    const bills = tableBills.concat(takeawayBills);
    if (!bills.length) {
      el.innerHTML = `<div style="grid-column:1/-1">${emptyState('fa-bell-slash', t('csh_no_bills'))}</div>`;
      return;
    }
    el.innerHTML = bills
      .map((b) => `
          <article class="table-tile is-bill">
            <div class="table-tile-head">
              <div class="min-w-0">
                <div class="table-tile-name truncate">${escapeHtml(b.name)}</div>
                <div class="table-tile-meta">${escapeHtml(b.meta)}</div>
              </div>
              ${b.at ? `<span class="badge-soft tone-danger">${escapeHtml(formatTime(b.at))}</span>` : ''}
            </div>
            <div class="price fs-4">${formatMoney(b.remaining)}</div>
            <button type="button" class="btn btn-primary" onclick='cashierApp.openPaymentModal(${escapeHtml(JSON.stringify(String(b.sessionId)))}, ${escapeHtml(JSON.stringify(String(b.name)))})'><i class="fa-solid fa-cash-register" aria-hidden="true"></i>${escapeHtml(t('csh_btn_pay'))}</button>
          </article>`)
      .join('');
  }

  /** "Minta tagihan": when items are still being prepared the server lists them and we ask before forcing. */
  async requestBill(sessionId, name) {
    const url = `/api/v1/cashier/table-sessions/${sessionId}/request-bill/`;
    try {
      await apiPost(url, {});
    } catch (e) {
      const items = e.details?.items;
      if (e.code !== 'BILL_UNSERVED_ITEMS' && !items) {
        showToast(e.message, 'error');
        return;
      }
      const list = (items || []).map((i) => `${i.quantity}× ${i.name} (${t(`kds_item_${i.status}`)})`).join(', ');
      const ok = await confirmDialog({
        title: `${t('csh_bill_unserved_title')} · ${name}`,
        message: `${t('csh_bill_unserved_msg')} ${list}`,
        confirmText: t('csh_bill_force'),
        tone: 'primary',
      });
      if (!ok) return;
      try {
        await apiPost(url, { force: true });
      } catch (err) {
        showToast(err.message, 'error');
        return;
      }
    }
    // The payment itself is taken in Atendimentu > Konta (one place for every bill).
    showToast(`${t('csh_bill_requested_toast')} ${name} · ${t('csh_pay_in_service')}`, 'success');
    await Promise.all([this.loadTables(), this.loadTakeaways()]);
  }

  openSessionModal(tableId, tableName) {
    this.selectedTableId = tableId;
    document.getElementById('open-session-table-name').textContent = tableName;
    const table = (this.tables || []).find((tb) => String(tb.id) === String(tableId));
    this.selectedGuestLimit = (table && Number(table.guest_limit)) || 25;
    const input = document.getElementById('open-session-guests');
    input.max = String(this.selectedGuestLimit);
    input.value = String(Math.min(2, this.selectedGuestLimit));
    input.classList.remove('is-invalid');
    const hint = document.getElementById('open-session-guests-hint');
    if (hint) hint.textContent = `${t('csh_guest_range')} 1–${this.selectedGuestLimit}`;
    bootstrap.Modal.getOrCreateInstance(document.getElementById('openSessionModal')).show();
  }

  async submitOpenSession() {
    const input = document.getElementById('open-session-guests');
    const guests = Number(input.value);
    const limit = this.selectedGuestLimit || 25;
    if (!Number.isInteger(guests) || guests < 1 || guests > limit) {
      input.classList.add('is-invalid');
      showToast(`${t('csh_guest_range')} 1–${limit}`, 'warning');
      return;
    }
    try {
      await apiPost(`/api/v1/cashier/tables/${this.selectedTableId}/open-session/`, { guest_count: guests });
      bootstrap.Modal.getInstance(document.getElementById('openSessionModal'))?.hide();
      showToast(t('session_opened_success'), 'success');
      SoundEffects.playSuccess();
      this.loadTables();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  /** Padlock: "close normally" (only when settled) or "guests left without paying" (reason required). */
  handleCloseSession(sessionId, tableName, isSettled, remaining) {
    this.closingSession = { id: sessionId, name: tableName, settled: Boolean(isSettled), remaining: Number(remaining) || 0 };
    document.getElementById('close-session-table-name').textContent = tableName;
    document.getElementById('close-session-unpaid').textContent = formatMoney(this.closingSession.remaining);
    const normal = document.getElementById('close-mode-normal');
    const walkout = document.getElementById('close-mode-walkout');
    normal.disabled = !this.closingSession.settled;
    document.getElementById('close-mode-normal-hint').hidden = this.closingSession.settled;
    (this.closingSession.settled ? normal : walkout).checked = true;
    const reason = document.getElementById('close-session-reason');
    reason.value = '';
    reason.classList.remove('is-invalid');
    this.updateCloseMode();
    bootstrap.Modal.getOrCreateInstance(document.getElementById('closeSessionModal')).show();
  }

  updateCloseMode() {
    const walkout = document.getElementById('close-mode-walkout').checked;
    document.getElementById('close-session-walkout-fields').hidden = !walkout;
    const btn = document.getElementById('btn-close-session-submit');
    btn.className = `btn ${walkout ? 'btn-danger' : 'btn-primary'}`;
  }

  async submitCloseSession() {
    const c = this.closingSession;
    if (!c) return;
    const walkout = document.getElementById('close-mode-walkout').checked;
    const reasonEl = document.getElementById('close-session-reason');
    const reason = reasonEl.value.trim();
    if (walkout && reason.length < 3) {
      reasonEl.classList.add('is-invalid');
      reasonEl.focus();
      return;
    }
    const btn = document.getElementById('btn-close-session-submit');
    btn.disabled = true;
    try {
      const res = await apiPost(`/api/v1/cashier/table-sessions/${c.id}/close/`, { mode: walkout ? 'walkout' : 'normal', reason });
      bootstrap.Modal.getInstance(document.getElementById('closeSessionModal'))?.hide();
      if (walkout) showToast(`${t('csh_walkout_done')} ${c.name} (${formatMoney(res?.unpaid_amount || 0)})`, 'warning');
      else showToast(`${t('session_closed')} ${c.name}`, 'info');
      this.expandedTables.delete(String(c.id));
      this.loadTables();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  // ------------------------------------------------------------------
  // Take away
  // ------------------------------------------------------------------
  async loadTakeaways() {
    try {
      this.takeaways = (await apiGet('/api/v1/cashier/takeaway/')) || [];
      this.renderTakeaways();
      this.updateBillAlertsUI();
    } catch (e) {
      console.error('takeaway', e);
    }
  }

  renderTakeaways() {
    const el = document.getElementById('takeaway-list-container');
    setCount('takeaway', this.takeaways.filter((o) => o.ready_for_pickup).length);
    const label = document.getElementById('takeaway-total-label');
    if (label) label.textContent = this.takeaways.length;
    if (!el) return;
    if (!this.takeaways.length) {
      el.innerHTML = `<div style="grid-column:1/-1">${emptyState('fa-bag-shopping', t('csh_no_takeaway'))}</div>`;
      return;
    }
    el.innerHTML = this.takeaways.map((o) => this.renderTakeawayTile(o)).join('');
  }

  renderTakeawayTile(o) {
    const sid = escapeHtml(JSON.stringify(String(o.session_id)));
    const nameJs = escapeHtml(JSON.stringify(String(o.table_name)));
    const items = (o.items || []).map((i) => `
      <li>
        <span class="min-w-0"><strong class="tabular">${escapeHtml(i.quantity)}×</strong> ${i.status === 'UNAVAILABLE' ? `<s>${escapeHtml(i.menu_name_snapshot)}</s>` : escapeHtml(i.menu_name_snapshot)}</span>
        <span class="badge-soft ${ITEM_TONE[i.status] || ''}">${escapeHtml(t(`kds_item_${i.status}`))}</span>
      </li>`).join('');
    const hasReady = (o.items || []).some((i) => i.status === 'READY');
    const paid = o.session_status === 'PAID';
    let pay = '';
    if (o.session_status === 'BILL_REQUESTED') {
      pay = `<button type="button" class="btn btn-sm btn-primary" onclick='cashierApp.openPaymentModal(${sid}, ${nameJs})'><i class="fa-solid fa-cash-register" aria-hidden="true"></i>${escapeHtml(t('csh_btn_pay'))}</button>`;
    } else if (!paid) {
      pay = `<button type="button" class="btn btn-sm btn-primary" onclick='cashierApp.requestBill(${sid}, ${nameJs})'><i class="fa-solid fa-file-invoice-dollar" aria-hidden="true"></i>${escapeHtml(t('csh_btn_request_bill'))}</button>`;
    }
    const handOver = hasReady
      ? `<button type="button" class="btn btn-sm btn-success" onclick='cashierApp.handOver(${sid}, this)'><i class="fa-solid fa-hand-holding" aria-hidden="true"></i>${escapeHtml(t('csh_btn_hand_over'))}</button>`
      : '';
    const badge = o.ready_for_pickup
      ? `<span class="badge-soft tone-success">${escapeHtml(t('csh_ready_pickup'))}</span>`
      : (paid ? `<span class="badge-soft tone-info">${escapeHtml(t('csh_bill_paid_waiting'))}</span>` : `<span class="badge-soft">${escapeHtml(t(`status_${(o.status || '').toLowerCase()}`) || o.status)}</span>`);
    return `
      <article class="table-tile${o.ready_for_pickup ? ' is-bill' : ''}">
        <div class="table-tile-head">
          <div class="min-w-0">
            <div class="table-tile-name truncate">${escapeHtml(o.table_name)}</div>
            <div class="table-tile-meta">${escapeHtml(o.order_code)} &middot; ${escapeHtml(formatTime(o.created_at))}</div>
          </div>
          ${badge}
        </div>
        <ul class="queue-items">${items}</ul>
        <div class="table-tile-stats"><span>${escapeHtml(t('total'))}</span><strong class="tabular">${formatMoney(o.bill?.grand_total ?? o.grand_total)}</strong></div>
        <div class="table-tile-actions">${handOver}${pay}</div>
      </article>`;
  }

  async handOver(sessionId, btn) {
    if (btn) btn.disabled = true;
    try {
      await apiPost(`/api/v1/cashier/table-sessions/${sessionId}/hand-over/`);
      showToast(t('csh_handed_over_toast'), 'success');
      SoundEffects.playSuccess();
      this.loadTakeaways();
    } catch (e) {
      showToast(e.message, 'error');
      if (btn) btn.disabled = false;
    }
  }

  // ------------------------------------------------------------------
  // Kitchen notices
  // ------------------------------------------------------------------
  async loadNotices() {
    try {
      this.notices = (await apiGet('/api/v1/cashier/kitchen-notices/')) || [];
      const ids = new Set(this.notices.map((n) => n.id));
      if (this.knownNoticeIds && [...ids].some((id) => !this.knownNoticeIds.has(id))) SoundEffects.playBell();
      this.knownNoticeIds = ids;
      this.renderNotices();
      this.updateServiceCount();
    } catch (e) {
      console.error('notices', e);
    }
  }

  renderNotices() {
    const el = document.getElementById('kitchen-notices-container');
    if (!el) return;
    if (!this.notices.length) {
      el.innerHTML = `<div style="grid-column:1/-1">${emptyState('fa-kitchen-set', t('csh_no_notices'))}</div>`;
      return;
    }
    el.innerHTML = this.notices.map((n) => `
      <article class="table-tile${n.status === 'OPEN' ? ' is-bill' : ''}">
        <div class="table-tile-head">
          <div class="min-w-0">
            <div class="table-tile-name truncate">${escapeHtml(n.menu_name)}</div>
            <div class="table-tile-meta">${escapeHtml(n.table_label)} &middot; ${escapeHtml(n.order_code)} &middot; ${escapeHtml(formatTime(n.created_at))}</div>
          </div>
          <span class="badge-soft tone-danger">${escapeHtml(t('csh_notice_out'))}</span>
        </div>
        ${n.message ? `<div class="fs-7 text-secondary-app">${escapeHtml(n.message)}</div>` : ''}
        <div class="table-tile-actions">
          <button type="button" class="btn btn-sm btn-danger" onclick="cashierApp.noticeAction('${escapeHtml(n.id)}', 'sold-out', this)"><i class="fa-solid fa-ban" aria-hidden="true"></i>${escapeHtml(t('csh_notice_btn_sold_out'))}</button>
          ${n.status === 'OPEN' ? `<button type="button" class="btn btn-sm btn-secondary-app" onclick="cashierApp.noticeAction('${escapeHtml(n.id)}', 'acknowledge', this)">${escapeHtml(t('csh_notice_btn_ack'))}</button>` : ''}
          <button type="button" class="btn btn-sm btn-ghost" onclick="cashierApp.noticeAction('${escapeHtml(n.id)}', 'resolve', this)">${escapeHtml(t('csh_notice_btn_dismiss'))}</button>
        </div>
      </article>`).join('');
  }

  async noticeAction(noticeId, action, btn) {
    if (btn) btn.disabled = true;
    try {
      const res = await apiPost(`/api/v1/cashier/kitchen-notices/${noticeId}/${action}/`);
      if (action === 'sold-out') {
        const extra = res && res.cancelled_items ? ` (${res.cancelled_items} ${t('stock_cancelled_portions')})` : '';
        showToast(t('csh_notice_sold_out_toast') + extra, 'success');
        this.loadStock();
      }
      this.loadNotices();
    } catch (e) {
      showToast(e.message, 'error');
      if (btn) btn.disabled = false;
    }
  }

  // ------------------------------------------------------------------
  // Menu & Stock (close for today / reopen only)
  // ------------------------------------------------------------------
  async loadStock() {
    try {
      this.stock = (await apiGet('/api/v1/cashier/menu-stock/')) || [];
      this.renderStockChips();
      this.renderStock();
    } catch (e) {
      console.error('stock', e);
    }
  }

  renderStockChips() {
    const el = document.getElementById('stock-category-chips');
    if (!el) return;
    const cats = [];
    this.stock.forEach((r) => {
      if (!cats.some((c) => c.id === r.category_id)) cats.push({ id: r.category_id, name: r.category_name });
    });
    const chip = (id, label) => `<button type="button" class="chip${this.stockCategory === id ? ' active' : ''}" onclick="cashierApp.setStockCategory('${escapeHtml(id)}')">${escapeHtml(label)}</button>`;
    el.innerHTML = chip('all', t('csh_order_all')) + cats.map((c) => chip(c.id, c.name)).join('');
  }

  setStockCategory(id) {
    this.stockCategory = id;
    this.renderStockChips();
    this.renderStock();
  }

  renderStock() {
    const body = document.getElementById('stock-list-body');
    if (!body) return;
    const q = (document.getElementById('stock-search')?.value || '').toLowerCase().trim();
    const rows = this.stock.filter((r) => (this.stockCategory === 'all' || r.category_id === this.stockCategory)
      && (!q || r.name.toLowerCase().includes(q)));
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="6" class="cell-empty">${emptyState('fa-magnifying-glass', t('csh_stock_empty'))}</td></tr>`;
      return;
    }
    const badge = {
      AVAILABLE: `<span class="badge-soft tone-success">${escapeHtml(t('stock_available'))}</span>`,
      CLOSED_TODAY: `<span class="badge-soft tone-danger">${escapeHtml(t('stock_closed_today'))}</span>`,
      QUOTA_REACHED: `<span class="badge-soft tone-warning">${escapeHtml(t('stock_quota_reached'))}</span>`,
      SOLD_OUT: `<span class="badge-soft tone-danger">${escapeHtml(t('stock_sold_out'))}</span>`,
    };
    body.innerHTML = rows.map((r) => {
      const action = r.state === 'CLOSED_TODAY'
        ? `<button type="button" class="btn btn-sm btn-secondary-app" onclick="cashierApp.setSoldOut('${escapeHtml(r.id)}', false, this)">${escapeHtml(t('stock_btn_reopen'))}</button>`
        : (r.state === 'SOLD_OUT' ? '' : `<button type="button" class="btn btn-sm btn-outline-danger" onclick="cashierApp.setSoldOut('${escapeHtml(r.id)}', true, this)">${escapeHtml(t('csh_notice_btn_sold_out'))}</button>`);
      return `
        <tr>
          <td class="cell-primary"><div class="d-flex align-items-center gap-2"><img src="${escapeHtml(r.image)}" alt="" class="thumb" loading="lazy"><div class="min-w-0"><div class="fw-bold truncate">${escapeHtml(r.name)}</div><div class="fs-8 text-tertiary">${escapeHtml(r.category_name)}</div></div></div></td>
          <td data-label="${escapeHtml(t('th_stock_status'))}">${badge[r.state] || ''}</td>
          <td class="text-end tabular" data-label="${escapeHtml(t('stock_th_quota'))}">${r.quota === null ? escapeHtml(t('stock_quota_unlimited')) : escapeHtml(r.quota)}</td>
          <td class="text-end tabular" data-label="${escapeHtml(t('stock_th_sold'))}">${escapeHtml(r.sold)}</td>
          <td class="text-end tabular" data-label="${escapeHtml(t('stock_th_left'))}">${r.remaining === null ? '-' : escapeHtml(r.remaining)}</td>
          <td class="cell-actions text-end">${action}</td>
        </tr>`;
    }).join('');
  }

  async setSoldOut(itemId, closed, btn) {
    if (btn) btn.disabled = true;
    try {
      const res = await apiPost(`/api/v1/cashier/menu-items/${itemId}/sold-out/`, { closed });
      if (closed) {
        const extra = res && res.cancelled_items ? ` (${res.cancelled_items} ${t('stock_cancelled_portions')})` : '';
        showToast(t('stock_closed_toast') + extra, 'success');
      } else {
        showToast(t('stock_reopened_toast'), 'success');
      }
      this.loadStock();
      this.loadNotices();
    } catch (e) {
      showToast(e.message, 'error');
      if (btn) btn.disabled = false;
    }
  }

  // ------------------------------------------------------------------
  // Payment (cash only, against a requested bill, inside an open shift)
  // ------------------------------------------------------------------
  findSession(sessionId) {
    const table = this.tables.find((tb) => tb.active_session && String(tb.active_session.id) === String(sessionId));
    if (table) return table.active_session;
    const ta = this.takeaways.find((o) => String(o.session_id) === String(sessionId));
    if (!ta) return null;
    return {
      id: ta.session_id,
      bill_total: ta.bill?.grand_total,
      remaining_balance: ta.bill?.remaining_balance,
      total_paid: ta.bill?.total_paid,
      items: (ta.items || []).filter((i) => i.status !== 'UNAVAILABLE').map((i) => ({
        name: i.menu_name_snapshot, quantity: i.quantity, subtotal: i.subtotal, note: i.note, order_code: ta.order_code,
      })),
    };
  }

  openPaymentModal(sessionId, tableName) {
    if (!this.shift) {
      showToast(t('csh_shift_required_toast'), 'warning');
      showPanel('shift-panel');
      return;
    }
    const session = this.findSession(sessionId);
    if (!session) {
      showToast(t('RESOURCE_NOT_FOUND'), 'error');
      return;
    }

    this.currentPaymentSessionId = sessionId;
    this.paymentIdempotencyKey = newKey('pay');
    const remaining = parseFloat(session.remaining_balance ?? session.bill_total) || 0;
    this.currentBillAmount = remaining;

    document.getElementById('pos-table-name').textContent = tableName;
    document.getElementById('pos-bill-total').textContent = formatMoney(remaining);
    document.getElementById('pos-tendered-amount').value = remaining.toFixed(2);
    document.getElementById('pos-paid-status').textContent =
      parseFloat(session.total_paid) > 0
        ? `${t('already_paid')}: ${formatMoney(session.total_paid)} · ${t('total')}: ${formatMoney(session.bill_total)}`
        : `${t('total')}: ${formatMoney(session.bill_total)}`;

    const items = session.items || [];
    document.getElementById('pos-order-items-container').innerHTML = items.length
      ? items
          .map(
            (i) => `
            <div class="d-flex justify-content-between gap-2 py-1 fs-7">
              <span class="min-w-0"><strong class="tabular">${escapeHtml(i.quantity)}×</strong> ${escapeHtml(i.name)}
                <span class="d-block text-tertiary fs-8">${escapeHtml(i.order_code)}${i.note ? ` · ${escapeHtml(i.note)}` : ''}</span>
              </span>
              <span class="tabular fw-semibold">${formatMoney(i.subtotal)}</span>
            </div>`
          )
          .join('')
      : emptyState('fa-receipt', t('no_items_to_pay'));

    this.resetPayButton();
    bootstrap.Modal.getOrCreateInstance(document.getElementById('paymentModal')).show();
  }

  setTenderedAmount(value) {
    document.getElementById('pos-tendered-amount').value = Number(value).toFixed(2);
    this.calculateChange();
  }

  calculateChange() {
    const payBtn = document.getElementById('btn-submit-payment');
    const row = document.getElementById('pos-change-row');
    const out = document.getElementById('pos-change-amount');
    const tendered = parseFloat(document.getElementById('pos-tendered-amount').value) || 0;
    const change = Math.round((tendered - this.currentBillAmount) * 100) / 100;
    const short = change < 0;
    row.classList.toggle('is-short', short);
    row.classList.toggle('is-ok', !short);
    out.textContent = short ? `${t('short_by')} ${formatMoney(Math.abs(change))}` : formatMoney(change);
    payBtn.disabled = short || this.currentBillAmount <= 0;
  }

  resetPayButton() {
    const btn = document.getElementById('btn-submit-payment');
    btn.innerHTML = `<i class="fa-solid fa-check" aria-hidden="true"></i><span>${escapeHtml(t('btn_finish_payment'))}</span>`;
    this.calculateChange();
  }

  async processPayment() {
    const btn = document.getElementById('btn-submit-payment');
    if (btn.disabled) return;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>';

    const tendered = parseFloat(document.getElementById('pos-tendered-amount').value) || 0;

    try {
      const payment = await apiPost(
        `/api/v1/cashier/table-sessions/${this.currentPaymentSessionId}/payments/`,
        { tendered_amount: tendered.toFixed(2), method: 'CASH', idempotency_key: this.paymentIdempotencyKey },
        { 'Idempotency-Key': this.paymentIdempotencyKey }
      );
      bootstrap.Modal.getInstance(document.getElementById('paymentModal'))?.hide();
      showToast(`${t('payment_success')} ${formatMoney(payment.amount)} · ${t('change_label')} ${formatMoney(payment.change_amount)}`, 'success');
      SoundEffects.playSuccess();
      this.loadTables();
      this.loadTakeaways();
      this.loadShift();
      this.showReceiptModal(payment);
    } catch (e) {
      showToast(e.message, 'error');
      this.resetPayButton();
    }
  }

  // ------------------------------------------------------------------
  // Receipts
  // ------------------------------------------------------------------
  async viewReceiptForSession(sessionId) {
    try {
      const payment = await apiGet(`/api/v1/cashier/table-sessions/${sessionId}/payments/`);
      this.showReceiptModal(payment);
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  receiptHtml(payment) {
    return Receipt.html(payment);
  }

  showReceiptModal(payment) {
    document.getElementById('receipt-print-content').innerHTML = this.receiptHtml(payment);
    bootstrap.Modal.getOrCreateInstance(document.getElementById('receiptModal')).show();
  }

  openPrintWindow(innerHtml, title) {
    return Receipt.printHtml(innerHtml, title);
  }

  printCurrentReceipt() {
    const el = document.getElementById('printable-receipt');
    if (el) this.openPrintWindow(el.outerHTML, t('receipt_title'));
  }

  // ------------------------------------------------------------------
  // Shift
  // ------------------------------------------------------------------
  async loadShift() {
    try {
      const d = (await apiGet('/api/v1/cashier/shift/')) || {};
      this.shift = d.current || null;
      this.lastShift = d.last_closed || null;
      this.renderShift();
    } catch (e) {
      console.error('shift', e);
    }
  }

  renderShift() {
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    };
    const openBox = document.getElementById('shift-open-box');
    const currentBox = document.getElementById('shift-current-box');
    if (!openBox || !currentBox) return;
    openBox.hidden = Boolean(this.shift);
    currentBox.hidden = !this.shift;
    if (this.shift) {
      set('shift-opened-at', this.shift.opened_at);
      set('shift-opening', formatMoney(this.shift.opening_cash));
      set('shift-cash-sales', formatMoney(this.shift.cash_sales));
      set('shift-expected', formatMoney(this.shift.expected_cash));
      set('shift-payments', this.shift.payments_count);
      this.updateShiftDifference();
    }
    const last = document.getElementById('shift-last-box');
    if (last) {
      last.hidden = !this.lastShift;
      if (this.lastShift) {
        const s = this.lastShift;
        const row = (key, value) => `<div class="d-flex justify-content-between py-1"><span class="text-secondary-app">${escapeHtml(t(key))}</span><strong class="tabular">${escapeHtml(value)}</strong></div>`;
        document.getElementById('shift-last-body').innerHTML =
          row('csh_shift_closed_at', s.closed_at || '-') + row('csh_shift_opening', formatMoney(s.opening_cash))
          + row('csh_shift_cash_sales', formatMoney(s.cash_sales)) + row('csh_shift_expected', formatMoney(s.expected_cash))
          + row('csh_shift_counted_label', formatMoney(s.counted_cash)) + row('csh_shift_difference', formatMoney(s.difference))
          + (s.closing_note ? `<div class="fs-7 text-secondary-app mt-1">${escapeHtml(s.closing_note)}</div>` : '');
      }
    }
  }

  updateShiftDifference() {
    if (!this.shift) return;
    const input = document.getElementById('shift-counted-cash');
    const out = document.getElementById('shift-difference');
    const row = document.getElementById('shift-difference-row');
    if (!input || !out) return;
    if (input.value === '') {
      out.textContent = '-';
      row.classList.remove('is-short', 'is-ok');
      return;
    }
    const diff = Math.round(((parseFloat(input.value) || 0) - parseFloat(this.shift.expected_cash)) * 100) / 100;
    out.textContent = (diff > 0 ? '+' : '') + formatMoney(diff);
    row.classList.toggle('is-short', diff !== 0);
    row.classList.toggle('is-ok', diff === 0);
  }

  async openShift() {
    const raw = document.getElementById('shift-opening-cash').value;
    if (raw === '' || Number(raw) < 0) {
      document.getElementById('shift-opening-cash').classList.add('is-invalid');
      return;
    }
    const btn = document.getElementById('btn-open-shift');
    btn.disabled = true;
    try {
      await apiPost('/api/v1/cashier/shift/open/', { opening_cash: Number(raw).toFixed(2) });
      showToast(t('csh_shift_opened_toast'), 'success');
      document.getElementById('shift-opening-cash').value = '';
      await this.loadShift();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  async closeShift() {
    const input = document.getElementById('shift-counted-cash');
    const note = document.getElementById('shift-note');
    if (input.value === '' || Number(input.value) < 0) {
      input.classList.add('is-invalid');
      return;
    }
    input.classList.remove('is-invalid');
    const diff = Math.round((Number(input.value) - parseFloat(this.shift.expected_cash)) * 100) / 100;
    if (diff !== 0 && !note.value.trim()) {
      note.classList.add('is-invalid');
      showToast(t('csh_shift_note_required'), 'warning');
      return;
    }
    note.classList.remove('is-invalid');
    const ok = await confirmDialog({
      title: t('csh_shift_btn_close'),
      message: `${t('csh_shift_expected')}: ${formatMoney(this.shift.expected_cash)} · ${t('csh_shift_counted_label')}: ${formatMoney(input.value)}`,
      confirmText: t('csh_shift_btn_close'),
      tone: diff === 0 ? 'primary' : 'danger',
    });
    if (!ok) return;
    const btn = document.getElementById('btn-close-shift');
    btn.disabled = true;
    try {
      const closed = await apiPost('/api/v1/cashier/shift/close/', { counted_cash: Number(input.value).toFixed(2), note: note.value.trim() });
      showToast(t('csh_shift_closed_toast'), 'success');
      input.value = '';
      note.value = '';
      this.printShiftReport(closed);
      await this.loadShift();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  /** Z-report of the open shift, or of `data` (the shift just closed), or of the last closed one. */
  printShiftReport(data) {
    const d = data || this.shift || this.lastShift;
    if (!d) {
      showToast(t('csh_shift_none'), 'info');
      return;
    }
    const r = window.CELVASS_RESTAURANT || {};
    const row = (label, value, bold) => `<div style="display:flex;justify-content:space-between;${bold ? 'font-weight:bold' : ''}"><span>${escapeHtml(label)}</span><span>${escapeHtml(value)}</span></div>`;
    const html = `
      <div style="text-align:center"><strong style="font-size:14px">${escapeHtml((r.name || 'Celvass Resto & Bar').toUpperCase())}</strong><br><strong>Z-REPORT</strong></div>
      <div style="border-top:1px dashed #000;border-bottom:1px dashed #000;margin:8px 0;padding:4px 0">
        ${escapeHtml(d.cashier || r.cashier || '')}<br>${escapeHtml(t('csh_shift_active'))}: ${escapeHtml(d.opened_at || '')}
        ${d.closed_at ? `<br>${escapeHtml(t('csh_shift_closed_at'))}: ${escapeHtml(d.closed_at)}` : ''}
      </div>
      ${row(t('csh_shift_opening'), formatMoney(d.opening_cash))}
      ${row(t('csh_shift_cash_sales'), formatMoney(d.cash_sales))}
      ${row(t('csh_shift_payments'), String(d.payments_count ?? 0))}
      <div style="border-top:1px dashed #000;margin:4px 0"></div>
      ${row(t('csh_shift_expected'), formatMoney(d.expected_cash), true)}
      ${d.counted_cash !== null && d.counted_cash !== undefined ? row(t('csh_shift_counted_label'), formatMoney(d.counted_cash)) : ''}
      ${d.difference !== null && d.difference !== undefined ? row(t('csh_shift_difference'), formatMoney(d.difference), true) : ''}
      ${d.closing_note ? `<div style="margin-top:6px">${escapeHtml(d.closing_note)}</div>` : ''}`;
    this.openPrintWindow(html, 'Z-Report');
  }

  // ------------------------------------------------------------------
  // Realtime
  // ------------------------------------------------------------------
  initWebSocket() {
    this.ws = new WebSocketClient('/ws/cashier/', (msg) => {
      const d = msg.data || {};
      const where = d.table_name || d.table_code || '';
      if (msg.event === 'TABLE_ACTIVATION_REQUESTED') {
        SoundEffects.playBell();
        showToast(`${t('table_activation_requested_title')}: ${where}`, 'warning');
        this.loadActivationRequests();
        this.loadTables();
      } else if (msg.event === 'NEW_ORDER_WAITING' || msg.event === 'ORDER_CREATED') {
        SoundEffects.playBell();
        showToast(`${t('new_order_toast')} ${d.order_code || ''} · ${where}`, 'warning');
        this.loadPendingOrders();
      } else if (msg.event === 'BILL_REQUESTED') {
        if (!d.requested_by_staff) {
          SoundEffects.playBell();
          showToast(`${t('bill_request_toast')} ${where}`, 'info');
        }
        this.loadTables();
        this.loadTakeaways();
      } else if (msg.event === 'BILL_REOPENED') {
        showToast(`${t('csh_bill_reopened_toast')} ${where}`, 'warning');
        this.loadTables();
      } else if (msg.event === 'KITCHEN_NOTICE') {
        SoundEffects.playBell();
        showToast(`${t('csh_notice_new_toast')} ${d.menu_name || ''}`, 'warning');
        this.loadNotices();
      } else if (msg.event === 'KITCHEN_NOTICE_UPDATED' || msg.event === 'MENU_AVAILABILITY_CHANGED') {
        this.loadNotices();
        this.loadStock();
      } else if (msg.event === 'TAKEAWAY_READY') {
        SoundEffects.playBell();
        showToast(`${t('csh_takeaway_ready_toast')} ${where}`, 'success');
        this.loadTakeaways();
      } else if (msg.event === 'ITEM_STATUS_CHANGED') {
        this.loadTakeaways();
        this.loadTables();
      } else if (['SESSION_OPENED', 'SESSION_CLOSED', 'PAYMENT_COMPLETED'].includes(msg.event)) {
        this.loadActivationRequests();
        this.loadTables();
        this.loadTakeaways();
      }
    });
  }
}
