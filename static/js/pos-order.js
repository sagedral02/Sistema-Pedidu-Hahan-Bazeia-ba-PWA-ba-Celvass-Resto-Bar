/**
 * Cashier order screen: catalog (category chips, search, sold-out and "Left n" badges), cart and destination
 * (take away with an optional customer name, or a table). Orders go straight to the kitchen.
 * Server values are escaped before they reach the DOM; prices shown here are for display only.
 */
class PosOrder {
  constructor(app) {
    this.app = app;
    this.menu = [];
    this.category = 'all';
    this.cart = [];
    this.destination = 'TAKEAWAY';
    this.idempotencyKey = null;
    this.busy = false;

    document.getElementById('pos-order-search')?.addEventListener('input', () => this.renderGrid());
    window.addEventListener('languageChanged', () => {
      this.renderChips();
      this.renderGrid();
      this.renderCart();
    });
  }

  modal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('posOrderModal'));
  }

  /** options: { destination: 'TAKEAWAY' | 'TABLE', tableId } */
  async open(options = {}) {
    this.cart = [];
    this.category = 'all';
    this.idempotencyKey = newKey('pos');
    document.getElementById('pos-order-search').value = '';
    document.getElementById('pos-order-customer').value = '';
    document.getElementById('pos-order-note').value = '';
    this.fillTables(options.tableId);
    this.setDestination(options.destination || 'TAKEAWAY');
    this.showPane('menu');
    this.renderCart();
    this.modal().show();
    await this.loadMenu();
  }

  async loadMenu() {
    try {
      this.menu = (await apiGet('/api/v1/cashier/menu/')) || [];
    } catch (e) {
      showToast(e.message, 'error');
      this.menu = [];
    }
    this.renderChips();
    this.renderGrid();
    this.renderCart();
  }

  items() {
    return this.menu.flatMap((c) => (c.items || []).map((i) => ({ ...i, category_slug: c.slug, category_name: c.name })));
  }

  findItem(id) {
    return this.items().find((i) => String(i.id) === String(id));
  }

  /** Only tables with an open session can receive an order (the cashier opens the table first). */
  orderableTables() {
    return (this.app.tables || []).filter((tb) => tb.active_session
      && (tb.active_session.status === 'OPEN' || tb.active_session.status === 'BILL_REQUESTED'));
  }

  fillTables(selectedId) {
    const select = document.getElementById('pos-order-table');
    const tables = this.orderableTables();
    select.innerHTML = `<option value="">${escapeHtml(t('csh_order_choose_table'))}</option>` + tables.map((tb) =>
      `<option value="${escapeHtml(tb.id)}"${String(tb.id) === String(selectedId) ? ' selected' : ''}>${escapeHtml(tb.display_name)}</option>`
    ).join('');
    document.getElementById('pos-order-no-tables').hidden = tables.length > 0;
  }

  /** Phones and tablets show the catalog or the cart (desktop shows both side by side). */
  showPane(pane) {
    const body = document.getElementById('pos-order-body');
    if (!body) return;
    body.dataset.pane = pane === 'cart' ? 'cart' : 'menu';
    [['pos-tab-menu', 'menu'], ['pos-tab-cart', 'cart']].forEach(([id, name]) => {
      const tab = document.getElementById(id);
      if (!tab) return;
      const active = body.dataset.pane === name;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    const scroller = body.closest('.modal-body');
    if (scroller) scroller.scrollTop = 0;
  }

  setDestination(dest) {
    this.destination = dest;
    document.getElementById('pos-dest-takeaway').classList.toggle('is-active', dest === 'TAKEAWAY');
    document.getElementById('pos-dest-table').classList.toggle('is-active', dest === 'TABLE');
    document.getElementById('pos-dest-takeaway-fields').hidden = dest !== 'TAKEAWAY';
    document.getElementById('pos-dest-table-fields').hidden = dest !== 'TABLE';
  }

  renderChips() {
    const el = document.getElementById('pos-order-chips');
    if (!el) return;
    const chip = (slug, label) => `<button type="button" class="chip${this.category === slug ? ' active' : ''}" onclick="posOrder.setCategory('${escapeHtml(slug)}')">${escapeHtml(label)}</button>`;
    el.innerHTML = chip('all', t('csh_order_all')) + this.menu.map((c) => chip(c.slug, c.name)).join('');
  }

  setCategory(slug) {
    this.category = slug;
    this.renderChips();
    this.renderGrid();
  }

  inCart(id) {
    return this.cart.filter((l) => String(l.id) === String(id)).reduce((sum, l) => sum + l.quantity, 0);
  }

  room(item) {
    const cap = 99 - this.inCart(item.id);
    if (item.remaining === null || item.remaining === undefined) return cap;
    return Math.min(cap, item.remaining - this.inCart(item.id));
  }

  renderGrid() {
    const el = document.getElementById('pos-order-grid');
    if (!el) return;
    const q = (document.getElementById('pos-order-search')?.value || '').toLowerCase().trim();
    const list = this.items().filter((i) => (this.category === 'all' || i.category_slug === this.category)
      && (!q || i.name.toLowerCase().includes(q)));
    if (!list.length) {
      el.innerHTML = emptyState('fa-magnifying-glass', t('csh_stock_empty'));
      return;
    }
    el.innerHTML = list.map((i) => {
      const off = !i.orderable;
      const flag = off
        ? `<span class="badge-soft tone-danger">${escapeHtml(t('sold_out'))}</span>`
        : (i.remaining !== null && i.remaining !== undefined ? `<span class="badge-soft tone-warning">${escapeHtml(t('csh_order_left'))} ${escapeHtml(i.remaining)}</span>` : '');
      const qty = this.inCart(i.id);
      // One dish per row (single column list): image, name + badges, price, add button.
      const img = i.display_image ? `<img class="pos-row-thumb" src="${escapeHtml(i.display_image)}" alt="" loading="lazy" onerror="this.remove()">` : '<span class="pos-row-thumb" aria-hidden="true"></span>';
      return `
        <button type="button" class="pos-row${off ? ' is-off' : ''}" ${off ? 'disabled aria-disabled="true"' : ''} onclick="posOrder.add('${escapeHtml(i.id)}')">
          ${img}
          <span class="pos-row-main">
            <span class="pos-row-name">${escapeHtml(i.name)}</span>
            <span class="pos-row-meta">${flag}${qty ? `<span class="badge-soft tone-accent">${escapeHtml(qty)}× ${escapeHtml(t('csh_in_cart'))}</span>` : ''}</span>
          </span>
          <span class="price pos-row-price">${formatMoney(i.price)}</span>
          <span class="pos-row-add" aria-hidden="true"><i class="fa-solid fa-plus"></i></span>
        </button>`;
    }).join('');
  }

  add(id) {
    const item = this.findItem(id);
    if (!item || !item.orderable) return;
    if (this.room(item) <= 0) {
      showToast(`${t('cust_max_portions')} ${item.remaining}`, 'warning');
      return;
    }
    const line = this.cart.find((l) => String(l.id) === String(id));
    if (line) line.quantity += 1;
    else this.cart.push({ id: item.id, name: item.name, price: parseFloat(item.price) || 0, quantity: 1, note: '' });
    this.renderCart();
    this.renderGrid();
  }

  change(index, delta) {
    const line = this.cart[index];
    if (!line) return;
    if (delta > 0) {
      const item = this.findItem(line.id);
      if (item && this.room(item) <= 0) {
        showToast(`${t('cust_max_portions')} ${item.remaining}`, 'warning');
        return;
      }
    }
    line.quantity += delta;
    if (line.quantity <= 0) this.cart.splice(index, 1);
    this.renderCart();
    this.renderGrid();
  }

  setNote(index, value) {
    if (this.cart[index]) this.cart[index].note = value.slice(0, 500);
  }

  renderCart() {
    const el = document.getElementById('pos-order-lines');
    if (!el) return;
    const total = this.cart.reduce((sum, l) => sum + l.price * l.quantity, 0);
    const qty = this.cart.reduce((sum, l) => sum + l.quantity, 0);
    document.getElementById('pos-order-total').textContent = formatMoney(total);
    const setText = (id, value) => {
      const node = document.getElementById(id);
      if (node) node.textContent = value;
    };
    setText('pos-order-count', qty);
    setText('pos-order-footer-count', qty);
    setText('pos-order-footer-sum', formatMoney(total));
    document.getElementById('btn-pos-order-submit').disabled = !this.cart.length || this.busy;
    if (!this.cart.length) {
      el.innerHTML = `<div class="fs-7 text-tertiary py-2">${escapeHtml(t('csh_order_cart_empty'))}</div>`;
      return;
    }
    el.innerHTML = this.cart.map((l, idx) => `
      <div class="cart-line">
        <div class="cart-line-info">
          <div class="cart-line-name">${escapeHtml(l.name)}</div>
          <input type="text" class="form-control form-control-sm mt-1" maxlength="500" placeholder="${escapeHtml(t('csh_order_note'))}" aria-label="${escapeHtml(t('csh_order_note'))}" value="${escapeHtml(l.note)}" oninput="posOrder.setNote(${idx}, this.value)">
        </div>
        <div class="d-flex align-items-center gap-1">
          <button type="button" class="btn btn-sm btn-ghost btn-icon" onclick="posOrder.change(${idx}, -1)" aria-label="-"><i class="fa-solid fa-minus"></i></button>
          <span class="tabular fw-bold">${escapeHtml(l.quantity)}</span>
          <button type="button" class="btn btn-sm btn-ghost btn-icon" onclick="posOrder.change(${idx}, 1)" aria-label="+"><i class="fa-solid fa-plus"></i></button>
        </div>
        <span class="price">${formatMoney(l.price * l.quantity)}</span>
      </div>`).join('');
  }

  async submit() {
    if (this.busy) return;
    if (!this.cart.length) {
      showToast(t('csh_order_need_items'), 'warning');
      return;
    }
    const body = {
      destination: this.destination,
      customer_note: document.getElementById('pos-order-note').value.trim(),
      items: this.cart.map((l) => ({ menu_item_id: l.id, quantity: l.quantity, note: l.note.trim() })),
    };
    if (this.destination === 'TABLE') {
      const tableId = document.getElementById('pos-order-table').value;
      if (!tableId) {
        showToast(t('csh_order_need_table'), 'warning');
        return;
      }
      body.table_id = tableId;
    } else {
      body.customer_name = document.getElementById('pos-order-customer').value.trim();
    }

    this.busy = true;
    const btn = document.getElementById('btn-pos-order-submit');
    btn.disabled = true;
    try {
      const order = await apiPost('/api/v1/cashier/orders/create/', body, { 'Idempotency-Key': this.idempotencyKey });
      this.modal().hide();
      SoundEffects.playSuccess();
      showToast(`${t('csh_order_sent_toast')} ${order.table_name} (${order.order_code})`, 'success');
      this.app.loadTables();
      this.app.loadTakeaways();
      if (order.is_takeaway) showPanel('pos-panel', 'takeaway');
    } catch (e) {
      showToast(e.message, 'error');
      this.idempotencyKey = newKey('pos'); // the order was refused: the next try is a new request
      this.loadMenu();
    } finally {
      this.busy = false;
      this.renderCart();
    }
  }
}
