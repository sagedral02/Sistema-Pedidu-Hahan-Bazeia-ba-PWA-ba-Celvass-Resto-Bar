/**
 * Riwayat tab of the reports page: order and transaction lists with server-side filters, sorting and
 * pagination, plus a detail panel (timeline, items, payments) with receipt printing.
 *
 * Scope rules are enforced by the server. The browser only shows what it receives and escapes every value.
 */
(function () {
  'use strict';

  const Page = window.ReportsPage;
  if (!Page) return;

  const { cfg, esc, tr, lang, el } = Page;
  const $ = (id) => document.getElementById(id);
  const ui = {
    kindButtons: Array.from(document.querySelectorAll('[data-kind]')),
    status: $('rpt-h-status'),
    source: $('rpt-h-source'),
    order_type: $('rpt-h-type'),
    cashier: $('rpt-h-cashier'),
    search: $('rpt-h-search'),
    sourceWrap: $('rpt-h-source-wrap'),
    kpi: $('rpt-h-kpi'),
    head: $('rpt-h-head'),
    body: $('rpt-h-body'),
    pager: $('rpt-h-pager'),
    pageInfo: $('rpt-h-pageinfo'),
    prev: $('rpt-h-prev'),
    next: $('rpt-h-next'),
    detail: $('rptDetail'),
    detailBody: $('rpt-detail-body'),
    detailTitle: $('rptDetailTitle'),
  };
  if (!ui.body || !ui.detail) return;

  const PAGE_SIZE = 20;
  const ORDER_STATUSES = ['WAITING_CASHIER_CONFIRMATION', 'CONFIRMED', 'PREPARING', 'READY', 'SERVED', 'COMPLETED', 'REJECTED', 'CANCELLED'];
  const PAYMENT_STATUSES = ['COMPLETED', 'VOIDED'];
  const SOURCES = ['CUSTOMER', 'CASHIER'];
  const ORDER_TYPES = ['DINE_IN', 'TAKEAWAY'];

  const state = {
    kind: 'orders',
    page: 1,
    sort: { orders: '-time', transactions: '-time' },
    requestId: 0,
    detailRequestId: 0,
    openDetail: null,
    receipt: null,
  };

  // ---------------------------------------------------------------- filters

  function fillSelect(select, allKey, values, labelOf) {
    if (!select) return;
    const previous = select.value;
    select.innerHTML =
      '<option value="">' + esc(tr(allKey)) + '</option>' +
      values.map((v) => '<option value="' + esc(v) + '">' + esc(labelOf(v)) + '</option>').join('');
    if (values.includes(previous)) select.value = previous;
  }

  function buildSelects() {
    const statuses = state.kind === 'orders'
      ? ORDER_STATUSES.filter((s) => cfg.isAdmin || s !== 'WAITING_CASHIER_CONFIRMATION')
      : PAYMENT_STATUSES;
    const prefix = state.kind === 'orders' ? 'rpt_status_' : 'rpt_pstatus_';
    fillSelect(ui.status, 'rpt_all_status', statuses, (s) => tr(prefix + s));
    fillSelect(ui.source, 'rpt_all_sources', SOURCES, (s) => tr('rpt_source_' + s));
    fillSelect(ui.order_type, 'rpt_all_order_types', ORDER_TYPES, (m) => tr('rpt_ot_' + m));
    ui.sourceWrap.hidden = state.kind !== 'orders';
  }

  function filterParams() {
    const params = new URLSearchParams();
    const add = (key, value) => value && params.set(key, value);
    add('status', ui.status.value);
    if (state.kind === 'orders') add('source', ui.source.value);
    add('order_type', ui.order_type.value);
    if (ui.cashier) add('cashier', ui.cashier.value);
    add('q', ui.search.value.trim());
    return params;
  }

  function listParams() {
    const params = Page.periodParams();
    filterParams().forEach((value, key) => params.set(key, value));
    params.set('sort', state.sort[state.kind]);
    return params;
  }

  function updateDownloads(enabled) {
    const params = listParams();
    params.set('type', state.kind + '_history');
    Page.setDownloads(enabled ? Page.exportUrl(params, 'csv') : null, enabled ? Page.exportUrl(params, 'pdf') : null);
  }

  function setKind(kind) {
    state.kind = kind === 'transactions' ? 'transactions' : 'orders';
    ui.kindButtons.forEach((btn) => {
      const active = btn.dataset.kind === state.kind;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    buildSelects();
  }

  function resetFilters() {
    ['status', 'source', 'order_type', 'cashier'].forEach((key) => {
      if (ui[key]) ui[key].value = '';
    });
    ui.search.value = '';
  }

  // ---------------------------------------------------------------- list

  function cellHtml(col, row) {
    const value = row[col.key];
    if (col.key === 'status') return Page.renderBadge(value);
    return esc(value);
  }

  function renderHead(columns) {
    const sort = state.sort[state.kind];
    const key = sort.replace(/^-/, '');
    const desc = sort.startsWith('-');
    ui.head.innerHTML =
      '<tr>' +
      columns
        .map((col) => {
          const cls = col.align === 'end' ? ' class="text-end"' : '';
          if (!col.sortable) return '<th' + cls + '>' + esc(col.label) + '</th>';
          const active = col.key === key;
          const icon = active ? (desc ? 'fa-sort-down' : 'fa-sort-up') : 'fa-sort';
          return '<th' + cls + ' aria-sort="' + (active ? (desc ? 'descending' : 'ascending') : 'none') + '">' +
            '<button type="button" class="th-sort' + (active ? ' is-active' : '') + '" data-sort="' + esc(col.key) + '">' +
            esc(col.label) + ' <i class="fa-solid ' + icon + '" aria-hidden="true"></i></button></th>';
        })
        .join('') +
      '</tr>';
  }

  function renderRows(data) {
    if (!data.rows.length) {
      ui.body.innerHTML = Page.emptyState('fa-solid fa-inbox', 'rpt_empty_filtered', data.columns.length);
      return;
    }
    ui.body.innerHTML = data.rows
      .map((row) => {
        const cells = data.columns
          .map((col) => {
            if (col.key === 'code') return '<td class="cell-primary fw-semibold">' + cellHtml(col, row) + '</td>';
            const cls = col.align === 'end' ? 'text-end tabular' : '';
            return '<td class="' + cls + '" data-label="' + esc(col.label) + '">' + cellHtml(col, row) + '</td>';
          })
          .join('');
        return '<tr class="row-link" tabindex="0" role="button" data-id="' + esc(row.id) + '">' + cells + '</tr>';
      })
      .join('');
  }

  function renderPager(data) {
    ui.pager.hidden = data.total === 0;
    ui.pageInfo.textContent =
      tr('rpt_page_label') + ' ' + data.page + ' ' + tr('rpt_page_of') + ' ' + data.pages + ' \u00b7 ' + data.total + ' ' + tr('rpt_rows_count');
    ui.prev.disabled = data.page <= 1;
    ui.next.disabled = data.page >= data.pages;
    state.page = data.page;
  }

  function render(data) {
    Page.el.period.textContent = data.period_label;
    Page.el.scope.hidden = !data.scope_own;
    ui.kpi.innerHTML = data.kpi
      .map((item) => '<div class="kpi"><div class="kpi-label">' + esc(item.label) + '</div><div class="kpi-value">' + esc(item.value) + '</div></div>')
      .join('');
    renderHead(data.columns);
    renderRows(data);
    renderPager(data);
  }

  async function load() {
    const current = ++state.requestId;
    Page.clearError();
    updateDownloads(false);
    ui.kpi.innerHTML = '';
    ui.pager.hidden = true;
    ui.head.innerHTML = '';
    ui.body.innerHTML = Page.emptyState('fa-solid fa-spinner fa-spin', 'rpt_loading', 6);
    ui.body.setAttribute('aria-busy', 'true');

    const params = listParams();
    params.set('page', String(state.page));
    params.set('page_size', String(PAGE_SIZE));

    try {
      const response = await fetch(cfg.historyUrls[state.kind] + '?' + params.toString(), {
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
      });
      const payload = await response.json();
      if (current !== state.requestId) return;
      ui.body.setAttribute('aria-busy', 'false');
      if (!response.ok) {
        ui.body.innerHTML = '';
        Page.showError(payload && payload.error && payload.error.message);
        return;
      }
      render(payload);
      updateDownloads(true);
    } catch (e) {
      if (current !== state.requestId) return;
      ui.body.setAttribute('aria-busy', 'false');
      ui.body.innerHTML = '';
      Page.showError();
    }
  }

  function reloadFromFirstPage() {
    state.page = 1;
    load();
  }

  // ---------------------------------------------------------------- detail panel

  function row(labelKey, valueHtml) {
    if (valueHtml === '' || valueHtml === null || valueHtml === undefined) return '';
    return '<div class="rpt-dl-row"><dt>' + esc(tr(labelKey)) + '</dt><dd>' + valueHtml + '</dd></div>';
  }

  function actorName(entry) {
    return entry.actor || tr('rpt_actor_' + entry.actor_type);
  }

  function orderDetailHtml(d) {
    const items = d.items
      .map(
        (i) => '<tr><td>' + (i.status_code === 'UNAVAILABLE' ? '<s>' + esc(i.name) + '</s>' : esc(i.name)) +
          (i.status ? ' <span class="badge-soft' + (i.status_code === 'UNAVAILABLE' ? ' tone-danger' : (i.status_code === 'SERVED' ? ' tone-success' : '')) + '">' + esc(i.status) + '</span>' : '') +
          (i.unavailable_reason ? '<div class="fs-8 text-tertiary">' + esc(i.unavailable_reason) + '</div>' : '') +
          (i.note ? '<div class="fs-8 text-tertiary">' + esc(i.note) + '</div>' : '') +
          '</td><td class="text-end tabular">' + esc(i.quantity) + '</td><td class="text-end tabular">' + esc(i.subtotal) + '</td></tr>'
      )
      .join('');
    const timeline = d.timeline
      .map(
        (e) => '<li><span class="rpt-tl-dot" aria-hidden="true"></span><div class="rpt-tl-body">' +
          '<div>' + (e.from ? Page.renderBadge(e.from) + ' <i class="fa-solid fa-arrow-right fs-8 text-tertiary" aria-hidden="true"></i> ' : '') + Page.renderBadge(e.to) + '</div>' +
          '<div class="fs-8 text-tertiary">' + esc(e.at) + ' \u00b7 ' + esc(actorName(e)) + '</div>' +
          (e.reason ? '<div class="fs-7">' + esc(e.reason) + '</div>' : '') + '</div></li>'
      )
      .join('');
    const payments = d.payments.length
      ? d.payments
          .map(
            (p) => '<li class="rpt-pay"><div><strong>' + esc(p.code) + '</strong> ' + Page.renderBadge(p.status) +
              '<div class="fs-8 text-tertiary">' + esc(p.method) + ' \u00b7 ' + esc(p.paid_at) + '</div></div><strong class="tabular">' + esc(p.amount) + '</strong></li>'
          )
          .join('')
      : '<li class="text-tertiary fs-7">' + esc(tr('rpt_d_no_payments')) + '</li>';

    return (
      '<div class="d-flex flex-wrap align-items-center gap-2 mb-3"><h3 class="fs-5 fw-bold mb-0">' + esc(d.code) + '</h3>' + Page.renderBadge(d.status) + '</div>' +
      '<dl class="rpt-dl">' +
      row('rpt_d_table', esc(d.table)) +
      row('rpt_d_session', esc(d.session_code)) +
      row('rpt_filter_source', esc(d.source)) +
      row('rpt_d_created', esc(d.created_at)) +
      row('rpt_d_confirmed', esc(d.confirmed_at)) +
      row('rpt_d_staff', esc(d.confirmed_by || d.created_by)) +
      row('rpt_d_note', esc(d.customer_note)) +
      row('rpt_d_reason', esc(d.rejection_reason || d.cancellation_reason)) +
      '</dl>' +
      '<h4 class="section-title mt-4 mb-2">' + esc(tr('rpt_d_items')) + '</h4>' +
      '<div class="table-wrap"><table class="table table-app mb-0"><tbody>' + items + '</tbody></table></div>' +
      '<dl class="rpt-dl rpt-totals mt-3">' +
      row('rpt_d_subtotal', esc(d.money.subtotal)) +
      row('rpt_d_discount', esc(d.money.discount)) +
      row('rpt_d_tax', esc(d.money.tax)) +
      '<div class="rpt-dl-row rpt-total"><dt>' + esc(tr('rpt_d_total')) + '</dt><dd>' + esc(d.money.total) + '</dd></div></dl>' +
      '<h4 class="section-title mt-4 mb-2">' + esc(tr('rpt_d_timeline')) + '</h4>' +
      '<ol class="rpt-timeline">' + timeline + '</ol>' +
      '<h4 class="section-title mt-4 mb-2">' + esc(tr('rpt_d_payments')) + '</h4>' +
      '<ul class="rpt-pay-list">' + payments + '</ul>'
    );
  }

  function transactionDetailHtml(d) {
    const receiptAction = d.receipt
      ? '<button type="button" class="btn btn-primary mt-3" id="rpt-print-receipt"><i class="fa-solid fa-print" aria-hidden="true"></i><span>' + esc(tr('rpt_btn_print_receipt')) + '</span></button>'
      : '<p class="text-tertiary fs-7 mt-3 mb-0">' + esc(tr('rpt_d_no_receipt')) + '</p>';
    return (
      '<div class="d-flex flex-wrap align-items-center gap-2 mb-3"><h3 class="fs-5 fw-bold mb-0">' + esc(d.code) + '</h3>' + Page.renderBadge(d.status) + '</div>' +
      '<dl class="rpt-dl">' +
      row('rpt_d_table', esc(d.table)) +
      row('rpt_d_session', esc(d.session_code)) +
      row('rpt_filter_method', esc(d.method)) +
      row('rpt_filter_cashier', esc(d.cashier)) +
      row('rpt_d_paid_at', esc(d.paid_at)) +
      row('rpt_d_tendered', esc(d.tendered)) +
      row('rpt_d_change', esc(d.change)) +
      row('rpt_d_void_reason', esc(d.void_reason)) +
      row('rpt_d_voided_by', esc(d.voided_by)) +
      row('rpt_d_voided_at', esc(d.voided_at)) +
      '<div class="rpt-dl-row rpt-total"><dt>' + esc(tr('rpt_d_total')) + '</dt><dd>' + esc(d.amount) + '</dd></div>' +
      '</dl>' + receiptAction
    );
  }

  /**
   * Show the offcanvas without tripping over Bootstrap's transition guard: show() is ignored while a
   * hide is still running, so wait for `hidden.bs.offcanvas` instead of calling it blindly.
   */
  function showDetailPanel() {
    const instance = bootstrap.Offcanvas.getOrCreateInstance(ui.detail);
    if (instance._isShown) return;
    if (instance._isTransitioning) {
      ui.detail.addEventListener('hidden.bs.offcanvas', () => instance.show(), { once: true });
      return;
    }
    instance.show();
  }

  async function openDetail(kind, id, options) {
    const opts = Object.assign({ show: true }, options || {});
    const current = ++state.detailRequestId;
    state.openDetail = { kind, id };
    state.receipt = null;
    ui.detailTitle.textContent = tr(kind === 'orders' ? 'rpt_detail_order' : 'rpt_detail_transaction');
    ui.detailBody.innerHTML = '<div class="empty-state"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i><div>' + esc(tr('rpt_loading')) + '</div></div>';
    if (opts.show) showDetailPanel();

    try {
      const response = await fetch(cfg.historyUrls[kind] + encodeURIComponent(id) + '/?lang=' + encodeURIComponent(lang()), {
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
      });
      const payload = await response.json();
      if (current !== state.detailRequestId) return;
      if (!response.ok) {
        ui.detailBody.innerHTML = '<div class="alert alert-danger mb-0" role="alert">' + esc((payload.error && payload.error.message) || tr('rpt_detail_error')) + '</div>';
        return;
      }
      state.receipt = payload.receipt || null;
      ui.detailBody.innerHTML = kind === 'orders' ? orderDetailHtml(payload) : transactionDetailHtml(payload);
    } catch (e) {
      if (current !== state.detailRequestId) return;
      ui.detailBody.innerHTML = '<div class="alert alert-danger mb-0" role="alert">' + esc(tr('rpt_detail_error')) + '</div>';
    }
  }

  ui.detail.addEventListener('hidden.bs.offcanvas', () => {
    state.openDetail = null;
    state.receipt = null;
    state.detailRequestId++;
  });

  ui.detailBody.addEventListener('click', (event) => {
    if (!event.target.closest('#rpt-print-receipt')) return;
    if (state.receipt && window.Receipt) window.Receipt.print(state.receipt);
  });

  // ---------------------------------------------------------------- events

  function rowFromEvent(event) {
    return event.target.closest('tr[data-id]');
  }

  ui.body.addEventListener('click', (event) => {
    const tr_ = rowFromEvent(event);
    if (tr_) openDetail(state.kind, tr_.dataset.id);
  });
  ui.body.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const tr_ = rowFromEvent(event);
    if (!tr_) return;
    event.preventDefault();
    openDetail(state.kind, tr_.dataset.id);
  });

  ui.head.addEventListener('click', (event) => {
    const button = event.target.closest('[data-sort]');
    if (!button) return;
    const key = button.dataset.sort;
    const current = state.sort[state.kind];
    state.sort[state.kind] = current === key ? '-' + key : key;
    reloadFromFirstPage();
  });

  ui.kindButtons.forEach((btn) =>
    btn.addEventListener('click', () => {
      if (btn.dataset.kind !== state.kind) Page.navigate('#history/' + btn.dataset.kind);
    })
  );

  [ui.status, ui.source, ui.order_type, ui.cashier].forEach((select) => select && select.addEventListener('change', reloadFromFirstPage));

  let searchTimer = null;
  ui.search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(reloadFromFirstPage, 300);
  });

  ui.prev.addEventListener('click', () => {
    if (state.page > 1) {
      state.page -= 1;
      load();
    }
  });
  ui.next.addEventListener('click', () => {
    state.page += 1;
    load();
  });

  window.addEventListener('languageChanged', () => {
    buildSelects();
    if (state.openDetail) openDetail(state.openDetail.kind, state.openDetail.id, { show: false });
  });

  // ---------------------------------------------------------------- tab contract

  Page.registerTab('history', {
    get lastKind() {
      return state.kind;
    },

    enter({ parts, query }) {
      const kind = parts[0] === 'transactions' || parts[0] === 'orders' ? parts[0] : state.kind;
      const kindChanged = kind !== state.kind;
      setKind(kind);
      if (kindChanged || query.toString()) resetFilters();

      ['status', 'source', 'order_type', 'cashier'].forEach((key) => {
        const value = query.get(key);
        if (value && ui[key]) ui[key].value = value;
      });
      if (query.get('q')) ui.search.value = query.get('q');

      Page.setHash('#history/' + state.kind);
      state.page = 1;
      load();
    },

    load,
  });
})();
