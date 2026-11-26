/**
 * Kitchen Display System controller.
 * Columns: CONFIRMED (new) -> PREPARING -> READY. Tickets older than 10 min are flagged,
 * older than 20 min are marked late.
 */

const KDS_WARN_MINUTES = 10;
const KDS_LATE_MINUTES = 20;

class KitchenKDSApp {
  constructor() {
    this.orders = [];
    this.busy = new Set();
    this.init();
  }

  init() {
    this.loadQueue();
    this.initWebSocket();
    window.addEventListener('languageChanged', () => this.renderKDS());
    setInterval(() => this.updateElapsedTimers(), 1000);
    setInterval(() => {
      if (!document.hidden) this.loadQueue();
    }, 8000);
  }

  async loadQueue() {
    try {
      this.orders = (await apiGet('/api/v1/kitchen/orders/')) || [];
      this.renderKDS();
    } catch (e) {
      console.error('kitchen queue', e);
    }
  }

  renderKDS() {
    const groups = { CONFIRMED: [], PREPARING: [], READY: [] };
    this.orders.forEach((o) => groups[o.status]?.push(o));

    const cols = {
      CONFIRMED: ['kds-confirmed-col', 'count-confirmed'],
      PREPARING: ['kds-preparing-col', 'count-preparing'],
      READY: ['kds-ready-col', 'count-ready'],
    };

    Object.entries(cols).forEach(([status, [colId, countId]]) => {
      const list = groups[status];
      const col = document.getElementById(colId);
      const count = document.getElementById(countId);
      if (count) count.textContent = list.length;
      document.querySelectorAll(`[data-count-for="${status}"]`).forEach((el) => {
        el.textContent = list.length > 0 ? list.length : '';
      });
      if (!col) return;
      col.innerHTML = list.length
        ? list.map((o) => this.renderCard(o, status)).join('')
        : `<div class="empty-state"><i class="fa-solid fa-mug-hot" aria-hidden="true"></i><div class="fs-7">${escapeHtml(t('kds_empty_col'))}</div></div>`;
    });

    this.updateElapsedTimers();
  }

  renderCard(order, column) {
    const action = {
      CONFIRMED: ['startPreparing', 'btn-primary', 'fa-fire', 'btn_start_cooking'],
      PREPARING: ['markReady', 'btn-success', 'fa-bell', 'btn_mark_ready'],
      READY: ['markServed', 'btn-secondary-app', 'fa-check-double', 'btn_mark_served'],
    }[column];
    const startedAt = order.confirmed_at || order.submitted_at || order.created_at;

    const items = (order.items || []).map((i) => this.renderItem(order, i)).join('');
    const active = (order.items || []).filter((i) => i.status !== 'UNAVAILABLE');
    const doneCount = active.filter((i) => i.status === 'READY' || i.status === 'SERVED').length;
    const progress = active.length > 1
      ? `<div class="ticket-progress">${escapeHtml(doneCount)}/${escapeHtml(active.length)} ${escapeHtml(t('kds_items_progress'))}</div>`
      : '';

    return `
      <article class="ticket" id="card-${escapeHtml(order.id)}" data-started="${escapeHtml(startedAt || '')}">
        <div class="ticket-head">
          <div class="min-w-0">
            <div class="ticket-table truncate">${order.is_takeaway ? `<span class="badge-soft tone-info me-1"><i class="fa-solid fa-bag-shopping" aria-hidden="true"></i>${escapeHtml(t('csh_status_takeaway'))}</span>` : ''}${escapeHtml(order.table_name || order.table_code)}</div>
            <div class="ticket-code">${escapeHtml(order.order_code)}</div>
          </div>
          <span class="ticket-timer" aria-label="${escapeHtml(t('elapsed_time'))}">--:--</span>
        </div>
        <ul class="ticket-items">${items}</ul>
        ${progress}
        ${order.customer_note ? `<div class="ticket-table-note"><strong>${escapeHtml(t('customer_notes'))}</strong> ${escapeHtml(order.customer_note)}</div>` : ''}
        <button type="button" class="btn ${action[1]} w-100" onclick="kdsApp.${action[0]}('${escapeHtml(order.id)}', this)">
          <i class="fa-solid ${action[2]}" aria-hidden="true"></i>${escapeHtml(t(action[3]))}
        </button>
      </article>`;
  }

