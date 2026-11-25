/**
 * Admin Overview: KPI cards with period-over-period change and interactive charts.
 * Data comes from GET /api/v1/admin/overview/?range=today|7d|30d (see apps/reporting/overview.py).
 * Charts are drawn by static/js/charts.js; clicking a chart element opens the matching report or history list.
 *
 * Everything inserted into the page is built with textContent / DOM calls, never with markup from the server.
 */
(function () {
  'use strict';

  const panel = document.getElementById('overview-panel');
  if (!panel || !window.Charts) return;

  const REPORTS_URL = panel.dataset.reportsUrl || '/reports/';
  const REFRESH_MS = 60000;
  const STALE_MS = 30000;
  const $ = (id) => document.getElementById(id);
  const tr = (key) => (typeof t === 'function' ? t(key) : key);
  const money = (value) => (typeof formatMoney === 'function' ? formatMoney(value) : '$' + Number(value).toFixed(2));
  const count = (value) => String(Math.round(Number(value) || 0));

  const state = { range: 'today', topMode: 'qty', data: null, loadedAt: 0, requestId: 0, loading: false };
  const el = {
    status: $('ov-status'),
    rangeGroup: $('ov-range'),
    rangeButtons: Array.from(document.querySelectorAll('#ov-range [data-range]')),
    refresh: $('ov-refresh'),
    modeButtons: Array.from(document.querySelectorAll('#ov-top-mode [data-mode]')),
    paymentsList: $('ov-recent-payments'),
    rejectionsList: $('ov-recent-rejections'),
  };

  // ---------------------------------------------------------------- navigation helpers

  function periodQuery(data) {
    return new URLSearchParams({ start: data.period.start, end: data.period.end });
  }

  function go(hash, params) {
    const query = params.toString();
    window.location.href = REPORTS_URL + hash + (query ? '?' + query : '');
  }

  function openHistory(kind, extra) {
    const params = periodQuery(state.data);
    Object.entries(extra || {}).forEach(([key, value]) => value && params.set(key, value));
    go('#history/' + kind, params);
  }

  const hourLabel = (hour) => String(hour).padStart(2, '0') + ':00';
  const shortDay = (iso) => iso.slice(8, 10) + '/' + iso.slice(5, 7);
  const fullDay = (iso) => iso.slice(8, 10) + '/' + iso.slice(5, 7) + '/' + iso.slice(0, 4);

  // ---------------------------------------------------------------- KPI cards

  function setDelta(node, tone, icon, text, note) {
    node.dataset.tone = tone;
    node.replaceChildren();
    if (icon) {
      const i = document.createElement('i');
      i.className = 'fa-solid ' + icon;
      i.setAttribute('aria-hidden', 'true');
      node.append(i);
    }
    node.append(document.createTextNode(text));
    if (note) {
      const small = document.createElement('span');
      small.className = 'kpi-delta-note';
      small.textContent = note;
      node.append(small);
    }
  }

  function deltaFor(card, badWhenUp) {
    const node = card.querySelector('[data-delta]');
    const change = card._change;
    if (change === null || change === undefined) {
      setDelta(node, 'flat', null, tr('ov_no_comparison'));
      return;
    }
    const sign = change > 0 ? '+' : change < 0 ? '\u2212' : '';
    const amount = Math.abs(change).toFixed(1);
    const text = card._unit === 'points' ? `${sign}${amount} ${tr('ov_points')}` : `${sign}${amount}%`;
    const tone = change === 0 ? 'flat' : (change > 0) === !badWhenUp ? 'good' : 'bad';
    const icon = change === 0 ? 'fa-minus' : change > 0 ? 'fa-arrow-up' : 'fa-arrow-down';
    setDelta(node, tone, icon, ' ' + text + ' ', tr('ov_vs_previous'));
  }

  function renderKpis(data) {
    const cards = {};
    document.querySelectorAll('#ov-kpis [data-kpi]').forEach((card) => (cards[card.dataset.kpi] = card));
    const k = data.kpi;

    const fill = (key, value, change, options) => {
      const card = cards[key];
      if (!card) return;
      card.querySelector('[data-value]').textContent = value;
      card._change = change;
      card._unit = (options && options.unit) || 'percent';
      deltaFor(card, Boolean(options && options.badWhenUp));
    };

    fill('sales', money(k.sales.value), k.sales.delta_pct);
    fill('transactions', count(k.transactions.value), k.transactions.delta_pct);
    fill('average', money(k.average.value), k.average.delta_pct);
    fill('orders', count(k.orders.value), k.orders.delta_pct);
    fill('rejection_rate', k.rejection_rate.value === null ? '\u2014' : k.rejection_rate.value.toFixed(1) + '%',
      k.rejection_rate.delta_points, { unit: 'points', badWhenUp: true });

    const tables = cards.tables;
    tables.querySelector('[data-value]').textContent = `${data.tables.occupied} / ${data.tables.total}`;
    tables.querySelector('[data-delta]').replaceChildren();
  }

  // ---------------------------------------------------------------- charts

  const common = () => ({ keyboardHint: tr('ov_kbd_hint'), emptyText: tr('rpt_empty') });

  function renderTrend(data) {
    const trend = data.trend;
    const hourly = trend.granularity === 'hour';
    Charts.line($('ov-trend'), Object.assign(common(), {
      ariaLabel: tr('ov_chart_trend'),
      labels: trend.labels,
      labelFormat: (label) => (hourly ? hourLabel(label) : shortDay(label)),
      tooltipLabel: (label) => (hourly ? hourLabel(label) : fullDay(label)),
      series: [
        { key: 'current', label: tr('ov_series_current'), values: trend.current, color: '--accent', fill: true },
        { key: 'previous', label: tr('ov_series_previous'), values: trend.previous, color: '--text-tertiary', dashed: true },
      ],
      format: money,
      formatAxis: (v) => '$' + Charts.compact(v),
      onClick: ({ index }) => {
        const day = hourly ? data.period.start : trend.labels[index];
        const params = new URLSearchParams({ start: day, end: day, status: 'COMPLETED' });
        go('#history/transactions', params);
      },
    }));
  }

  function renderMethods(data) {
    const colors = { CASH: '--success', MANUAL_OTHER: '--info' };
    Charts.donut($('ov-methods'), Object.assign(common(), {
      ariaLabel: tr('rpt_type_payment_methods'),
      valueLabel: tr('ov_kpi_sales'),
      items: data.methods.map((m) => ({ key: m.method, label: tr('rpt_method_' + m.method), value: m.total, color: colors[m.method] })),
      format: money,
      centerValue: money,
      centerLabel: tr('total'),
      onClick: ({ key }) => openHistory('transactions', { method: key, status: 'COMPLETED' }),
    }));
  }

  function renderHours(data) {
    Charts.bar($('ov-hours'), Object.assign(common(), {
      ariaLabel: tr('ov_chart_hours'),
      labels: data.busy_hours.map((_, hour) => hour),
      labelFormat: (hour) => String(hour).padStart(2, '0'),
      tooltipLabel: hourLabel,
      series: [{ key: 'orders', label: tr('ov_kpi_orders'), values: data.busy_hours, color: '--accent' }],
      format: count,
      integer: true,
      height: 220,
      onClick: () => openHistory('orders'),
    }));
  }

  function renderStatuses(data) {
    const s = data.statuses;
    const filters = { waiting: 'WAITING_CASHIER_CONFIRMATION', completed: 'COMPLETED', rejected: 'REJECTED', cancelled: 'CANCELLED' };
    const rows = [
      ['waiting', '--warning'], ['active', '--info'], ['completed', '--success'], ['rejected', '--danger'], ['cancelled', '--text-tertiary'],
    ];
    Charts.hbar($('ov-statuses'), Object.assign(common(), {
      ariaLabel: tr('ov_chart_status'),
      valueLabel: tr('ov_kpi_orders'),
      items: rows.map(([key, color]) => ({ key, label: tr('ov_st_' + key), value: s[key], color })),
      format: count,
      onClick: ({ key }) => openHistory('orders', { status: filters[key] }),
    }));
  }

  function renderTop(data) {
    const byQty = state.topMode === 'qty';
    const list = byQty ? data.top_items.by_qty : data.top_items.by_revenue;
    Charts.hbar($('ov-top'), Object.assign(common(), {
      ariaLabel: tr('ov_chart_top'),
      valueLabel: tr(byQty ? 'ov_top_qty' : 'ov_top_revenue'),
      items: list.map((item) => ({
        key: item.name, label: item.name, value: byQty ? item.qty : item.revenue, color: '--accent',
        detail: [{ label: tr('ov_top_qty'), value: count(item.qty) }, { label: tr('ov_top_revenue'), value: money(item.revenue) }],
      })),
      format: byQty ? count : money,
      onClick: ({ key }) => openHistory('orders', { q: key }),
    }));
  }

  function renderCategories(data) {
    Charts.donut($('ov-categories'), Object.assign(common(), {
      ariaLabel: tr('ov_chart_categories'),
      valueLabel: tr('ov_kpi_sales'),
      items: data.categories.map((c, i) => ({ key: c.name || '_none_' + i, label: c.name || tr('rpt_uncategorized'), value: c.revenue })),
      format: money,
      centerValue: money,
      centerLabel: tr('total'),
      onClick: () => {
        const params = periodQuery(data);
        params.set('type', 'menu_sales');
        go('#summary', params);
      },
    }));
  }

  function renderCashiers(data) {
    Charts.hbar($('ov-cashiers'), Object.assign(common(), {
      ariaLabel: tr('ov_chart_cashiers'),
      valueLabel: tr('ov_kpi_sales'),
      items: data.cashiers.map((c) => ({
        key: c.id, label: c.name, value: c.total, color: '--success',
        detail: [{ label: tr('ov_kpi_transactions'), value: count(c.count) }],
      })),
      format: money,
      onClick: ({ key }) => openHistory('transactions', { cashier: key, status: 'COMPLETED' }),
    }));
  }

  // ---------------------------------------------------------------- recent lists

  function listItem(href, title, meta, amount) {
    const li = document.createElement('li');
    const left = document.createElement('div');
    left.className = 'min-w-0';
    const link = document.createElement('a');
    link.href = href;
    link.className = 'fw-semibold d-block truncate';
    link.textContent = title;
    const small = document.createElement('div');
    small.className = 'fs-8 text-tertiary truncate';
    small.textContent = meta;
    left.append(link, small);
    li.append(left);
    if (amount) {
      const strong = document.createElement('strong');
      strong.className = 'tabular';
      strong.textContent = amount;
      li.append(strong);
    }
    return li;
  }

  function emptyItem(text) {
    const li = document.createElement('li');
    li.className = 'text-tertiary fs-7';
    li.textContent = text;
    return li;
  }

  function link(kind, row) {
    const params = new URLSearchParams({ start: row.date, end: row.date, q: row.code });
    return REPORTS_URL + '#history/' + kind + '?' + params.toString();
  }

  function renderLists(data) {
    el.paymentsList.replaceChildren(
      ...(data.recent_payments.length
        ? data.recent_payments.map((p) => listItem(link('transactions', p), `${p.code} \u00b7 ${p.table}`, `${tr('rpt_method_' + p.method)} \u00b7 ${p.at}`, money(p.amount)))
        : [emptyItem(tr('ov_no_payments'))])
    );
    el.rejectionsList.replaceChildren(
      ...(data.recent_rejections.length
        ? data.recent_rejections.map((o) => listItem(link('orders', o), `${o.code} \u00b7 ${o.table}`, o.reason ? `${o.at} \u00b7 ${o.reason}` : o.at))
        : [emptyItem(tr('ov_no_rejections'))])
    );
  }

  // ---------------------------------------------------------------- render + load

  function renderAll() {
    if (!state.data) return;
    el.rangeGroup.setAttribute('aria-label', tr('ov_range_label'));
    renderKpis(state.data);
    renderTrend(state.data);
    renderMethods(state.data);
    renderHours(state.data);
    renderStatuses(state.data);
    renderTop(state.data);
    renderCategories(state.data);
    renderCashiers(state.data);
    renderLists(state.data);
  }

  function setStatus(kind) {
    el.status.dataset.state = kind;
    if (kind === 'loading') el.status.textContent = tr('rpt_loading');
    else if (kind === 'error') el.status.textContent = tr('ov_error');
    else if (kind === 'ok') {
      const now = new Date();
      el.status.textContent = `${tr('ov_updated')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
    } else el.status.textContent = '';
  }

  async function load(options) {
    const silent = Boolean(options && options.silent);
    const current = ++state.requestId;
    state.loading = true;
    if (!silent || !state.data) setStatus('loading');
    try {
      const response = await fetch('/api/v1/admin/overview/?range=' + encodeURIComponent(state.range), {
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
      });
      const body = await response.json();
      if (current !== state.requestId) return;
      if (!response.ok || body.success === false) throw new Error('overview');
      state.data = body.data;
      state.loadedAt = Date.now();
      renderAll();
      setStatus('ok');
    } catch (e) {
      if (current !== state.requestId) return;
      setStatus('error'); // keep whatever is on screen
    } finally {
      if (current === state.requestId) state.loading = false;
    }
  }

  const isVisible = () => !panel.hidden && !document.hidden;

  // ---------------------------------------------------------------- events

  el.rangeButtons.forEach((button) =>
    button.addEventListener('click', () => {
      if (button.dataset.range === state.range) return;
      state.range = button.dataset.range;
      el.rangeButtons.forEach((b) => {
        const active = b === button;
        b.classList.toggle('is-active', active);
        b.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      load();
    })
  );

  el.modeButtons.forEach((button) =>
    button.addEventListener('click', () => {
      state.topMode = button.dataset.mode;
      el.modeButtons.forEach((b) => {
        const active = b === button;
        b.classList.toggle('is-active', active);
        b.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      if (state.data) renderTop(state.data);
    })
  );

  el.refresh.addEventListener('click', () => load());

  window.addEventListener('languageChanged', () => {
    renderAll();
    if (state.data) setStatus('ok');
  });

  window.addEventListener('app:panel', (event) => {
    if (event.detail.id !== 'overview-panel') return;
    if (!state.data || Date.now() - state.loadedAt > STALE_MS) load({ silent: Boolean(state.data) });
    else renderAll(); // sizes may have changed while the panel was hidden
  });

  document.addEventListener('visibilitychange', () => {
    if (isVisible() && Date.now() - state.loadedAt > REFRESH_MS) load({ silent: true });
  });

  setInterval(() => {
    if (isVisible() && !state.loading) load({ silent: true });
  }, REFRESH_MS);

  document.addEventListener('DOMContentLoaded', () => {
    if (!panel.hidden) load();
  });
})();
