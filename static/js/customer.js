/**
 * Celvass Resto & Bar — Customer Dine-In PWA Controller
 * Pure Tetun Base Interface & Dynamic Multilingual System
 * Luxury Dark Glassmorphism & Device-Locked Table Security
 */

class CustomerApp {
  constructor(config) {
    this.sessionToken = config.sessionToken;
    this.qrToken = config.qrToken;
    this.tableCode = config.tableCode;
    this.tableName = config.tableName;
    this.deviceId = this.getOrCreateDeviceId();
    this.storageKey = `resto_cart_${this.sessionToken || 'default'}`;
    this.cart = this.loadCart();
    this.selectedItem = null;
    this.activeOrders = [];
    this.lastOrdersJson = null;
    this.sessionWatchTimer = null;
    this.isSessionOpening = false;

    this.init();
  }

  getOrCreateDeviceId() {
    let id = localStorage.getItem('celvass_device_id');
    if (!id) {
      id = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : ('dev_' + Math.random().toString(36).substring(2) + Date.now().toString(36));
      localStorage.setItem('celvass_device_id', id);
    }
    // Also store in cookie for server-side verification
    document.cookie = `celvass_device_id=${id}; path=/; max-age=31536000; SameSite=Lax`;
    return id;
  }

  init() {
    // If table has no active session, initiate automatic cashier activation verification
    if (!this.sessionToken && this.qrToken) {
      this.startActivationFlow();
    }

    // Ensure mobile table badge in dock has the table code
    const mobileTableBadge = document.getElementById('dock-table-name-mobile');
    if (mobileTableBadge) {
      if (this.tableCode) {
        mobileTableBadge.textContent = this.tableCode;
      }
    }

    this.renderCartUI();
    this.initWebSocket();
    this.loadActiveOrders();

    // Listen for language switch
    window.addEventListener('languageChanged', () => {
      this.renderCartUI();
      this.renderOrdersUI();
      this.checkBillButtonEligibility();
    });


    // Category Filter
    document.querySelectorAll('.cat-pill').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.cat-pill').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');