  /** One ticket line: quantity, name, status chip and the single next action for that line. */
  renderItem(order, item) {
    const status = item.status || 'QUEUED';
    const next = {
      QUEUED: ['start', 'btn-primary', 'fa-fire', 'kds_item_btn_start'],
      COOKING: ['ready', 'btn-success', 'fa-bell', 'kds_item_btn_ready'],
      READY: ['served', 'btn-secondary-app', 'fa-check', 'kds_item_btn_served'],
    }[status];
    const tone = { QUEUED: 'tone-warning', COOKING: 'tone-accent', READY: 'tone-success', SERVED: '', UNAVAILABLE: 'tone-danger' }[status] || '';
    const button = next
      ? `<button type="button" class="btn btn-sm ${next[1]} ticket-item-btn" onclick="kdsApp.itemAction('${escapeHtml(item.id)}', '${next[0]}', this)">
           <i class="fa-solid ${next[2]}" aria-hidden="true"></i>${escapeHtml(t(next[3]))}
         </button>`
      : '';
    const canRunOut = status === 'QUEUED' || status === 'COOKING';
    const outButton = canRunOut
      ? `<button type="button" class="btn btn-sm btn-outline-danger ticket-item-btn" data-out-label="${escapeHtml(t('kds_item_btn_unavailable'))}" data-out-confirm="${escapeHtml(t('kds_item_confirm_unavailable'))}" onclick="kdsApp.reportOut('${escapeHtml(item.id)}', this)">
           <i class="fa-solid fa-ban" aria-hidden="true"></i><span>${escapeHtml(t('kds_item_btn_unavailable'))}</span>
         </button>`
      : '';
    return `
        <li class="ticket-item is-${escapeHtml(status.toLowerCase())}" data-item-id="${escapeHtml(item.id)}">
          <span class="ticket-qty">${escapeHtml(item.quantity)}×</span>
          <span class="min-w-0 flex-grow-1">
            <span class="ticket-item-name">${escapeHtml(item.menu_name_snapshot)}</span>
            <span class="badge-soft ${tone} ms-1">${escapeHtml(t(`kds_item_${status}`))}</span>
            ${item.note ? `<span class="ticket-note"><i class="fa-solid fa-triangle-exclamation me-1" aria-hidden="true"></i>${escapeHtml(item.note)}</span>` : ''}
            ${status === 'UNAVAILABLE' && item.unavailable_reason ? `<span class="ticket-note">${escapeHtml(item.unavailable_reason)}</span>` : ''}
          </span>
          <span class="ticket-item-actions">${button}${outButton}</span>
        </li>`;
  }

  /** Two taps: the first arms the button, the second reports the dish as out of stock (no browser dialogs). */
  async reportOut(itemId, btn) {
    if (!btn.dataset.armed) {
      btn.dataset.armed = '1';
      btn.querySelector('span').textContent = btn.dataset.outConfirm;
      btn.classList.replace('btn-outline-danger', 'btn-danger');
      setTimeout(() => {
        if (!btn.isConnected || !btn.dataset.armed) return;
        delete btn.dataset.armed;
        btn.querySelector('span').textContent = btn.dataset.outLabel;
        btn.classList.replace('btn-danger', 'btn-outline-danger');
      }, 4000);
      return;
    }
    const key = `out:${itemId}`;
    if (this.busy.has(key)) return;
    this.busy.add(key);
    btn.disabled = true;
    try {
      await apiPost(`/api/v1/kitchen/items/${itemId}/unavailable/`, {});
      showToast(t('kds_item_reported'), 'warning');
      await this.loadQueue();
    } catch (e) {
      showToast(e.message, 'error');
      btn.disabled = false;
    } finally {
      this.busy.delete(key);
    }
  }

  async itemAction(itemId, action, btn) {
    const key = `item:${itemId}`;
    if (this.busy.has(key)) return;
    this.busy.add(key);
    if (btn) btn.disabled = true;
    try {
      await apiPost(`/api/v1/kitchen/items/${itemId}/${action}/`);
      if (action === 'ready') SoundEffects.playBell();
      await this.loadQueue();
    } catch (e) {
      showToast(e.message, 'error');
      if (btn) btn.disabled = false;
    } finally {
      this.busy.delete(key);
    }
  }

  updateElapsedTimers() {
    const now = Date.now();
    document.querySelectorAll('.ticket[data-started]').forEach((card) => {
      const start = new Date(card.dataset.started).getTime();
      const timer = card.querySelector('.ticket-timer');
      if (!timer || Number.isNaN(start)) return;
      const secs = Math.max(0, Math.floor((now - start) / 1000));
      const mins = Math.floor(secs / 60);
      timer.textContent = `${String(mins).padStart(2, '0')}:${String(secs % 60).padStart(2, '0')}`;
      const late = mins >= KDS_LATE_MINUTES;
      const warn = !late && mins >= KDS_WARN_MINUTES;
      timer.classList.toggle('is-late', late);
      timer.classList.toggle('is-warn', warn);
      card.classList.toggle('is-late', late);
      card.classList.toggle('is-warn', warn);
    });
  }

  async transition(orderId, btn, path, onDone) {
    if (this.busy.has(orderId)) return;
    this.busy.add(orderId);
    if (btn) btn.disabled = true;
    try {
      await apiPost(`/api/v1/kitchen/orders/${orderId}/${path}/`);
      if (onDone) onDone();
      await this.loadQueue();
    } catch (e) {
      showToast(e.message, 'error');
      if (btn) btn.disabled = false;
    } finally {
      this.busy.delete(orderId);
    }
  }

  startPreparing(orderId, btn) {
    return this.transition(orderId, btn, 'start');
  }

  markReady(orderId, btn) {
    return this.transition(orderId, btn, 'ready', () => SoundEffects.playBell());
  }

  markServed(orderId, btn) {
    return this.transition(orderId, btn, 'served');
  }

  initWebSocket() {
    this.wsClient = new WebSocketClient('/ws/kitchen/', (msg) => {
      if (msg.event === 'ORDER_CONFIRMED') {
        SoundEffects.playBell();
        showToast(`${t('new_kitchen_ticket')} ${msg.data?.table_name || msg.data?.table_code || ''} (${msg.data?.order_code || ''})`, 'warning');
        this.loadQueue();
      } else if (['ORDER_PREPARING', 'ORDER_READY', 'ORDER_SERVED', 'ORDER_CANCELLED', 'ITEM_STATUS_CHANGED', 'MENU_AVAILABILITY_CHANGED'].includes(msg.event)) {
        this.loadQueue();
      }
    });
  }
}
