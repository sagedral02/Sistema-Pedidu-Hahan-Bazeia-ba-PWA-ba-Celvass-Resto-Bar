/**
 * Customer dine-in controller: menu filtering, cart, order submission, order status and bill request.
 *
 * Device lock: the server issues an httpOnly device cookie when the table page loads. The browser
 * sends it automatically with every same-origin request, so this script never handles the id.
 * Prices shown here are for display only; the server recalculates every order.
 */

const ORDER_STATUS = {
  WAITING_CASHIER_CONFIRMATION: { cls: 'status-waiting', key: 'status_waiting' },
  CONFIRMED: { cls: 'status-confirmed', key: 'status_confirmed' },
  PREPARING: { cls: 'status-preparing', key: 'status_preparing' },
  READY: { cls: 'status-ready', key: 'status_ready' },
  SERVED: { cls: 'status-served', key: 'status_served' },
  COMPLETED: { cls: 'status-completed', key: 'status_completed' },
  REJECTED: { cls: 'status-rejected', key: 'status_rejected' },
  CANCELLED: { cls: 'status-cancelled', key: 'status_cancelled' },
};

// Must match apps/tables/services.request_bill_for_session.
const COOKING_STATUSES = ['WAITING_CASHIER_CONFIRMATION', 'CONFIRMED', 'PREPARING', 'READY'];

class CustomerApp {
  constructor(config) {
    this.sessionToken = config.sessionToken || '';
    this.sessionActive = Boolean(config.sessionActive);
    this.qrToken = config.qrToken || '';
    this.tableCode = config.tableCode || '';
    this.tableName = config.tableName || '';
    this.defaultImage = config.defaultImage || '/static/icons/default-food.png';
    this.guestLimit = Math.max(1, parseInt(config.guestLimit, 10) || 25);
    this.pendingMine = Boolean(config.pendingMine);
    this.isOpen = config.isOpen !== false;
    this.sessionStatus = config.sessionStatus || '';
    this.leaving = false;

    this.storageKey = `celvass_cart_${this.sessionToken || this.qrToken || 'default'}`;
    this.cart = this.loadCart();
    this.activeOrders = [];
    this.lastOrdersJson = null;
    this.sessionWatchTimer = null;
    this.isSessionOpening = false;
    this.activeCategory = 'all';
    this.modalItem = null;
    this.modalQty = 1;

    this.init();
  }

  // ------------------------------------------------------------------
  // Setup
  // ------------------------------------------------------------------
  init() {
    if (!this.sessionToken && this.qrToken && this.pendingMine) this.startActivationFlow();

    this.bindMenu();
    this.bindItemModal();
    this.renderCartUI();
    this.initWebSocket();
    this.loadActiveOrders();
    this.checkBillButtonEligibility();

    window.addEventListener('languageChanged', () => {
      this.renderCartUI();
      this.renderOrdersUI();
      this.checkBillButtonEligibility();
    });
  }