        const cat = String(btn.dataset.category || btn.getAttribute('data-category') || '').trim();
        document.querySelectorAll('.menu-item-col').forEach((card) => {
          const cardCat = String(card.dataset.category || card.getAttribute('data-category') || '').trim();
          if (cat === 'all' || cardCat === cat) {
            card.style.display = 'block';
          } else {
            card.style.display = 'none';
          }
        });
      });
    });

    // Search bar
    const searchInput = document.getElementById('menu-search');
    const dockSearchInput = document.getElementById('dock-search-input');

    const applySearch = (q) => {
      document.querySelectorAll('.menu-item-col').forEach((card) => {
        const name = (card.dataset.name || '').toLowerCase();
        const desc = (card.dataset.desc || '').toLowerCase();
        if (!q || name.includes(q) || desc.includes(q)) {
          card.style.display = 'block';
        } else {
          card.style.display = 'none';
        }
      });
    };

    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        const q = e.target.value.toLowerCase().trim();
        if (dockSearchInput && dockSearchInput.value !== e.target.value) {
          dockSearchInput.value = e.target.value;
        }
        applySearch(q);
      });
    }

    if (dockSearchInput) {
      dockSearchInput.addEventListener('input', (e) => {
        const q = e.target.value.toLowerCase().trim();
        if (searchInput && searchInput.value !== e.target.value) {
          searchInput.value = e.target.value;
        }
        applySearch(q);
        const grid = document.getElementById('menu-grid');
        if (grid && window.scrollY < 200) {
          grid.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      });
      dockSearchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          closeDockSearch();
        }
      });
    }
  }

  startActivationFlow() {
    this.renderActivationWaitingBanner();
    this.requestTableActivation(false);
    this.startSessionWatch();
  }

  renderActivationWaitingBanner() {
    const bannerEl = document.getElementById('session-activation-status-banner');
    if (!bannerEl) return;
    bannerEl.innerHTML = `
      <div class="glass-card p-3 mb-3 border-warning border-opacity-60 bg-dark bg-opacity-70 text-center shadow-lg animate__animated animate__fadeIn">
        <div class="d-flex align-items-center justify-content-center gap-2 mb-2">
          <div class="spinner-grow spinner-grow-sm text-warning" role="status"></div>
          <h6 class="fw-bold mb-0 text-warning" data-i18n="waiting_cashier_activation">${t('waiting_cashier_activation')}</h6>
        </div>
        <p class="small text-white-70 mb-0" data-i18n="waiting_cashier_activation_desc">
          ${t('waiting_cashier_activation_desc')}
        </p>
      </div>
    `;
  }

  async requestTableActivation(userTriggered = false) {
    if (!this.qrToken) return;
    try {
      const res = await apiPost(
        `/api/v1/public/tables/${this.qrToken}/request-activation/`,
        { device_token: this.deviceId, guest_count: 2 },
        { 'X-Device-Token': this.deviceId }
      );
      if (res.status === 'ALREADY_OPEN' && res.session_token) {
        this.handleSessionOpened({ public_token: res.session_token });
      } else if (userTriggered) {
        showToast(t('waiting_cashier_activation'), 'info');
      }
    } catch (e) {
      console.warn('Activation request error:', e);
    }
  }

  startSessionWatch() {
    if (!this.qrToken) return;

    // 1. WebSocket for instant push on table QR group
    try {
      this.tableWs = new WebSocketClient(`/ws/table/${this.qrToken}/`, (msg) => {
        if (msg.event === 'SESSION_OPENED') {
          this.handleSessionOpened(msg.data);
        }
      });
    } catch (e) {
      console.warn('Table WS connection failed, falling back to polling:', e);
    }

    // 2. High-frequency polling (every 2.5s) to guarantee instant auto-unlock on all devices
    if (this.sessionWatchTimer) clearInterval(this.sessionWatchTimer);
    this.sessionWatchTimer = setInterval(async () => {
      if (document.hidden) return;
      try {
        const res = await apiGet(`/api/v1/public/tables/resolve/${this.qrToken}/`);
        if (res && res.session && res.session.has_active_session && res.session.public_token) {
          clearInterval(this.sessionWatchTimer);
          this.handleSessionOpened(res.session);
        }
      } catch (err) {
        // Silent catch on poll error
      }
    }, 2500);
  }

  handleSessionOpened(sessionData) {
    if (this.isSessionOpening) return;
    this.isSessionOpening = true;
    if (this.sessionWatchTimer) clearInterval(this.sessionWatchTimer);

    SoundEffects.playSuccess();
    showToast(t('session_opened_celebration'), 'success');

    const bannerEl = document.getElementById('session-activation-status-banner');
    if (bannerEl) {
      bannerEl.innerHTML = `
        <div class="glass-card p-3 border-success border-opacity-75 bg-success bg-opacity-20 text-center shadow-lg animate__animated animate__zoomIn mb-3">
          <div class="d-flex align-items-center justify-content-center gap-2 mb-1">
            <i class="fa-solid fa-circle-check fs-4 text-success"></i>
            <h6 class="fw-bold mb-0 text-white">${t('session_opened_celebration')}</h6>
          </div>
          <small class="text-white-50">Loke hela pájina pedidu...</small>
        </div>
      `;
    }

    // Smoothly reload page so template renders with full active session & unlocked buttons
    setTimeout(() => {
      window.location.reload();
    }, 350);
  }

  loadCart() {

    try {
      const data = localStorage.getItem(this.storageKey);
      return data ? JSON.parse(data) : [];
    } catch {
      return [];
    }
  }

  saveCart() {
    localStorage.setItem(this.storageKey, JSON.stringify(this.cart));
    this.renderCartUI();
  }

  addToCart(item, quantity = 1, note = '') {
    const existing = this.cart.find((i) => i.id === item.id && i.note === note);
    if (existing) {
      existing.quantity += quantity;
    } else {
      this.cart.push({
        id: item.id,
        name: item.name,
        price: parseFloat(item.price),
        image: item.image,
        quantity: quantity,
        note: note,
      });
    }
    this.saveCart();
    showToast(`${t('added_to_cart')} ${item.name}`, 'success');
  }

  updateQuantity(index, delta) {
    if (this.cart[index]) {
      this.cart[index].quantity += delta;
      if (this.cart[index].quantity <= 0) {
        const removedName = this.cart[index].name;
        this.cart.splice(index, 1);
        showToast(`${t('item_removed_toast') || 'Item hasai tiha ona'}: ${removedName}`, 'info');
      }
      this.saveCart();
    }
  }

  removeFromCart(index) {
    if (this.cart[index] !== undefined) {
      const removedName = this.cart[index].name;
      this.cart.splice(index, 1);
      this.saveCart();
      if (typeof SoundEffects !== 'undefined' && SoundEffects.playClick) {
        SoundEffects.playClick();
      }
      showToast(`${t('item_removed_toast') || 'Item hasai tiha ona'}: ${removedName}`, 'info');
    }
  }

  clearCartWithConfirm() {
    if (this.cart.length === 0) return;
    const confirmMsg = t('clear_cart_confirm') || 'Tebes atu hamos karreta pedidu tomak?';
    if (confirm(confirmMsg)) {
      this.clearCart();
      if (typeof SoundEffects !== 'undefined' && SoundEffects.playClick) {
        SoundEffects.playClick();
      }
      showToast(t('cart_cleared') || 'Karreta mamuk ona.', 'info');
    }
  }

  clearCart() {
    this.cart = [];
    this.saveCart();
  }

  getCartTotal() {
    return this.cart.reduce((sum, item) => sum + item.price * item.quantity, 0);
  }

  getCartCount() {
    return this.cart.reduce((sum, item) => sum + item.quantity, 0);
  }

  renderCartUI() {
    const count = this.getCartCount();
    const total = this.getCartTotal();

    const bar = document.getElementById('floating-cart-bar');
    const badge = document.getElementById('cart-count-badge');
    const totalEl = document.getElementById('cart-total-price');
    const dockBadge = document.getElementById('dock-cart-badge');
    const topDockBadge = document.getElementById('top-dock-cart-badge');
    const clearCartBtn = document.getElementById('btn-clear-cart');

    if (clearCartBtn) {
      clearCartBtn.style.display = count > 0 ? 'inline-flex' : 'none';
    }

    if (bar && count > 0) {
      bar.style.display = 'flex';
      if (badge) badge.textContent = `${count} ${count > 1 ? 'Itens' : 'Item'}`;
      if (totalEl) totalEl.textContent = `$${total.toFixed(2)}`;
    } else if (bar) {
      bar.style.display = 'none';
    }

    if (dockBadge) {
      if (count > 0) {
        dockBadge.textContent = count;
        dockBadge.style.display = 'block';
      } else {
        dockBadge.style.display = 'none';
      }
    }

    if (topDockBadge) {
      if (count > 0) {
        topDockBadge.textContent = count;
        topDockBadge.style.display = 'inline-block';
      } else {
        topDockBadge.style.display = 'none';
      }
    }

    const drawerList = document.getElementById('cart-items-list');
    const drawerTotal = document.getElementById('cart-drawer-total');
    if (drawerTotal) drawerTotal.textContent = `$${total.toFixed(2)}`;

    if (drawerList) {
      if (this.cart.length === 0) {
        drawerList.innerHTML = `
          <div class="text-center py-5 text-white-50">
            <i class="fa-solid fa-basket-shopping fa-3x mb-3 text-warning opacity-50"></i>
            <p class="mb-0 fw-semibold">${t('cart_empty')}</p>
          </div>
        `;
      } else {
        drawerList.innerHTML = this.cart
          .map(
            (item, index) => `
          <div class="d-flex align-items-center justify-content-between py-2.5 border-bottom border-secondary border-opacity-25 gap-2 animate__animated animate__fadeIn">
            <div class="pe-1 flex-grow-1 min-w-0">
              <div class="fw-bold text-white fs-6 text-truncate">${item.name}</div>
              <div class="text-warning fw-bold small">
                $${(item.price * item.quantity).toFixed(2)} 
                <span class="text-white-50 small fw-normal">($${item.price.toFixed(2)}/item)</span>
              </div>
              ${item.note ? `<div class="small text-warning bg-warning bg-opacity-10 border border-warning border-opacity-25 rounded-pill px-2 py-0 d-inline-block mt-0.5" style="font-size:0.75rem;"><i class="fa-solid fa-pen small me-1"></i>${item.note}</div>` : ''}
            </div>
            <div class="d-flex align-items-center gap-1.5 flex-shrink-0">
              <button class="btn btn-sm btn-outline-light rounded-circle d-flex align-items-center justify-content-center" style="width:30px; height:30px; padding:0;" onclick="customerApp.updateQuantity(${index}, -1)" title="Minus">
                <i class="fa-solid fa-minus small"></i>
              </button>
              <span class="fw-bold text-white px-1" style="min-width:22px; text-align:center;">${item.quantity}</span>
              <button class="btn btn-sm btn-outline-warning rounded-circle d-flex align-items-center justify-content-center" style="width:30px; height:30px; padding:0;" onclick="customerApp.updateQuantity(${index}, 1)" title="Plus">
                <i class="fa-solid fa-plus small"></i>
              </button>
              <button class="btn btn-sm btn-outline-danger border-opacity-50 text-danger rounded-circle d-flex align-items-center justify-content-center ms-1" style="width:30px; height:30px; padding:0;" onclick="customerApp.removeFromCart(${index})" title="${t('delete')}">
                <i class="fa-solid fa-trash-can small"></i>
              </button>
            </div>
          </div>
        `
          )
          .join('');
      }
    }
  }

  async submitOrder() {
    if (this.cart.length === 0) {
      showToast(t('cart_empty_toast'), 'warning');
      return;
    }
    if (!this.sessionToken) {
      showToast(t('session_not_open_toast'), 'error');
      return;
    }

    const customerNote = document.getElementById('order-customer-note')?.value || '';
    const idempotencyKey = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : ('ord_' + Date.now());

    const payload = {
      items: this.cart.map((item) => ({
        menu_item_id: item.id,
        quantity: item.quantity,
        note: item.note,
      })),
      customer_note: customerNote,
    };

    const submitBtn = document.getElementById('btn-submit-order');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-2"></i> ...';
    }

    try {
      const order = await apiPost(`/api/v1/public/sessions/${this.sessionToken}/orders/`, payload, {
        'Idempotency-Key': idempotencyKey,
        'X-Device-Token': this.deviceId,
      });

      this.clearCart();
      const cartModal = bootstrap.Offcanvas.getInstance(document.getElementById('cartOffcanvas'));
      if (cartModal) cartModal.hide();

      showToast(`${t('order_sent_toast')} ${order.order_code}. ${t('waiting_cashier_toast')}`, 'success');
      SoundEffects.playSuccess();

      this.loadActiveOrders();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = `<i class="fa-solid fa-paper-plane me-1"></i> ${t('btn_submit_order')}`;
      }
    }
  }

  async loadActiveOrders() {
    if (!this.sessionToken) return;

    try {
      const orders = await apiGet(`/api/v1/public/sessions/${this.sessionToken}/orders/list/`, {
        'X-Device-Token': this.deviceId,
      });
      const newOrders = orders || [];
      const newJson = JSON.stringify(newOrders);
      if (this.lastOrdersJson !== newJson) {
        this.lastOrdersJson = newJson;
        this.activeOrders = newOrders;
        this.renderOrdersUI();
        this.checkBillButtonEligibility();
      }
    } catch (e) {
      // Session closed or inactive
    }
  }

  checkBillButtonEligibility() {
    const btn = document.getElementById('btn-request-bill');
    if (!btn) return;

    // If button already shows requested or paid, preserve
    if (btn.dataset.status === 'BILL_REQUESTED') {
      btn.disabled = true;
      btn.className = 'btn btn-sm btn-secondary text-white fw-bold rounded-pill px-3 shadow-sm';
      btn.innerHTML = `<i class="fa-solid fa-clock me-1"></i> ${t('bill_requested')}`;
      btn.removeAttribute('title');
      return;
    }

    if (btn.dataset.status === 'PAID') {
      btn.disabled = true;
      btn.className = 'btn btn-sm btn-success text-white fw-bold rounded-pill px-3 shadow-sm';
      btn.innerHTML = `<i class="fa-solid fa-circle-check me-1"></i> ${t('paid_status')}`;
      btn.removeAttribute('title');
      return;
    }

    const hasAnyOrder = this.activeOrders && this.activeOrders.length > 0;
    const hasCookingOrder = hasAnyOrder && this.activeOrders.some((o) =>
      ['WAITING_CASHIER_CONFIRMATION', 'CONFIRMED', 'PREPARING'].includes(o.status)
    );
    const hasServedOrder = hasAnyOrder && this.activeOrders.some((o) =>
      o.status === 'SERVED' || o.status === 'COMPLETED'
    );

    if (!hasAnyOrder) {
      btn.disabled = true;
      btn.dataset.eligible = 'false';
      btn.className = 'btn btn-sm btn-outline-secondary text-white-50 opacity-60 rounded-pill px-3 shadow-sm';
      btn.title = t('bill_need_order') || 'Favor halo pedidu uluk molok husu konta.';
      btn.innerHTML = `<i class="fa-solid fa-receipt me-1"></i> ${t('request_bill')}`;
    } else if (hasCookingOrder) {
      btn.disabled = true;
      btn.dataset.eligible = 'cooking';
      btn.className = 'btn btn-sm btn-outline-warning text-warning opacity-75 rounded-pill px-3 shadow-sm';
      btn.title = t('bill_still_cooking') || 'Pedidu sei tein hela iha dapur.';
      btn.innerHTML = `<i class="fa-solid fa-utensils me-1"></i> ${t('request_bill')}`;
    } else if (!hasServedOrder) {
      btn.disabled = true;
      btn.dataset.eligible = 'not_served';
      btn.className = 'btn btn-sm btn-outline-secondary text-white-50 opacity-60 rounded-pill px-3 shadow-sm';
      btn.title = t('bill_need_served') || 'Hein hahán entrega ba meza ona foin bele husu konta.';
      btn.innerHTML = `<i class="fa-solid fa-receipt me-1"></i> ${t('request_bill')}`;
    } else {
      // Eligible: at least one order served and no orders cooking
      btn.disabled = false;
      btn.dataset.eligible = 'true';
      btn.className = 'btn btn-sm btn-warning text-dark fw-bold rounded-pill px-3 shadow-sm pulse-gold';
      btn.title = t('request_bill') || 'Husu Konta';
      btn.innerHTML = `<i class="fa-solid fa-receipt me-1"></i> ${t('request_bill')}`;
    }
  }

  renderOrdersUI() {
    const listEl = document.getElementById('customer-active-orders');
    if (!listEl) return;

    if (this.activeOrders.length === 0) {
      listEl.innerHTML = '';
      return;
    }

    listEl.innerHTML = `
      <div class="glass-card p-3 mb-3 border-warning border-opacity-40 shadow-lg">
        <h6 class="fw-bold mb-3 text-warning d-flex align-items-center justify-content-between" style="font-family: 'Outfit', sans-serif;">
          <span><i class="fa-solid fa-clock-rotate-left me-2"></i> ${t('order_status_title')}</span>
          <span class="badge bg-warning text-dark fw-bold rounded-pill px-2.5 py-1 shadow-sm">${this.activeOrders.length} ${t('total_orders')}</span>
        </h6>
        ${this.activeOrders
          .map((ord) => {
            const statusClass = {
              WAITING_CASHIER_CONFIRMATION: 'status-waiting',
              CONFIRMED: 'status-confirmed',
              PREPARING: 'status-preparing',
              READY: 'status-ready',
              SERVED: 'status-served',
              COMPLETED: 'status-completed',
              REJECTED: 'status-rejected',
            }[ord.status] || 'bg-secondary text-white';

            const statusText = {
              WAITING_CASHIER_CONFIRMATION: t('status_waiting'),
              CONFIRMED: t('status_confirmed'),
              PREPARING: t('status_preparing'),
              READY: t('status_ready'),
              SERVED: t('status_served'),
              COMPLETED: t('status_completed'),
              REJECTED: `${t('status_rejected')}: ${ord.rejection_reason || ''}`,
            }[ord.status] || ord.status;

            return `
            <div class="p-3 border border-secondary border-opacity-25 rounded-3 mb-2 bg-dark bg-opacity-40">
              <div class="d-flex flex-wrap justify-content-between align-items-center gap-1 mb-2">
                <span class="fw-bold text-white fs-6 text-truncate" style="max-width: 140px;">${ord.order_code}</span>
                <span class="badge badge-status ${statusClass} text-wrap" style="font-size: 0.72rem; padding: 0.32rem 0.55rem; max-width: 100%; white-space: normal;">${statusText}</span>
              </div>
              <div class="small text-white-50 mb-2">
                ${ord.items.map((i) => `<span class="text-white">${i.quantity}x</span> ${i.menu_name_snapshot}`).join(', ')}
              </div>
              <div class="d-flex justify-content-between align-items-center small fw-bold pt-2 border-top border-secondary border-opacity-25">
                <span class="text-warning fs-6">$${ord.grand_total}</span>
                <a href="/t/${this.qrToken}/order/${ord.order_code}/" class="btn btn-sm btn-outline-warning rounded-pill px-3 py-1">
                  ${t('btn_view_order')}
                </a>
              </div>
            </div>
          `;
          })
          .join('')}
      </div>
    `;
  }

  async requestBill() {
    if (!this.sessionToken) {
      showToast(t('session_not_open_toast') || 'Sesi meza seidauk loke.', 'warning');
      return;
    }

    if (!this.activeOrders || this.activeOrders.length === 0) {
      showToast(t('bill_need_order') || 'Favor halo pedidu uluk molok husu konta.', 'warning');
      return;
    }

    const hasCookingOrder = this.activeOrders.some((o) =>
      ['WAITING_CASHIER_CONFIRMATION', 'CONFIRMED', 'PREPARING'].includes(o.status)
    );
    if (hasCookingOrder) {
      showToast(t('bill_still_cooking') || 'Pedidu sei tein hela iha dapur.', 'warning');
      return;
    }

    const hasServedOrder = this.activeOrders.some((o) => o.status === 'SERVED' || o.status === 'COMPLETED');
    if (!hasServedOrder) {
      showToast(t('bill_need_served') || "Hein hahán entrega ba meza ona foin bele husu konta.", 'warning');
      return;
    }

    const btn = document.getElementById('btn-request-bill');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-1"></i> ...';
    }

    try {
      const res = await apiPost(
        `/api/v1/public/sessions/${this.sessionToken}/request-bill/`,
        {},
        { 'X-Device-Token': this.deviceId }
      );
      const msg = res.message || t('bill_sent_toast');
      showToast(msg, 'info');
      SoundEffects.playBell();
      if (btn) {
        btn.disabled = true;
        btn.dataset.status = 'BILL_REQUESTED';
        btn.className = 'btn btn-sm btn-secondary text-white fw-bold rounded-pill px-3 shadow-sm';
        btn.innerHTML = `<i class="fa-solid fa-clock me-1"></i> ${t('bill_requested')}`;
      }
    } catch (e) {
      showToast(e.message, 'error');
      this.checkBillButtonEligibility();
    }
  }

  initWebSocket() {
    if (!this.sessionToken) return;

    this.ws = new WebSocketClient(`/ws/customer/${this.sessionToken}/`, (msg) => {
      if (msg.event === 'ORDER_CONFIRMED') {
        showToast(`${t('order_confirmed_toast')} ${msg.data.order_code}!`, 'success');
        SoundEffects.playSuccess();
        this.loadActiveOrders();
      } else if (msg.event === 'ORDER_REJECTED') {
        showToast(`${t('order_rejected_toast')} ${msg.data.order_code}: ${msg.data.reason}`, 'error');
        this.loadActiveOrders();
      } else if (msg.event === 'ORDER_PREPARING' || msg.event === 'ORDER_READY' || msg.event === 'ORDER_SERVED') {
        showToast(`${t('order_status_toast')} ${msg.data.order_code}`, 'info');
        this.loadActiveOrders();
      } else if (msg.event === 'SESSION_CLOSED') {
        showToast(t('session_closed_toast'), 'info');
        setTimeout(() => window.location.reload(), 2000);
      }
    });

    // Fallback polling every 10 seconds, only if document is visible
    setInterval(() => {
      if (!document.hidden) {
        this.loadActiveOrders();
      }
    }, 10000);
  }

  scrollToActiveOrders() {
    const el = document.getElementById('customer-active-orders');
    if (el && el.children.length > 0) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
      showToast(t('alert_waiting_desc') || "Seidauk iha pedidu ativu ba sesi meza ne'e.", 'info');
    }
  }
}

