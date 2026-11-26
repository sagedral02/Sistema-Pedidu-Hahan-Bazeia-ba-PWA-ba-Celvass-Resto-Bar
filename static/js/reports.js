/**
 * Reports page shell: tabs (Ringkasan | Riwayat), shared period filter, downloads, hash routing,
 * and the Ringkasan (summary) tab. The Riwayat tab lives in reports-history.js and plugs in through
 * ReportsPage.registerTab().
 *
 * Labels inside a report (columns, summary, values) are translated by the server for the chosen
 * language; the page chrome is translated by i18n.js.
 *
 * Hash routes:  #summary  |  #history/orders  |  #history/transactions
 *               optional query part, e.g. #history/transactions?start=2026-11-01&end=2026-11-07&method=CASH
 */
(function () {
  'use strict';

  const cfg = window.CELVASS_REPORTS || {};
  const $ = (id) => document.getElementById(id);
  const el = {
    form: $('rpt-form'),
    type: $('rpt-type'),
    chips: Array.from(document.querySelectorAll('[data-preset]')),
    custom: $('rpt-custom'),
    start: $('rpt-start'),
    end: $('rpt-end'),
    error: $('rpt-error'),
    period: $('rpt-period'),
    scope: $('rpt-scope'),
    summary: $('rpt-summary'),
    head: $('rpt-head'),
    body: $('rpt-body'),
    hint: $('rpt-drill-hint'),
    csv: $('rpt-download-csv'),
    pdf: $('rpt-download-pdf'),
    tabs: Array.from(document.querySelectorAll('[data-tab]')),
    summaryPane: $('rpt-pane-summary'),
    historyPane: $('rpt-pane-history'),
    summaryControls: $('rpt-summary-controls'),
    historyControls: $('rpt-history-controls'),
    auditPane: $('rpt-pane-audit'), // admin only: null for cashiers
    auditControls: $('rpt-audit-controls'),
    downloads: $('rpt-downloads'),
  };
  if (!el.form || !el.body) return;

  const esc = (value) => (typeof window.escapeHtml === 'function' ? window.escapeHtml(value) : String(value));
  const tr = (key) => (typeof t === 'function' ? t(key) : key);
  const lang = () => (typeof currentLang !== 'undefined' ? currentLang : 'tet');
  const PRESETS = ['today', '7days', 'month', 'custom'];

  const page = {
    cfg,
    esc,
    tr,
    lang,
    el,
    state: { tab: 'summary', preset: 'today' },
    tabs: {},
    started: false,
  };
  window.ReportsPage = page;

  // ---------------------------------------------------------------- shared helpers

  /** Period parameters (preset, start, end) plus the language, as URLSearchParams. */
  page.periodParams = function () {
    const params = new URLSearchParams({ preset: page.state.preset, lang: lang() });
    if (page.state.preset === 'custom') {
      params.set('start', el.start.value);
      params.set('end', el.end.value);
    }
    return params;
  };

  page.showError = function (message) {
    el.error.textContent = message || tr('rpt_load_error');
    el.error.hidden = false;
  };

  page.clearError = function () {
    el.error.hidden = true;
  };

  /** Point the download buttons at the given URLs. Pass nulls to disable them (loading or failed request). */
  page.setDownloads = function (csvUrl, pdfUrl) {
    [[el.csv, csvUrl], [el.pdf, pdfUrl]].forEach(([link, url]) => {
      const enabled = Boolean(url);
      if (enabled) link.href = url;
      link.classList.toggle('disabled', !enabled);
      link.setAttribute('aria-disabled', enabled ? 'false' : 'true');
    });
  };

  page.exportUrl = function (params, format) {
    const p = new URLSearchParams(params);
    p.set('format', format);
    return cfg.exportUrl + '?' + p.toString();
  };

  page.renderBadge = function (status) {
    if (!status || !status.code) return '-';
    return '<span class="badge-soft' + (status.tone ? ' tone-' + esc(status.tone) : '') + '">' + esc(status.label) + '</span>';
  };

  page.emptyState = function (icon, messageKey, colspan) {
    return '<tr><td colspan="' + colspan + '" class="cell-empty"><div class="empty-state"><i class="' + esc(icon) +
      '" aria-hidden="true"></i><div>' + esc(tr(messageKey)) + '</div></div></td></tr>';
  };

  // ---------------------------------------------------------------- period

  function setPeriod(preset, start, end) {
    page.state.preset = PRESETS.includes(preset) ? preset : 'today';
    if (start) el.start.value = start;
    if (end) el.end.value = end;
    el.chips.forEach((chip) => {
      const active = chip.dataset.preset === page.state.preset;
      chip.classList.toggle('active', active);
      chip.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    el.custom.hidden = page.state.preset !== 'custom';
  }

  /** Apply preset/start/end from a hash query (drill-down, deep links). Returns true when it changed anything. */
  page.applyPeriodQuery = function (query) {
    const start = query.get('start');
    const end = query.get('end');
    if (start && end) {
      setPeriod('custom', start, end);
      return true;
    }
    if (PRESETS.includes(query.get('preset') || '')) {
      setPeriod(query.get('preset'));
      return true;
    }
    return false;
  };

  // ---------------------------------------------------------------- routing and tabs

  function parseHash() {
    const raw = decodeURIComponent(window.location.hash.replace(/^#/, ''));
    const [path, queryString] = raw.split('?');
    const parts = (path || '').split('/').filter(Boolean);
    const known = ['summary', 'history', 'audit'].includes(parts[0]) ? parts[0] : 'summary';
    // A tab that is not on the page (the Audit tab for cashiers) falls back to the summary.
    const tab = page.tabs[known] ? known : 'summary';
    return { tab, parts: parts.slice(1), query: new URLSearchParams(queryString || '') };
  }

  function syncNav(tab) {
    const hasHistoryLink = Boolean(document.querySelector('[data-nav="history"]'));
    document.querySelectorAll('[data-nav]').forEach((link) => {
      const active = link.dataset.nav === 'history' ? tab === 'history' : tab === 'summary' || !hasHistoryLink;
      link.classList.toggle('is-active', active);
      link.setAttribute('aria-current', active ? 'page' : 'false');
    });
  }

  function showTab(name) {
    page.state.tab = name;
    el.tabs.forEach((btn) => {
      const active = btn.dataset.tab === name;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    el.summaryPane.hidden = name !== 'summary';
    el.historyPane.hidden = name !== 'history';
    el.summaryControls.hidden = name !== 'summary';
    el.historyControls.hidden = name !== 'history';
    if (el.auditPane) el.auditPane.hidden = name !== 'audit';
    if (el.auditControls) el.auditControls.hidden = name !== 'audit';
    // Every tab sets its own download URLs on enter (the audit tab exports the audit log).
    if (el.downloads) el.downloads.hidden = false;
    page.setDownloads(null, null);
    page.clearError();
    syncNav(name);
  }

  function route() {
    const { tab, parts, query } = parseHash();
    const impl = page.tabs[tab];
    if (!impl) return;
    page.applyPeriodQuery(query);
    showTab(tab);
    impl.enter({ parts, query });
  }

  /** Change the hash (adds a history entry) and route; routes directly when the hash is unchanged. */
  page.navigate = function (hash) {
    if (window.location.hash === hash) route();
    else window.location.hash = hash;
  };

  /** Update the hash without routing, so the URL always reflects the visible tab. */
  page.setHash = function (hash) {
    if (window.location.hash !== hash) window.history.replaceState(null, '', hash);
  };

  /**
   * Open an offcanvas without tripping over Bootstrap's transition guard: show() is ignored while a hide is
   * still running, so wait for `hidden.bs.offcanvas` in that case instead of calling it blindly.
   */
  page.showOffcanvas = function (element) {
    const instance = bootstrap.Offcanvas.getOrCreateInstance(element);
    if (instance._isShown) return;
    if (instance._isTransitioning) {
      element.addEventListener('hidden.bs.offcanvas', () => instance.show(), { once: true });
      return;
    }
    instance.show();
  };

  page.registerTab = function (name, impl) {
    page.tabs[name] = impl;
  };

  page.reload = function () {
    const impl = page.tabs[page.state.tab];
    if (impl) impl.load();
  };

  // ---------------------------------------------------------------- summary tab

  const summary = {
    drills: [],

    params() {
      const params = page.periodParams();
      params.set('type', el.type.value);
      return params;
    },

    updateDownloads(enabled) {
      const params = summary.params();
      page.setDownloads(enabled ? page.exportUrl(params, 'csv') : null, enabled ? page.exportUrl(params, 'pdf') : null);
    },

    render(data) {
      el.period.textContent = data.period_label;
      el.scope.hidden = !data.scope_own;
      summary.drills = data.drills || [];
      const hasDrill = summary.drills.some(Boolean);
      el.hint.hidden = !hasDrill;

      el.summary.innerHTML = data.summary
        .map((item) => '<div class="kpi"><div class="kpi-label">' + esc(item.label) + '</div><div class="kpi-value">' + esc(item.value) + '</div></div>')
        .join('');

      el.head.innerHTML =
        '<tr>' + data.columns.map((col) => '<th' + (col.align === 'end' ? ' class="text-end"' : '') + '>' + esc(col.label) + '</th>').join('') + '</tr>';

      if (!data.rows.length) {
        el.body.innerHTML = page.emptyState('fa-solid fa-chart-simple', 'rpt_empty', data.columns.length);
        return;
      }

      el.body.innerHTML = data.rows
        .map((row, index) => {
          const drill = summary.drills[index];
          const attrs = drill ? ' class="row-link" tabindex="0" role="link" data-drill="' + index + '"' : '';
          const cells = row
            .map((cell, i) => {
              const col = data.columns[i];
              if (i === 0) return '<td class="cell-primary fw-semibold">' + esc(cell) + '</td>';
              const align = col.align === 'end' ? 'text-end tabular' : '';
              return '<td class="' + align + '" data-label="' + esc(col.label) + '">' + esc(cell) + '</td>';
            })
            .join('');
          return '<tr' + attrs + '>' + cells + '</tr>';
        })
        .join('');
    },

    async load() {
      const current = (summary.requestId = (summary.requestId || 0) + 1);
      page.clearError();
      summary.updateDownloads(false);
      el.summary.innerHTML = '';
      el.head.innerHTML = '';
      el.period.textContent = '';
      el.scope.hidden = true;
      el.hint.hidden = true;
      el.body.innerHTML = page.emptyState('fa-solid fa-spinner fa-spin', 'rpt_loading', 4);
      el.body.setAttribute('aria-busy', 'true');

      try {
        const response = await fetch(cfg.dataUrl + '?' + summary.params().toString(), {
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
        });
        const payload = await response.json();
        if (current !== summary.requestId) return;
        el.body.setAttribute('aria-busy', 'false');
        if (!response.ok) {
          el.body.innerHTML = '';
          page.showError(payload && payload.error && payload.error.message);
          return;
        }
        summary.render(payload);
        summary.updateDownloads(true);
      } catch (e) {
        if (current !== summary.requestId) return;
        el.body.setAttribute('aria-busy', 'false');
        el.body.innerHTML = '';
        page.showError();
      }
    },

    enter({ query }) {
      // Deep links such as #summary?type=menu_sales (from the admin overview) pick the report.
      const type = query && query.get('type');
      if (type && Array.from(el.type.options).some((option) => option.value === type)) el.type.value = type;
      page.setHash('#summary');
      summary.load();
    },

    /** Row click: open the matching Riwayat list with the drill-down filters. */
    openDrill(index) {
      const drill = summary.drills[index];
      if (!drill) return;
      const query = new URLSearchParams();
      Object.entries(drill).forEach(([key, value]) => {
        if (key !== 'kind' && value) query.set(key, value);
      });
      page.navigate('#history/' + drill.kind + '?' + query.toString());
    },
  };

  page.registerTab('summary', { enter: summary.enter, load: summary.load });

  el.body.addEventListener('click', (event) => {
    const row = event.target.closest('tr[data-drill]');
    if (row) summary.openDrill(Number(row.dataset.drill));
  });
  el.body.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const row = event.target.closest('tr[data-drill]');
    if (!row) return;
    event.preventDefault();
    summary.openDrill(Number(row.dataset.drill));
  });

  // ---------------------------------------------------------------- shared controls

  el.tabs.forEach((btn) =>
    btn.addEventListener('click', () => {
      const name = btn.dataset.tab;
      if (name === 'history') page.navigate('#history/' + ((page.tabs.history && page.tabs.history.lastKind) || 'orders'));
      else page.navigate('#' + name);
    })
  );

  el.chips.forEach((chip) =>
    chip.addEventListener('click', () => {
      setPeriod(chip.dataset.preset);
      page.reload();
    })
  );
  el.type.addEventListener('change', () => summary.load());
  el.form.addEventListener('submit', (event) => {
    event.preventDefault();
    page.reload();
  });

  // Reports link inside the portal navigation: switch tabs instead of reloading the page.
  document.addEventListener('click', (event) => {
    const link = event.target.closest('[data-nav="reports"]');
    if (!link) return;
    event.preventDefault();
    page.navigate('#summary');
  });

  window.addEventListener('hashchange', route);

  function start() {
    page.started = true;
    route();
  }

  window.addEventListener('languageChanged', () => {
    document.title = tr('reports') + ' - ' + (cfg.restaurant || '');
    if (!page.started) start();
    else page.reload();
  });

  // i18n.js dispatches `languageChanged` on DOMContentLoaded, which starts the page. Without i18n, start now.
  if (typeof t !== 'function') start();
})();
