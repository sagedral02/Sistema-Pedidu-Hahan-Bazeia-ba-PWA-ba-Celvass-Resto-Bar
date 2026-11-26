/**
 * Audit tab of the reports page (admin only): who changed what and when, with server-side filters,
 * sorting and pagination, plus a detail panel that shows the before / after values of an event.
 *
 * Secrets are masked by the server. Every value is inserted with textContent or escaped before display.
 */
(function () {
  'use strict';

  const Page = window.ReportsPage;
  if (!Page) return;

  const { cfg, esc, tr, lang } = Page;
  const $ = (id) => document.getElementById(id);
  const ui = {
    entity: $('rpt-a-entity'),
    search: $('rpt-a-search'),
    kpi: $('rpt-a-kpi'),
    head: $('rpt-a-head'),
    body: $('rpt-a-body'),
    pager: $('rpt-a-pager'),
    pageInfo: $('rpt-a-pageinfo'),
    prev: $('rpt-a-prev'),
    next: $('rpt-a-next'),
    detail: $('rptDetail'),
    detailBody: $('rpt-detail-body'),
    detailTitle: $('rptDetailTitle'),
  };
  if (!ui.body || !ui.detail || !cfg.auditUrl) return;

  const PAGE_SIZE = 20;
  const state = { page: 1, sort: '-time', requestId: 0, detailRequestId: 0, openId: null };

  // ---------------------------------------------------------------- list

  function params() {
    const p = Page.periodParams();
    if (ui.entity.value) p.set('entity', ui.entity.value);
    const q = ui.search.value.trim();
    if (q) p.set('q', q);
    p.set('sort', state.sort);
    return p;
  }

  /** Keep the entity dropdown in step with the server's list while preserving the current choice. */
  function fillEntities(types) {
    const current = ui.entity.value;
    ui.entity.replaceChildren();
    const all = document.createElement('option');
    all.value = '';
    all.textContent = tr('rpt_all_entities');
    ui.entity.append(all);
    types.forEach((type) => {
      const option = document.createElement('option');
      option.value = type;
      option.textContent = type;
      ui.entity.append(option);
    });
    ui.entity.value = types.includes(current) ? current : '';
  }

  function renderHead(columns) {
    const key = state.sort.replace(/^-/, '');
    const desc = state.sort.startsWith('-');
    ui.head.innerHTML =
      '<tr>' +
      columns
        .map((col) => {
          const active = col.key === key;
          const icon = active ? (desc ? 'fa-sort-down' : 'fa-sort-up') : 'fa-sort';
          return '<th aria-sort="' + (active ? (desc ? 'descending' : 'ascending') : 'none') + '">' +
            '<button type="button" class="th-sort' + (active ? ' is-active' : '') + '" data-sort="' + esc(col.key) + '">' +
            esc(col.label) + ' <i class="fa-solid ' + icon + '" aria-hidden="true"></i></button></th>';
        })
        .join('') +
      '</tr>';
  }

  function renderRows(data) {
    if (!data.rows.length) {
      ui.body.innerHTML = Page.emptyState('fa-solid fa-clipboard-list', 'rpt_empty_filtered', data.columns.length);
      return;
    }
    ui.body.innerHTML = data.rows
      .map((row) => {
        const cells = data.columns
          .map((col) => {
            if (col.key === 'action') {
              return '<td class="cell-primary"><span class="badge-soft tone-accent">' + esc(row.action) + '</span></td>';
            }
            return '<td data-label="' + esc(col.label) + '">' + esc(row[col.key]) + '</td>';
          })
          .join('');
        return '<tr class="row-link" tabindex="0" role="button" data-id="' + esc(row.id) + '">' + cells + '</tr>';
      })
      .join('');
  }

  function updateDownloads(enabled) {
    const p = params();
    p.set('type', 'audit');
    Page.setDownloads(enabled ? Page.exportUrl(p, 'csv') : null, enabled ? Page.exportUrl(p, 'pdf') : null);
  }

  function render(data) {
    updateDownloads(true);
    Page.el.period.textContent = data.period_label;
    Page.el.scope.hidden = true;
    ui.kpi.innerHTML = data.kpi
      .map((item) => '<div class="kpi"><div class="kpi-label">' + esc(item.label) + '</div><div class="kpi-value">' + esc(item.value) + '</div></div>')
      .join('');
    fillEntities(data.entity_types || []);
    renderHead(data.columns);
    renderRows(data);

    ui.pager.hidden = data.total === 0;
    ui.pageInfo.textContent =
      tr('rpt_page_label') + ' ' + data.page + ' ' + tr('rpt_page_of') + ' ' + data.pages + ' \u00b7 ' + data.total + ' ' + tr('rpt_rows_count');
    ui.prev.disabled = data.page <= 1;
    ui.next.disabled = data.page >= data.pages;
    state.page = data.page;
  }

  async function load() {
    const current = ++state.requestId;
    Page.clearError();
    ui.kpi.innerHTML = '';
    ui.pager.hidden = true;
    ui.head.innerHTML = '';
    ui.body.innerHTML = Page.emptyState('fa-solid fa-spinner fa-spin', 'rpt_loading', 4);
    ui.body.setAttribute('aria-busy', 'true');
    updateDownloads(false);

    const query = params();
    query.set('page', String(state.page));
    query.set('page_size', String(PAGE_SIZE));
    try {
      const response = await fetch(cfg.auditUrl + '?' + query.toString(), {
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

  // ---------------------------------------------------------------- detail

  function row(labelKey, text) {
    if (!text) return '';
    return '<div class="rpt-dl-row"><dt>' + esc(tr(labelKey)) + '</dt><dd>' + esc(text) + '</dd></div>';
  }

  function value(text) {
    return text === null || text === undefined
      ? '<span class="text-tertiary">\u2014</span>'
      : '<code class="audit-value">' + esc(text) + '</code>';
  }

  function detailHtml(d) {
    const changes = d.changes.length
      ? '<div class="table-wrap"><div class="table-responsive"><table class="table table-app mb-0"><thead><tr><th>' +
        esc(tr('rpt_d_field')) + '</th><th>' + esc(tr('rpt_d_before')) + '</th><th>' + esc(tr('rpt_d_after')) + '</th></tr></thead><tbody>' +
        d.changes
          .map((c) => '<tr><td class="fw-semibold">' + esc(c.key) + '</td><td>' + value(c.before) + '</td><td>' + value(c.after) + '</td></tr>')
          .join('') +
        '</tbody></table></div></div>'
      : '<p class="text-tertiary fs-7 mb-0">' + esc(tr('rpt_audit_no_changes')) + '</p>';

    return (
      '<div class="d-flex flex-wrap align-items-center gap-2 mb-3"><span class="badge-soft tone-accent">' + esc(d.action) + '</span></div>' +
      '<dl class="rpt-dl">' +
      row('th_time', d.created_at) +
      row('rpt_d_actor', d.actor) +
      row('rpt_d_role', d.actor_role) +
      row('rpt_d_entity', d.entity_type) +
      row('rpt_d_entity_id', d.entity_id) +
      row('rpt_d_request', d.request_id) +
      row('rpt_d_ip', d.ip) +
      row('rpt_d_agent', d.user_agent) +
      '</dl>' +
      '<h4 class="section-title mt-4 mb-2">' + esc(tr('rpt_d_changes')) + '</h4>' + changes
    );
  }

  async function openDetail(id, options) {
    const opts = Object.assign({ show: true }, options || {});
    const current = ++state.detailRequestId;
    state.openId = id;
    ui.detailTitle.textContent = tr('rpt_detail_audit');
    ui.detailBody.innerHTML = '<div class="empty-state"><i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i><div>' + esc(tr('rpt_loading')) + '</div></div>';
    if (opts.show) Page.showOffcanvas(ui.detail);

    try {
      const response = await fetch(cfg.auditUrl + encodeURIComponent(id) + '/?lang=' + encodeURIComponent(lang()), {
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
      });
      const payload = await response.json();
      if (current !== state.detailRequestId) return;
      if (!response.ok) {
        ui.detailBody.innerHTML = '<div class="alert alert-danger mb-0" role="alert">' + esc((payload.error && payload.error.message) || tr('rpt_detail_error')) + '</div>';
        return;
      }
      ui.detailBody.innerHTML = detailHtml(payload);
    } catch (e) {
      if (current !== state.detailRequestId) return;
      ui.detailBody.innerHTML = '<div class="alert alert-danger mb-0" role="alert">' + esc(tr('rpt_detail_error')) + '</div>';
    }
  }

  ui.detail.addEventListener('hidden.bs.offcanvas', () => {
    state.openId = null;
    state.detailRequestId++;
  });

  // ---------------------------------------------------------------- events

  const rowOf = (event) => event.target.closest('tr[data-id]');

  ui.body.addEventListener('click', (event) => {
    const tr_ = rowOf(event);
    if (tr_) openDetail(tr_.dataset.id);
  });
  ui.body.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const tr_ = rowOf(event);
    if (!tr_) return;
    event.preventDefault();
    openDetail(tr_.dataset.id);
  });

  ui.head.addEventListener('click', (event) => {
    const button = event.target.closest('[data-sort]');
    if (!button) return;
    const key = button.dataset.sort;
    state.sort = state.sort === key ? '-' + key : key;
    reloadFromFirstPage();
  });

  ui.entity.addEventListener('change', reloadFromFirstPage);

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
    if (state.openId) openDetail(state.openId, { show: false });
  });

  // ---------------------------------------------------------------- tab contract

  Page.registerTab('audit', {
    enter({ query }) {
      if (query.get('q')) ui.search.value = query.get('q');
      if (query.get('entity')) ui.entity.value = query.get('entity');
      Page.setHash('#audit');
      state.page = 1;
      load();
    },
    load,
  });
})();