/**
 * Global Navigation & Dock Inline Search Controllers
 */
function scrollToMenuCatalog() {
  const dockMenuBtn = document.getElementById('dock-btn-menu');
  if (dockMenuBtn) {
    document.querySelectorAll('.flying-glass-dock .dock-item-btn').forEach(b => b.classList.remove('active'));
    dockMenuBtn.classList.add('active');
  }
  const el = document.getElementById('menu-grid');
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function scrollToActiveOrders() {
  const dockStatusBtn = document.getElementById('dock-btn-status');
  if (dockStatusBtn) {
    document.querySelectorAll('.flying-glass-dock .dock-item-btn').forEach(b => b.classList.remove('active'));
    dockStatusBtn.classList.add('active');
  }
  const el = document.getElementById('customer-active-orders');
  if (el && el.children.length > 0) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } else {
    showToast(t('alert_waiting_desc') || "Seidauk iha pedidu ativu ba sesi meza ne'e.", 'info');
  }
}

function toggleDockSearch() {
  const normalItems = document.getElementById('dock-normal-items');
  const searchBox = document.getElementById('dock-search-box');
  const input = document.getElementById('dock-search-input');
  if (normalItems && searchBox) {
    normalItems.style.setProperty('display', 'none', 'important');
    searchBox.style.setProperty('display', 'flex', 'important');
    if (input) {
      input.focus();
    }
  }
}

