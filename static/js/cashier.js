/**
 * Cashier portal controller: verification queue, table activation, tables & POS payment,
 * bill requests, receipts and shift close (Z-report). Order and receipt history lives in /reports/#history.
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

class CashierApp {
  constructor() {
    this.pendingOrders = [];
    this.tables = [];
    this.activationRequests = [];
    this.tableFilter = 'ALL';
    this.selectedTableId = null;
    this.currentPaymentSessionId = null;
    this.currentBillAmount = 0;
    this.paymentIdempotencyKey = null;
    this.currentShiftData = null;

    this.knownActivationIds = null;
    this.knownOrderIds = null;
    this.knownBillSessionIds = null;

    this.init();
  }

  init() {
    // Bookmarks from before the history moved to Reports.
    if (/^#orders-history-panel/.test(window.location.hash)) {
      window.location.replace('/reports/#history');
      return;
    }
    this.refreshAll();
    this.initWebSocket();

    window.addEventListener('languageChanged', () => {
      this.renderPendingOrdersUI();
      this.renderActivationRequestsUI();
      this.renderTablesUI();
      this.updateBillAlertsUI();
    });

    window.addEventListener('app:panel', (e) => {
      const id = e.detail.id;
      if (id === 'shift-close-panel') this.loadShiftSummary();
      if (id === 'bill-alerts-panel') this.loadTables();
    });

    setInterval(() => {
      if (document.hidden) return;
      this.loadPendingOrders();
      this.loadTables();
      this.loadActivationRequests();
    }, 8000);
  }

  refreshAll() {
    this.loadPendingOrders();
    this.loadTables();
    this.loadActivationRequests();
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
              <div class="fs-7 text-tertiary">${escapeHtml(t('table_activation_requested_title'))} &middot; ${escapeHtml(req.guest_count || 2)} ${escapeHtml(t('people'))}</div>
            </div>
            <span class="badge-soft tone-info">${escapeHtml(t('badge_open_request'))}</span>
          </div>
          <div class="d-flex gap-2 mt-2">
            <button type="button" class="btn btn-sm btn-primary flex-grow-1" onclick="cashierApp.approveActivation('${escapeHtml(req.id)}')"><i class="fa-solid fa-check" aria-hidden="true"></i>${escapeHtml(t('btn_approve_activation'))}</button>
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

  async approveActivation(requestId) {
    try {
      await apiPost(`/api/v1/cashier/activation-requests/${requestId}/approve/`);
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
      this.updateBillAlertsUI();
    } catch (e) {
      console.error('tables', e);
    }
  }

  sessionState(table) {
    const s = table.active_session;
    if (!s) return table.pending_activation ? 'pending' : 'free';
    const remaining = parseFloat(s.remaining_balance ?? s.bill_total) || 0;
    if (s.is_fully_paid || s.status === 'PAID' || (remaining <= 0 && parseFloat(s.bill_total) > 0)) return 'paid';
    if (s.status === 'BILL_REQUESTED') return 'bill';
    return 'open';
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
    const qrLink = `<a class="btn btn-sm btn-ghost btn-icon" href="/t/${encodeURIComponent(table.qr_token)}/" target="_blank" rel="noopener" title="${escapeHtml(t('open_table_menu'))}" aria-label="${escapeHtml(t('open_table_menu'))}"><i class="fa-solid fa-qrcode"></i></a>`;

    const badge = {
      free: `<span class="badge-soft">${escapeHtml(t('filter_avail_tables'))}</span>`,
      pending: `<span class="badge-soft tone-info">${escapeHtml(t('badge_open_request'))}</span>`,
      open: `<span class="badge-soft tone-info">${escapeHtml(t('active_session'))}</span>`,
      bill: `<span class="badge-soft tone-danger">${escapeHtml(t('request_bill'))}</span>`,
      paid: `<span class="badge-soft tone-success">${escapeHtml(t('paid_status'))}</span>`,
    }[state];

    let body = '';
    let actions = '';
    if (s) {
      const remaining = parseFloat(s.remaining_balance ?? s.bill_total) || 0;
      body = `
        <div class="table-tile-stats">
          <span>${escapeHtml(s.guest_count)} ${escapeHtml(t('people'))} &middot; ${escapeHtml(s.orders_count)} ${escapeHtml(t('orders_short'))}</span>
          <strong class="tabular">${formatMoney(state === 'paid' ? s.bill_total : remaining)}</strong>
        </div>`;
      actions =
        state === 'paid'
          ? `<button type="button" class="btn btn-sm btn-secondary-app" onclick="cashierApp.viewReceiptForSession('${escapeHtml(s.id)}')"><i class="fa-solid fa-receipt" aria-hidden="true"></i>${escapeHtml(t('receipt_title'))}</button>`
          : `<button type="button" class="btn btn-sm btn-primary" onclick='cashierApp.openPaymentModal(${escapeHtml(JSON.stringify(String(s.id)))}, ${nameJs})' ${remaining <= 0 ? 'disabled' : ''}><i class="fa-solid fa-cash-register" aria-hidden="true"></i>${escapeHtml(t('btn_pay_pos'))}</button>`;
      actions += qrLink;
      actions += `<button type="button" class="btn btn-sm btn-ghost btn-icon" onclick='cashierApp.handleCloseSession(${escapeHtml(JSON.stringify(String(s.id)))}, ${nameJs}, ${state === 'paid' || remaining <= 0 ? 'true' : 'false'})' title="${escapeHtml(t('close_session'))}" aria-label="${escapeHtml(t('close_session'))}"><i class="fa-solid fa-lock"></i></button>`;
    } else if (state === 'pending') {
      body = `<div class="fs-7 text-secondary-app">${escapeHtml(table.pending_activation.guest_count || 2)} ${escapeHtml(t('people'))} &middot; ${escapeHtml(t('waiting_verification'))}</div>`;
      actions = `<button type="button" class="btn btn-sm btn-primary" onclick="cashierApp.approveActivation('${escapeHtml(table.pending_activation.id)}')"><i class="fa-solid fa-check" aria-hidden="true"></i>${escapeHtml(t('btn_approve_activation'))}</button>${qrLink}`;
    } else {
      body = `<div class="fs-7 text-tertiary">${escapeHtml(t('table_empty'))}</div>`;
      actions = `<button type="button" class="btn btn-sm btn-secondary-app" onclick='cashierApp.openSessionModal(${escapeHtml(JSON.stringify(String(table.id)))}, ${nameJs})'><i class="fa-solid fa-door-open" aria-hidden="true"></i>${escapeHtml(t('btn_open_session'))}</button>${qrLink}`;
    }

    return `
      <article class="table-tile is-${state}">
        <div class="table-tile-head">
          <div class="min-w-0">
            <div class="table-tile-name truncate">${name}</div>
            <div class="table-tile-meta">${escapeHtml(table.table_code)}${table.capacity ? ` &middot; ${escapeHtml(table.capacity)} ${escapeHtml(t('seats'))}` : ''}</div>
          </div>
          ${badge}
        </div>
        ${body}
        <div class="table-tile-actions">${actions}</div>
      </article>`;
  }

  updateBillAlertsUI() {
    const bills = this.tables.filter((tb) => tb.active_session?.status === 'BILL_REQUESTED');
    setCount('bills', bills.length);
    const el = document.getElementById('bill-alerts-container');
    if (!el) return;
    if (!bills.length) {
      el.innerHTML = `<div style="grid-column:1/-1">${emptyState('fa-bell-slash', t('no_bill_alerts'))}</div>`;
      return;
    }
    el.innerHTML = bills
      .map((tb) => {
        const s = tb.active_session;
        const remaining = parseFloat(s.remaining_balance ?? s.bill_total) || 0;
        return `
          <article class="table-tile is-bill">
            <div class="table-tile-head">
              <div class="min-w-0">
                <div class="table-tile-name truncate">${escapeHtml(tb.display_name)}</div>
                <div class="table-tile-meta">${escapeHtml(tb.table_code)} &middot; ${escapeHtml(s.guest_count)} ${escapeHtml(t('people'))}</div>
              </div>
              <span class="badge-soft tone-danger">${escapeHtml(formatTime(s.bill_requested_at))}</span>
            </div>
            <div class="price fs-4">${formatMoney(remaining)}</div>
            <button type="button" class="btn btn-primary" onclick='cashierApp.openPaymentModal(${escapeHtml(JSON.stringify(String(s.id)))}, ${escapeHtml(JSON.stringify(String(tb.display_name)))})'><i class="fa-solid fa-cash-register" aria-hidden="true"></i>${escapeHtml(t('btn_pay_pos'))}</button>
          </article>`;
      })
      .join('');
  }

  openSessionModal(tableId, tableName) {
    this.selectedTableId = tableId;
    document.getElementById('open-session-table-name').textContent = tableName;
    document.getElementById('open-session-guests').value = '2';
    bootstrap.Modal.getOrCreateInstance(document.getElementById('openSessionModal')).show();
  }

  async submitOpenSession() {
    const raw = document.getElementById('open-session-guests').value;
    const guests = parseInt(raw, 10);
    if (!Number.isInteger(guests) || guests < 1 || guests > 25) {
      showToast(t('guest_count_hint'), 'warning');
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

  async handleCloseSession(sessionId, tableName, isSettled) {
    const ok = await confirmDialog({
      title: `${t('close_session')} · ${tableName}`,
      message: isSettled ? t('close_session_msg') : t('close_session_unpaid_msg'),
      confirmText: t('close_session'),
      tone: isSettled ? 'primary' : 'danger',
    });
    if (!ok) return;
    try {
      await apiPost(`/api/v1/cashier/table-sessions/${sessionId}/close/`);
      showToast(`${t('session_closed')} ${tableName}`, 'info');
      this.loadTables();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  // ------------------------------------------------------------------
  // Payment
  // ------------------------------------------------------------------
  openPaymentModal(sessionId, tableName) {
    const table = this.tables.find((tb) => tb.active_session && String(tb.active_session.id) === String(sessionId));
    const session = table?.active_session;
    if (!session) {
      showToast(t('RESOURCE_NOT_FOUND'), 'error');
      return;
    }

    this.currentPaymentSessionId = sessionId;
    this.paymentIdempotencyKey = crypto?.randomUUID ? crypto.randomUUID() : `pay_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    const remaining = parseFloat(session.remaining_balance ?? session.bill_total) || 0;
    this.currentBillAmount = remaining;

    document.getElementById('pos-table-name').textContent = tableName;
    document.getElementById('pos-bill-total').textContent = formatMoney(remaining);
    document.getElementById('pos-payment-method').value = 'CASH';
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

    this.onMethodChange();
    this.resetPayButton();
    bootstrap.Modal.getOrCreateInstance(document.getElementById('paymentModal')).show();
  }

  onMethodChange() {
    const isCash = document.getElementById('pos-payment-method').value === 'CASH';
    document.getElementById('pos-cash-fields').hidden = !isCash;
    this.calculateChange();
  }

  setTenderedAmount(value) {
    document.getElementById('pos-tendered-amount').value = Number(value).toFixed(2);
    this.calculateChange();
  }

  calculateChange() {
    const isCash = document.getElementById('pos-payment-method').value === 'CASH';
    const payBtn = document.getElementById('btn-submit-payment');
    const row = document.getElementById('pos-change-row');
    const out = document.getElementById('pos-change-amount');
    if (!isCash) {
      payBtn.disabled = this.currentBillAmount <= 0;
      return;
    }
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

    const method = document.getElementById('pos-payment-method').value;
    const tendered = method === 'CASH' ? parseFloat(document.getElementById('pos-tendered-amount').value) || 0 : this.currentBillAmount;

    try {
      const payment = await apiPost(
        `/api/v1/cashier/table-sessions/${this.currentPaymentSessionId}/payments/`,
        { tendered_amount: tendered.toFixed(2), method, idempotency_key: this.paymentIdempotencyKey },
        { 'Idempotency-Key': this.paymentIdempotencyKey }
      );
      bootstrap.Modal.getInstance(document.getElementById('paymentModal'))?.hide();
      showToast(`${t('payment_success')} ${formatMoney(payment.amount)} · ${t('change_label')} ${formatMoney(payment.change_amount)}`, 'success');
      SoundEffects.playSuccess();
      this.loadTables();
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
  // Shift close
  // ------------------------------------------------------------------
  async loadShiftSummary() {
    try {
      const d = await apiGet('/api/v1/cashier/shift/summary/');
      this.currentShiftData = d;
      const set = (id, v) => {
        const el = document.getElementById(id);
        if (el) el.textContent = v;
      };
      set('shift-cash-total', formatMoney(d.cash_total));
      set('shift-other-total', formatMoney(d.other_total));
      set('shift-grand-total', formatMoney(d.grand_total));
      set('shift-paid-sessions-count', d.paid_sessions_count);
      set('shift-completed-orders-count', d.completed_orders_count);
      set('shift-payments-count', d.payments_count);
      set('shift-report-timestamp', d.timestamp);
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  async printShiftReport() {
    if (!this.currentShiftData) await this.loadShiftSummary();
    const d = this.currentShiftData || {};
    const r = window.CELVASS_RESTAURANT || {};
    const row = (label, value, bold) => `<div style="display:flex;justify-content:space-between;${bold ? 'font-weight:bold' : ''}"><span>${escapeHtml(label)}</span><span>${escapeHtml(value)}</span></div>`;
    const html = `
      <div style="text-align:center"><strong style="font-size:14px">${escapeHtml((r.name || 'Celvass Resto & Bar').toUpperCase())}</strong><br><strong>Z-REPORT</strong></div>
      <div style="border-top:1px dashed #000;border-bottom:1px dashed #000;margin:8px 0;padding:4px 0">${escapeHtml(t('date_time'))}: ${escapeHtml(d.timestamp || new Date().toLocaleString())}</div>
      ${row(t('cash_total'), formatMoney(d.cash_total))}
      ${row(t('card_total'), formatMoney(d.other_total))}
      <div style="border-top:1px dashed #000;margin:4px 0"></div>
      ${row(t('shift_revenue'), formatMoney(d.grand_total), true)}
      <div style="border-top:1px dashed #000;margin:8px 0 4px"></div>
      ${row(t('paid_tables_count'), String(d.paid_sessions_count ?? 0))}
      ${row(t('completed_orders_count'), String(d.completed_orders_count ?? 0))}
      ${row(t('payments_count'), String(d.payments_count ?? 0))}`;
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
        SoundEffects.playBell();
        showToast(`${t('bill_request_toast')} ${where}`, 'info');
        this.loadTables();
      } else if (['SESSION_OPENED', 'SESSION_CLOSED', 'PAYMENT_COMPLETED'].includes(msg.event)) {
        this.loadActivationRequests();
        this.loadTables();
      }
    });
  }
}