  bindMenu() {
    document.querySelectorAll('.chip[data-category]').forEach((chip) => {
      chip.addEventListener('click', () => this.setCategory(chip.dataset.category));
    });

    const search = document.getElementById('menu-search');
    if (search) {
      search.addEventListener('input', () => this.applyFilters());
      search.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          search.value = '';
          this.applyFilters();
        }
      });
    }

    document.querySelectorAll('#menu-grid, .menu-highlights').forEach((root) => {
      root.addEventListener('click', (e) => {
        const card = e.target.closest('.menu-card');
        if (card) this.handleCardClick(card);
      });
    });
  }

  setCategory(slug) {
    this.activeCategory = slug || 'all';
    document.querySelectorAll('.chip[data-category]').forEach((chip) => {
      const active = chip.dataset.category === this.activeCategory;
      chip.classList.toggle('active', active);
      chip.setAttribute('aria-pressed', String(active));
      if (active) chip.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
    });
    this.applyFilters();
  }

  /** Highlight rows follow the category chip and are hidden while searching. */
  syncHighlights(q) {
    document.querySelectorAll('.menu-highlights[data-highlight-for]').forEach((box) => {
      box.classList.toggle('d-none', Boolean(q) || box.dataset.highlightFor !== this.activeCategory);
    });
  }

  applyFilters() {
    const q = (document.getElementById('menu-search')?.value || '').toLowerCase().trim();
    this.syncHighlights(q);
    let visible = 0;
    document.querySelectorAll('.menu-item-col').forEach((col) => {
      const matchCat = this.activeCategory === 'all' || col.dataset.category === this.activeCategory;
      const matchQ = !q || (col.dataset.name || '').includes(q) || (col.dataset.desc || '').includes(q);
      const show = matchCat && matchQ;
      col.hidden = !show;
      if (show) visible += 1;
    });
    const empty = document.getElementById('menu-empty');
    if (empty) empty.hidden = visible > 0;
  }

  handleCardClick(card) {
    if (card.dataset.available !== '1') {
      showToast(t('sold_out'), 'info');
      return;
    }
    if (!this.sessionActive) {
      if (!this.isOpen) {
        showToast(t('cust_closed_title'), 'warning');
        return;
      }
      this.showGuestStep();
      showToast(t('session_not_open_toast'), 'warning');
      return;
    }
    this.openItemModal({
      remaining: card.dataset.remaining === '' || card.dataset.remaining === undefined ? null : parseInt(card.dataset.remaining, 10),
      id: card.dataset.itemId,
      name: card.dataset.itemName,
      price: parseFloat(card.dataset.itemPrice) || 0,
      image: card.dataset.itemImage || this.defaultImage,
      desc: card.dataset.itemDesc || '',
    });
  }

  // ------------------------------------------------------------------
  // Item modal
  // ------------------------------------------------------------------
  bindItemModal() {
    const addBtn = document.getElementById('btn-add-modal-item');
    if (addBtn) {
      addBtn.addEventListener('click', () => {
        if (!this.modalItem) return;
        const note = (document.getElementById('modal-item-note')?.value || '').trim();
        this.addToCart(this.modalItem, this.modalQty, note);
        bootstrap.Modal.getInstance(document.getElementById('itemCustomModal'))?.hide();
      });
    }
    window.adjustModalQty = (delta) => {
      const cap = this.maxAddable(this.modalItem);
      this.modalQty = Math.min(cap, Math.max(1, this.modalQty + delta));
      if (delta > 0 && this.modalQty === cap && this.modalItem && this.modalItem.remaining !== null) {
        showToast(`${t('cust_max_portions')} ${this.modalItem.remaining}`, 'info');
      }
      this.renderModalQty();
    };
  }

  openItemModal(item) {
    this.modalItem = item;
    this.modalQty = 1;
    document.getElementById('modal-item-name').textContent = item.name;
    document.getElementById('modal-item-price').textContent = formatMoney(item.price);
    const desc = document.getElementById('modal-item-desc');
    desc.textContent = item.desc;
    desc.hidden = !item.desc;
    document.getElementById('modal-item-note').value = '';
    document.getElementById('modal-item-img').src = item.image || this.defaultImage;
    this.renderModalQty();
    bootstrap.Modal.getOrCreateInstance(document.getElementById('itemCustomModal')).show();
  }

  renderModalQty() {
    document.getElementById('modal-item-qty').textContent = this.modalQty;
    const total = document.getElementById('modal-item-total');
    if (total && this.modalItem) total.textContent = `· ${formatMoney(this.modalItem.price * this.modalQty)}`;
  }

  // ------------------------------------------------------------------
  // Table activation (session not open yet)
  // ------------------------------------------------------------------
  /** Waiting for the cashier after this device asked to open the table (never sends a request by itself). */
  startActivationFlow() {
    document.getElementById('guest-step')?.classList.add('d-none');
    this.renderActivationWaitingBanner();
    this.startSessionWatch();
  }

  showGuestStep() {
    document.getElementById('recent-session-box')?.classList.add('d-none');
    const step = document.getElementById('guest-step');
    if (!step) return;
    step.classList.remove('d-none');
    step.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  guestCount() {
    return parseInt(document.getElementById('guest-count')?.textContent, 10) || 1;
  }

  adjustGuests(delta) {
    const out = document.getElementById('guest-count');
    if (!out) return;
    const next = Math.min(this.guestLimit, Math.max(1, this.guestCount() + delta));
    out.textContent = next;
    if (delta > 0 && next === this.guestLimit) showToast(`${t('cust_guests_max')} ${this.guestLimit}`, 'info');
  }

  async submitGuestCount() {
    const btn = document.getElementById('btn-guest-submit');
    if (btn) btn.disabled = true;
    const ok = await this.requestTableActivation(true, this.guestCount());
    if (ok) {
      this.startActivationFlow();
    } else if (btn) {
      btn.disabled = false;
    }
  }

  thanksUrl() {
    return `/t/${encodeURIComponent(this.qrToken)}/thanks/${encodeURIComponent(this.sessionToken)}/`;
  }

  /**
   * Reload the table page at most once every 15 seconds. The server decides what to show (menu, or the
   * "table occupied" page for another phone), so a reload can never turn into a loop.
   */
  safeReload() {
    const key = `celvas_reload_${this.qrToken}`;
    let last = 0;
    try {
      last = Number(sessionStorage.getItem(key)) || 0;
    } catch {
      /* private mode: fall through */
    }
    if (Date.now() - last < 15000) {
      this.showLockedNotice();
      return;
    }
    try {
      sessionStorage.setItem(key, String(Date.now()));
    } catch {
      /* ignore */
    }
    window.location.reload();
  }

  /** Another phone owns this table: stop polling and explain instead of retrying. */
  showLockedNotice() {
    this.leaving = true;
    if (this.sessionWatchTimer) clearInterval(this.sessionWatchTimer);
    if (this.ordersTimer) clearInterval(this.ordersTimer);
    const el = document.getElementById('session-activation-status-banner');
    if (el) {
      el.innerHTML = `
        <div class="callout tone-warning mb-3" role="alert">
          <i class="fa-solid fa-lock" aria-hidden="true"></i>
          <div>
            <div class="callout-title">${escapeHtml(t('occupied_title'))}</div>
            <div>${escapeHtml(t('occupied_help'))}</div>
            <div class="d-flex flex-wrap gap-2 mt-2">
              <button type="button" class="btn btn-sm btn-primary" onclick="window.QrScanner && QrScanner.open()"><i class="fa-solid fa-qrcode" aria-hidden="true"></i>${escapeHtml(t('qr_scan_btn'))}</button>
              <a class="btn btn-sm btn-secondary-app" href="/">${escapeHtml(t('btn_back_home'))}</a>
            </div>
          </div>
        </div>`;
      el.scrollIntoView({ block: 'start' });
    }
  }

  /** The table was paid and closed: leave the menu for the thank-you page (no new opening request). */
  goToThanks() {
    if (this.leaving || !this.qrToken || !this.sessionToken) return;
    this.leaving = true;
    localStorage.removeItem(this.storageKey);
    window.location.replace(this.thanksUrl());
  }

  renderActivationWaitingBanner() {
    const el = document.getElementById('session-activation-status-banner');
    if (!el) return;
    el.innerHTML = `
      <div class="callout tone-warning mb-3" role="status">
        <span class="spinner-border spinner-border-sm mt-1" style="color:var(--warning)" aria-hidden="true"></span>
        <div>
          <div class="callout-title">${escapeHtml(t('waiting_cashier_activation'))}</div>
          <div>${escapeHtml(t('waiting_cashier_activation_desc'))}</div>
        </div>
      </div>`;
  }

  async requestTableActivation(userTriggered = false, guests = null) {
    if (!this.qrToken || this.sessionActive || !guests) return false;
    try {
      const res = await apiPost(`/api/v1/public/tables/${encodeURIComponent(this.qrToken)}/request-activation/`, { guest_count: guests });
      if (res.status === 'ALREADY_OPEN' && res.session_token) {
        this.handleSessionOpened();
      } else if (userTriggered) {
        showToast(t('waiting_cashier_activation'), 'info');
      }
      return true;
    } catch (e) {
      if (userTriggered) showToast(e.message, 'error');
      return false;
    }
  }

  startSessionWatch() {
    if (!this.qrToken) return;
    try {
      this.tableWs = new WebSocketClient(`/ws/table/${this.qrToken}/`, (msg) => {
        if (msg.event === 'SESSION_OPENED') this.handleSessionOpened();
      });
    } catch (e) {
      /* polling below covers this */
    }

    if (this.sessionWatchTimer) clearInterval(this.sessionWatchTimer);
    this.sessionWatchTimer = setInterval(async () => {
      if (document.hidden) return;
      try {
        const res = await apiGet(`/api/v1/public/tables/resolve/${encodeURIComponent(this.qrToken)}/`);
        if (res?.session?.has_active_session) this.handleSessionOpened();
      } catch {
        /* keep polling */
      }
    }, 4000);
  }

  handleSessionOpened() {
    if (this.isSessionOpening) return;
    this.isSessionOpening = true;
    if (this.sessionWatchTimer) clearInterval(this.sessionWatchTimer);
    SoundEffects.playSuccess();
    const el = document.getElementById('session-activation-status-banner');
    if (el) {
      el.innerHTML = `
        <div class="callout tone-success mb-3" role="status">
          <i class="fa-solid fa-circle-check" aria-hidden="true"></i>
          <div class="callout-title">${escapeHtml(t('session_opened_celebration'))}</div>
        </div>`;
    }
    setTimeout(() => this.safeReload(), 600);
  }

  // ------------------------------------------------------------------
  // Cart
  // ------------------------------------------------------------------
  loadCart() {
    try {
      const data = JSON.parse(localStorage.getItem(this.storageKey) || '[]');
      return Array.isArray(data) ? data.filter((i) => i && i.id && i.quantity > 0) : [];
    } catch {
      return [];
    }
  }

  saveCart() {
    localStorage.setItem(this.storageKey, JSON.stringify(this.cart));
    this.renderCartUI();
  }

  /** Portions of a dish already in the cart (all notes together). */
  inCart(id) {
    return this.cart.filter((i) => i.id === id).reduce((sum, i) => sum + i.quantity, 0);
  }

  /** How many more portions of this dish may be added: the 50 cap, or today's remaining stock. */
  maxAddable(item) {
    if (!item) return 50;
    const room = 50 - this.inCart(item.id);
    if (item.remaining === null || item.remaining === undefined) return Math.max(1, room);
    return Math.max(1, Math.min(room, item.remaining - this.inCart(item.id)));
  }

  addToCart(item, quantity = 1, note = '') {
    const hasRemaining = item.remaining !== null && item.remaining !== undefined;
    if (hasRemaining) {
      const room = item.remaining - this.inCart(item.id);
      if (room <= 0) {
        showToast(`${t('cust_max_portions')} ${item.remaining}`, 'warning');
        return;
      }
      quantity = Math.min(quantity, room);
    }
    const existing = this.cart.find((i) => i.id === item.id && i.note === note);
    if (existing) {
      existing.quantity = Math.min(50, existing.quantity + quantity);
      if (hasRemaining) existing.remaining = item.remaining;
    } else {
      this.cart.push({
        id: item.id, name: item.name, price: parseFloat(item.price) || 0, quantity, note,
        remaining: hasRemaining ? item.remaining : null,
      });
    }
    this.saveCart();
    showToast(`${t('added_to_cart')} ${item.name}`, 'success');
  }

  updateQuantity(index, delta) {
    const line = this.cart[index];
    if (!line) return;
    if (delta > 0 && line.remaining !== null && line.remaining !== undefined && this.inCart(line.id) >= line.remaining) {
      showToast(`${t('cust_max_portions')} ${line.remaining}`, 'warning');
      return;
    }
    line.quantity += delta;
    if (line.quantity <= 0) {
      this.cart.splice(index, 1);
      showToast(`${t('item_removed_toast')} ${line.name}`, 'info');
    } else if (line.quantity > 50) {
      line.quantity = 50;
    }
    this.saveCart();
  }

  removeFromCart(index) {
    const line = this.cart[index];
    if (!line) return;
    this.cart.splice(index, 1);
    this.saveCart();
    showToast(`${t('item_removed_toast')} ${line.name}`, 'info');
  }

  async clearCartWithConfirm() {
    if (!this.cart.length) return;
    const ok = await confirmDialog({
      title: t('clear_cart'),
      message: t('clear_cart_confirm'),
      confirmText: t('clear_cart'),
      tone: 'danger',
    });
    if (!ok) return;
    this.clearCart();
    showToast(t('cart_cleared'), 'info');
  }

  clearCart() {
    this.cart = [];
    this.saveCart();
  }

  getCartTotal() {
    return this.cart.reduce((sum, i) => sum + i.price * i.quantity, 0);
  }

  getCartCount() {
    return this.cart.reduce((sum, i) => sum + i.quantity, 0);
  }

  renderCartUI() {
    const count = this.getCartCount();
    const total = this.getCartTotal();

    const bar = document.getElementById('floating-cart-bar');
    if (bar) {
      bar.classList.toggle('is-visible', count > 0);
      document.body.classList.toggle('has-cart-bar', count > 0);
      const badge = document.getElementById('cart-count-badge');
      if (badge) badge.textContent = `${count} ${t('items_count')}`;
      const totalEl = document.getElementById('cart-total-price');
      if (totalEl) totalEl.textContent = formatMoney(total);
    }

    const topCount = document.getElementById('top-cart-count');
    if (topCount) {
      topCount.hidden = count === 0;
      topCount.textContent = count;
    }

    const clearBtn = document.getElementById('btn-clear-cart');
    if (clearBtn) clearBtn.hidden = count === 0;

    const drawerTotal = document.getElementById('cart-drawer-total');
    if (drawerTotal) drawerTotal.textContent = formatMoney(total);

    const footer = document.getElementById('cart-footer');
    if (footer) footer.hidden = count === 0;

    const list = document.getElementById('cart-items-list');
    if (!list) return;
    if (!this.cart.length) {
      list.innerHTML = `
        <div class="empty-state">
          <i class="fa-solid fa-basket-shopping" aria-hidden="true"></i>
          <div class="empty-state-title">${escapeHtml(t('cart_empty'))}</div>
        </div>`;
      return;
    }

    list.innerHTML = this.cart
      .map(
        (item, index) => `
        <div class="cart-line">
          <div class="cart-line-info">
            <div class="cart-line-name">${escapeHtml(item.name)}</div>
            ${item.note ? `<div class="cart-line-note"><i class="fa-solid fa-pen fs-8 me-1" aria-hidden="true"></i>${escapeHtml(item.note)}</div>` : ''}
            <div class="fs-7 text-tertiary tabular">${formatMoney(item.price)} × ${item.quantity} = <strong class="text-secondary-app">${formatMoney(item.price * item.quantity)}</strong></div>
          </div>
          <div class="qty-stepper is-sm">
            <button type="button" onclick="customerApp.updateQuantity(${index}, -1)" aria-label="Hamenus"><i class="fa-solid ${item.quantity === 1 ? 'fa-trash-can' : 'fa-minus'}"></i></button>
            <span>${item.quantity}</span>
            <button type="button" onclick="customerApp.updateQuantity(${index}, 1)" aria-label="Aumenta"><i class="fa-solid fa-plus"></i></button>
          </div>
        </div>`
      )
      .join('');
  }

  async submitOrder() {
    if (!this.cart.length) {
      showToast(t('cart_empty_toast'), 'warning');
      return;
    }
    if (!this.sessionToken) {
      showToast(t('session_not_open_toast'), 'error');
      return;
    }

    const btn = document.getElementById('btn-submit-order');
    if (btn.disabled) return;
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>';

    const idempotencyKey = crypto?.randomUUID ? crypto.randomUUID() : `ord_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    const payload = {
      items: this.cart.map((i) => ({ menu_item_id: i.id, quantity: i.quantity, note: i.note })),
      customer_note: document.getElementById('order-customer-note')?.value.trim() || '',
    };

    try {
      const order = await apiPost(`/api/v1/public/sessions/${encodeURIComponent(this.sessionToken)}/orders/`, payload, {
        'Idempotency-Key': idempotencyKey,
      });
      this.clearCart();
      const noteEl = document.getElementById('order-customer-note');
      if (noteEl) noteEl.value = '';
      bootstrap.Offcanvas.getInstance(document.getElementById('cartOffcanvas'))?.hide();
      showToast(`${t('order_sent_toast')} ${order.order_code}. ${t('waiting_cashier_toast')}`, 'success');
      SoundEffects.playSuccess();
      this.loadActiveOrders();
      document.getElementById('customer-active-orders')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  // ------------------------------------------------------------------
  // Orders and bill
  // ------------------------------------------------------------------
  async loadActiveOrders() {
    if (!this.sessionToken) return;
    try {
      const orders = (await apiGet(`/api/v1/public/sessions/${encodeURIComponent(this.sessionToken)}/orders/list/`)) || [];
      const json = JSON.stringify(orders);
      if (json !== this.lastOrdersJson) {
        this.lastOrdersJson = json;
        this.activeOrders = orders;
        this.renderOrdersUI();
        this.checkBillButtonEligibility();
      }
    } catch (e) {
      if (e.code === 'DEVICE_LOCKED') this.safeReload();
      if (e.code === 'TABLE_SESSION_NOT_OPEN') this.goToThanks();
    }
  }

  billEligibility() {
    const orders = this.activeOrders || [];
    const live = orders.filter((o) => o.status !== 'REJECTED' && o.status !== 'CANCELLED');
    if (!live.length) return { ok: false, reason: t('bill_need_order') };
    if (live.some((o) => COOKING_STATUSES.includes(o.status))) return { ok: false, reason: t('bill_still_cooking') };
    if (!live.some((o) => o.status === 'SERVED' || o.status === 'COMPLETED')) return { ok: false, reason: t('bill_need_served') };
    return { ok: true, reason: '' };
  }

  checkBillButtonEligibility() {
    const btn = document.getElementById('btn-request-bill');
    const hint = document.getElementById('bill-hint');
    if (!btn) return;
    if (btn.dataset.status === 'BILL_REQUESTED' || btn.dataset.status === 'PAID') {
      btn.disabled = true;
      if (hint) hint.textContent = btn.dataset.status === 'BILL_REQUESTED' ? t('bill_wait_cashier') : '';
      return;
    }
    const { ok, reason } = this.billEligibility();
    btn.disabled = !ok;
    btn.classList.toggle('btn-primary', ok);
    btn.classList.toggle('btn-secondary-app', !ok);
    if (hint) hint.textContent = reason;
  }

  renderOrdersUI() {
    const el = document.getElementById('customer-active-orders');
    if (!el) return;
    if (!this.activeOrders.length) {
      el.innerHTML = '';
      return;
    }

    const rows = this.activeOrders
      .map((o) => {
        const st = ORDER_STATUS[o.status] || { cls: 'status-served', key: o.status };
        const items = (o.items || []).map((i) => {
          const label = `${i.quantity}× ${escapeHtml(i.menu_name_snapshot)}`;
          if (i.status === 'UNAVAILABLE') return `<s>${label}</s> <span class="badge-soft tone-danger">${escapeHtml(t('cust_item_UNAVAILABLE'))}</span>`;
          if (i.status === 'READY' || i.status === 'COOKING' || i.status === 'SERVED') {
            const tone = i.status === 'COOKING' ? 'tone-accent' : (i.status === 'READY' ? 'tone-success' : '');
            return `${label} <span class="badge-soft ${tone}">${escapeHtml(t(`cust_item_${i.status}`))}</span>`;
          }
          return label;
        }).join('<br>');
        const rejected = o.status === 'REJECTED' && o.rejection_reason
          ? `<div class="fs-7 mt-1" style="color:var(--danger)">${escapeHtml(t('reason'))} ${escapeHtml(o.rejection_reason)}</div>`
          : '';
        return `
          <div class="order-card">
            <div class="d-flex justify-content-between align-items-center gap-2 mb-1">
              <span class="fw-bold">${escapeHtml(o.order_code)}</span>
              <span class="status ${st.cls}">${escapeHtml(t(st.key))}</span>
            </div>
            <div class="fs-7 text-secondary-app">${items}</div>
            ${rejected}
            <div class="d-flex justify-content-between align-items-center mt-2">
              <span class="price">${formatMoney(o.grand_total)}</span>
              <a class="btn btn-sm btn-secondary-app" href="/t/${encodeURIComponent(this.qrToken)}/order/${encodeURIComponent(o.order_code)}/">${escapeHtml(t('btn_track_order'))}</a>
            </div>
          </div>`;
      })
      .join('');

    el.innerHTML = `
      <section class="surface surface-pad mb-3" aria-labelledby="orders-title">
        <div class="section-head">
          <h2 class="section-title" id="orders-title">${escapeHtml(t('order_status_title'))} <span class="count">${this.activeOrders.length}</span></h2>
        </div>
        ${rows}
      </section>`;
  }

  /** Re-read today's availability so sold-out flags and "Left n" badges follow the kitchen. */
  async refreshMenu() {
    try {
      const categories = (await apiGet('/api/v1/public/menu/')) || [];
      const byId = {};
      categories.forEach((c) => (c.items || []).forEach((i) => { byId[i.id] = i; }));
      document.querySelectorAll('.menu-card[data-item-id]').forEach((card) => {
        const info = byId[card.dataset.itemId];
        if (!info) return;
        const remaining = info.remaining === null || info.remaining === undefined ? '' : String(info.remaining);
        card.dataset.available = info.orderable ? '1' : '0';
        card.dataset.remaining = remaining;
        card.classList.toggle('is-unavailable', !info.orderable);
        if (info.orderable) card.removeAttribute('aria-disabled'); else card.setAttribute('aria-disabled', 'true');
        const media = card.querySelector('.menu-card-media');
        let flag = card.querySelector('[data-role="flag"]');
        if (!info.orderable || remaining !== '') {
          if (!flag) {
            flag = document.createElement('span');
            flag.dataset.role = 'flag';
            media.appendChild(flag);
          }
          flag.className = `menu-card-flag ${info.orderable ? 'tone-warning' : 'tone-danger'}`;
          flag.textContent = info.orderable ? `${t('cust_stock_left')} ${remaining}` : t('sold_out');
        } else if (flag) {
          flag.remove();
        }
      });
      // keep the cart within what is left
      this.cart.forEach((line) => {
        const info = byId[line.id];
        if (info) line.remaining = info.remaining === undefined ? null : info.remaining;
      });
    } catch (e) {
      console.error('menu refresh', e);
    }
  }

  async requestBill() {
    const { ok, reason } = this.billEligibility();
    if (!ok) {
      showToast(reason, 'warning');
      return;
    }
    const btn = document.getElementById('btn-request-bill');
    btn.disabled = true;
    try {
      const res = await apiPost(`/api/v1/public/sessions/${encodeURIComponent(this.sessionToken)}/request-bill/`, {});
      showToast(res.message || t('bill_sent_toast'), 'success');
      SoundEffects.playBell();
      btn.dataset.status = 'BILL_REQUESTED';
      btn.innerHTML = `<i class="fa-solid fa-clock" aria-hidden="true"></i><span>${escapeHtml(t('bill_requested'))}</span>`;
      btn.classList.remove('btn-primary');
      btn.classList.add('btn-secondary-app');
      this.checkBillButtonEligibility();
    } catch (e) {
      showToast(e.message, 'error');
      this.checkBillButtonEligibility();
    }
  }

  initWebSocket() {
    if (!this.sessionToken) return;
    this.ws = new WebSocketClient(`/ws/customer/${this.sessionToken}/`, (msg) => {
      const code = msg.data?.order_code || '';
      if (msg.event === 'ORDER_CONFIRMED') {
        showToast(`${t('order_confirmed_toast')} ${code}`, 'success');
        SoundEffects.playSuccess();
      } else if (msg.event === 'ORDER_REJECTED') {
        showToast(`${t('order_rejected_toast')} ${code}: ${msg.data?.reason || ''}`, 'error');
      } else if (msg.event === 'ITEM_STATUS_CHANGED') {
        const names = (msg.data?.items || []).map((i) => i.name).join(', ');
        if (msg.data?.status === 'UNAVAILABLE') {
          showToast(`${t('cust_item_unavailable_toast')} ${names}`, 'warning');
        } else if (msg.data?.status === 'READY') {
          showToast(`${t('cust_item_ready_toast')} ${names}`, 'info');
          SoundEffects.playBell();
        }
        this.refreshMenu();
      } else if (['ORDER_PREPARING', 'ORDER_READY', 'ORDER_SERVED'].includes(msg.event)) {
        showToast(`${t('order_status_toast')} ${code}`, 'info');
      } else if (msg.event === 'SESSION_CLOSED') {
        showToast(t('session_closed_toast'), 'info');
        setTimeout(() => this.goToThanks(), 1500);
        return;
      } else if (msg.event === 'PAYMENT_COMPLETED') {
        showToast(t('cust_paid_toast'), 'success');
      }
      this.loadActiveOrders();
    });

    this.ordersTimer = setInterval(() => {
      if (!document.hidden && !this.leaving) this.loadActiveOrders();
    }, 10000);
    setInterval(() => {
      if (!document.hidden && !this.leaving) this.refreshMenu();
    }, 30000);
  }
}