function closeDockSearch() {
  const normalItems = document.getElementById('dock-normal-items');
  const searchBox = document.getElementById('dock-search-box');
  const input = document.getElementById('dock-search-input');
  const menuSearch = document.getElementById('menu-search');
  if (normalItems && searchBox) {
    searchBox.style.setProperty('display', 'none', 'important');
    normalItems.style.removeProperty('display');
    if (input) {
      input.value = '';
    }
    if (menuSearch) {
      menuSearch.value = '';
    }
    document.querySelectorAll('.menu-item-col').forEach((card) => {
      card.style.display = 'block';
    });
  }
}

/**
 * Filter menu by category slug
 */
function filterByCategory(slug) {
  const targetSlug = String(slug || 'all').trim();
  document.querySelectorAll('.cat-pill').forEach((btn) => {
    const btnCat = String(btn.dataset.category || btn.getAttribute('data-category') || '').trim();
    if (btnCat === targetSlug) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });

  const items = document.querySelectorAll('.menu-item-col');
  let visibleCount = 0;
  items.forEach((col) => {
    const colCat = String(col.dataset.category || col.getAttribute('data-category') || '').trim();
    if (targetSlug === 'all' || colCat === targetSlug) {
      col.style.display = 'block';
      visibleCount++;
    } else {
      col.style.display = 'none';
    }
  });

  const activePill = document.querySelector(`.cat-pill[data-category="${slug}"]`);
  if (activePill) {
    activePill.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
  }
}
