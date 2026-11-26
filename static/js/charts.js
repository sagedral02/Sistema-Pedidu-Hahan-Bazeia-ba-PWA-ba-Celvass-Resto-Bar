/**
 * Small SVG chart library for the admin Overview (no dependencies).
 *
 *   Charts.line(container, { labels, series:[{ key, label, values, color, dashed, fill }], ... })
 *   Charts.bar(container,  { labels, series:[{ key, label, values, color }], ... })
 *   Charts.hbar(container, { items:[{ key, label, value, detail:[{label,value}] }], ... })
 *   Charts.donut(container,{ items:[{ key, label, value, color }], centerValue, centerLabel, ... })
 *   Charts.destroy(container)
 *
 * Shared options:
 *   ariaLabel     accessible name of the chart            keyboardHint  extra hint for keyboard users
 *   emptyText     text shown instead of an empty chart    format(value) value formatter for tooltips
 *   formatAxis    compact formatter for the value axis    labelFormat(label, index) x axis text
 *   tooltipLabel(label, index)  heading of the tooltip    onClick(payload) called on click / Enter
 *   height        chart height in px (line and bar)       colors are CSS variable names ('--accent') or css colors
 *   integer       line and bar: keep the value axis on whole numbers (counts)
 *
 * Every chart:
 *   - is drawn with SVG DOM calls and textContent only, so data is never parsed as markup;
 *   - redraws when its container is resized (ResizeObserver) and skips drawing while hidden;
 *   - shows a tooltip on hover, touch and keyboard focus (arrows / Tab, Enter to activate, Escape to close);
 *   - offers legend buttons (aria-pressed) that hide and show series or slices;
 *   - ships a visually hidden table with the same numbers for screen readers;
 *   - copes with empty data, all-zero data and constant data without dividing by zero;
 *   - plays its entry animation only when the user has not asked for reduced motion.
 */
(function () {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const PALETTE = ['--accent', '--info', '--success', '--warning', '--danger', '--text-tertiary'];
  const FALLBACK_COLOR = '#64748b';
  const states = new WeakMap();

  // ---------------------------------------------------------------- DOM helpers

  function svgEl(name, attrs, text) {
    const node = document.createElementNS(NS, name);
    if (attrs) Object.keys(attrs).forEach((key) => node.setAttribute(key, attrs[key]));
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function htmlEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function resolveColor(spec, index) {
    const wanted = spec || PALETTE[index % PALETTE.length];
    if (wanted.startsWith('--')) {
      return getComputedStyle(document.body).getPropertyValue(wanted).trim() || FALLBACK_COLOR;
    }
    return wanted;
  }

  function reducedMotion() {
    return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function number(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  /** Round a maximum up to a tidy axis value (1, 2, 2.5, 5, 10 times a power of ten). Never returns 0. */
  function niceMax(max) {
    if (!(max > 0)) return 1;
    const exp = Math.floor(Math.log10(max));
    const base = Math.pow(10, exp);
    const f = max / base;
    const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    return nice * base;
  }

  function compact(value) {
    const n = number(value);
    const abs = Math.abs(n);
    if (abs >= 1e6) return (n / 1e6).toFixed(abs >= 1e7 ? 0 : 1).replace(/\.0$/, '') + 'M';
    if (abs >= 1e3) return (n / 1e3).toFixed(abs >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'k';
    return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, '');
  }

  function truncate(text, maxChars) {
    const s = String(text);
    return s.length > maxChars ? s.slice(0, Math.max(1, maxChars - 1)) + '\u2026' : s;
  }

  // ---------------------------------------------------------------- shared parts

  function makeTooltip(container) {
    const tip = htmlEl('div', 'chart-tooltip');
    tip.setAttribute('role', 'tooltip');
    tip.hidden = true;
    const live = htmlEl('div', 'visually-hidden');
    live.setAttribute('aria-live', 'polite');
    container.append(tip, live);

    return {
      show(nodes, x, y, liveText) {
        tip.replaceChildren(...nodes);
        tip.hidden = false;
        const width = tip.offsetWidth;
        const height = tip.offsetHeight;
        const maxLeft = container.clientWidth - width;
        let left = x + 14;
        if (left > maxLeft) left = x - width - 14;
        left = Math.max(0, Math.min(left, Math.max(0, maxLeft)));
        let top = y - height - 12;
        if (top < 0) top = y + 14;
        tip.style.left = left + 'px';
        tip.style.top = top + 'px';
        if (liveText !== undefined) live.textContent = liveText;
      },
      hide() {
        tip.hidden = true;
        live.textContent = '';
      },
    };
  }

  function tipRow(color, label, value) {
    const row = htmlEl('div', 'chart-tip-row');
    if (color) {
      const dot = htmlEl('span', 'chart-tip-dot');
      dot.style.background = color;
      row.append(dot);
    }
    row.append(htmlEl('span', 'chart-tip-label', label), htmlEl('strong', 'chart-tip-value', value));
    return row;
  }

  function makeLegend(container, entries, state, redraw) {
    if (!entries.length) return;
    const legend = htmlEl('div', 'chart-legend');
    entries.forEach((entry) => {
      const hidden = state.hidden.has(entry.key);
      const button = htmlEl('button', 'chart-legend-item');
      button.type = 'button';
      button.setAttribute('aria-pressed', hidden ? 'false' : 'true');
      const swatch = htmlEl('span', 'chart-swatch');
      swatch.style.background = entry.color;
      if (entry.dashed) swatch.classList.add('is-dashed');
      button.append(swatch, htmlEl('span', 'chart-legend-label', entry.label));
      if (entry.value !== undefined) button.append(htmlEl('span', 'chart-legend-value', entry.value));
      button.addEventListener('click', () => {
        if (state.hidden.has(entry.key)) {
          state.hidden.delete(entry.key);
        } else if (state.hidden.size < entries.length - 1) {
          state.hidden.add(entry.key); // never hide the last visible entry
        }
        redraw();
      });
      legend.append(button);
    });
    container.append(legend);
  }

  function makeTable(container, caption, headers, rows) {
    // A <table> ignores the 1px width of .visually-hidden, so hide a wrapper instead
    const wrap = htmlEl('div', 'visually-hidden');
    const table = htmlEl('table');
    table.append(htmlEl('caption', '', caption));
    const head = htmlEl('tr');
    headers.forEach((h) => {
      const th = htmlEl('th', '', h);
      th.scope = 'col';
      head.append(th);
    });
    const thead = htmlEl('thead');
    thead.append(head);
    const tbody = htmlEl('tbody');
    rows.forEach((cells) => {
      const tr = htmlEl('tr');
      cells.forEach((cell, i) => {
        const td = htmlEl(i === 0 ? 'th' : 'td', '', cell);
        if (i === 0) td.scope = 'row';
        tr.append(td);
      });
      tbody.append(tr);
    });
    table.append(thead, tbody);
    wrap.append(table);
    container.append(wrap);
  }

  function emptyState(container, text) {
    const wrap = htmlEl('div', 'empty-state chart-empty');
    const icon = htmlEl('i', 'fa-solid fa-chart-simple');
    icon.setAttribute('aria-hidden', 'true');
    wrap.append(icon, htmlEl('div', '', text || ''));
    container.append(wrap);
  }

  function labelFor(o, index) {
    const raw = o.labels[index];
    return o.labelFormat ? o.labelFormat(raw, index) : String(raw);
  }

  function describe(o) {
    return [o.ariaLabel, o.keyboardHint].filter(Boolean).join('. ');
  }

  /**
   * Pointer + keyboard handling for charts whose data is indexed along x (line, bar).
   * ctx: { count, indexAt(px), anchor(index) -> {x,y}, activate(index, point), clear(), click(index) }
   */
  function wireIndexed(svg, ctx) {
    let active = null;

    const set = (index, point) => {
      active = index;
      ctx.activate(index, point);
    };
    const clear = () => {
      active = null;
      ctx.clear();
    };

    const fromPointer = (event) => {
      const rect = svg.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const index = ctx.indexAt(px);
      if (index === null) return clear();
      set(index, { x: px, y: event.clientY - rect.top });
    };
    svg.addEventListener('pointermove', fromPointer);
    svg.addEventListener('pointerdown', fromPointer); // a tap on touch screens has no preceding move
    svg.addEventListener('pointerleave', () => {
      if (document.activeElement !== svg) clear();
    });
    svg.addEventListener('click', () => {
      if (active !== null && ctx.click) ctx.click(active);
    });
    svg.addEventListener('focus', () => {
      if (active === null) set(ctx.count - 1, ctx.anchor(ctx.count - 1));
    });
    svg.addEventListener('blur', clear);
    svg.addEventListener('keydown', (event) => {
      const last = ctx.count - 1;
      let next = active === null ? last : active;
      if (event.key === 'ArrowRight') next = Math.min(last, next + 1);
      else if (event.key === 'ArrowLeft') next = Math.max(0, next - 1);
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = last;
      else if (event.key === 'Escape') return clear();
      else if ((event.key === 'Enter' || event.key === ' ') && active !== null && ctx.click) {
        event.preventDefault();
        return ctx.click(active);
      } else return;
      event.preventDefault();
      set(next, ctx.anchor(next));
    });
  }

  function rootSvg(width, height, label, focusable) {
    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, width, height, role: 'img', 'aria-label': label });
    if (focusable) svg.setAttribute('tabindex', '0');
    return svg;
  }

  function axes(svg, m, w, h, max, o) {
    const grid = svgEl('g', { class: 'chart-grid' });
    const fmt = o.formatAxis || compact;
    for (let k = 0; k <= 4; k += 1) {
      const y = m.t + h - (h * k) / 4;
      grid.append(svgEl('line', { x1: m.l, x2: m.l + w, y1: y, y2: y, class: k === 0 ? 'chart-baseline' : '' }));
      grid.append(svgEl('text', { x: m.l - 8, y: y + 4, 'text-anchor': 'end', class: 'chart-axis' }, fmt((max * k) / 4)));
    }
    svg.append(grid);
  }

  // ---------------------------------------------------------------- line and bar (indexed charts)

  function indexedHasData(o) {
    return o.labels.length > 0 && o.series.some((s) => s.values.some((v) => number(v) > 0));
  }

  function drawIndexed(container, state, o, width, animate, kind) {
    const all = o.series.map((s, i) => Object.assign({}, s, { color: resolveColor(s.color, i) }));
    const shown = all.filter((s) => !state.hidden.has(s.key));
    const height = o.height || 240;
    const m = { t: 14, r: 14, b: 30, l: 48 };
    const w = Math.max(40, width - m.l - m.r);
    const h = height - m.t - m.b;
    const n = o.labels.length;
    let max = niceMax(Math.max(0, ...shown.flatMap((s) => s.values.map(number))));
    if (o.integer && max < 4) max = 4; // counts: whole-number gridlines (0, 1, 2, 3, 4) instead of 0.25 steps
    const fmt = o.format || ((v) => String(v));
    const y = (v) => m.t + h - (number(v) / max) * h;
    const band = w / n;
    const xAt = kind === 'bar' ? (i) => m.l + band * (i + 0.5) : (i) => m.l + (n <= 1 ? w / 2 : (i * w) / (n - 1));

    const svg = rootSvg(width, height, describe(o), true);
    axes(svg, m, w, h, max, o);

    // x axis labels, thinned so they never overlap
    const perLabel = 54;
    const step = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(w / perLabel))));
    const xAxis = svgEl('g');
    for (let i = 0; i < n; i += 1) {
      if (i % step !== 0) continue;
      xAxis.append(svgEl('text', { x: xAt(i), y: height - 8, 'text-anchor': 'middle', class: 'chart-axis' }, labelFor(o, i)));
    }
    svg.append(xAxis);

    const marker = svgEl('line', { class: 'chart-cross', y1: m.t, y2: m.t + h, visibility: 'hidden' });
    const bandRect = svgEl('rect', { class: 'chart-band', y: m.t, height: h, visibility: 'hidden' });
    svg.append(kind === 'bar' ? bandRect : marker);

    const data = svgEl('g', { class: animate && !reducedMotion() ? (kind === 'bar' ? 'chart-grow' : 'chart-fade') : '' });
    const dots = [];
    if (kind === 'bar') {
      const groupWidth = Math.min(band * 0.7, 56);
      const barWidth = groupWidth / Math.max(1, shown.length);
      shown.forEach((s, si) => {
        s.values.forEach((value, i) => {
          const top = y(value);
          const barH = Math.max(0, m.t + h - top);
          if (barH <= 0) return;
          data.append(svgEl('rect', {
            x: xAt(i) - groupWidth / 2 + si * barWidth + 1, y: top, width: Math.max(2, barWidth - 2), height: barH,
            rx: 3, fill: s.color, class: 'chart-bar',
          }));
        });
      });
    } else {
      shown.forEach((s) => {
        const points = s.values.map((v, i) => `${i === 0 ? 'M' : 'L'}${xAt(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
        if (s.fill) {
          const area = `${points} L${xAt(n - 1).toFixed(1)} ${m.t + h} L${xAt(0).toFixed(1)} ${m.t + h} Z`;
          data.append(svgEl('path', { d: area, fill: s.color, opacity: 0.12 }));
        }
        const line = svgEl('path', {
          d: points, fill: 'none', stroke: s.color, 'stroke-width': 2.25, 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
        });
        if (s.dashed) line.setAttribute('stroke-dasharray', '5 4');
        data.append(line);
        if (n <= 31) {
          s.values.forEach((v, i) => data.append(svgEl('circle', { cx: xAt(i), cy: y(v), r: 2.5, fill: s.color })));
        }
        dots.push({ series: s, node: svgEl('circle', { r: 5, fill: s.color, class: 'chart-active-dot', visibility: 'hidden' }) });
      });
    }
    svg.append(data);
    dots.forEach((d) => svg.append(d.node));

    container.append(svg);
    const tooltip = makeTooltip(container);

    const anchor = (index) => {
      const top = Math.min(...shown.map((s) => y(s.values[index])));
      return { x: xAt(index), y: Number.isFinite(top) ? top : m.t };
    };

    wireIndexed(svg, {
      count: n,
      anchor,
      indexAt(px) {
        if (px < m.l - 6 || px > m.l + w + 6) return null;
        if (kind === 'bar') return Math.max(0, Math.min(n - 1, Math.floor((px - m.l) / band)));
        return n <= 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / w) * (n - 1))));
      },
      activate(index, point) {
        const x = xAt(index);
        if (kind === 'bar') {
          bandRect.setAttribute('x', x - band / 2);
          bandRect.setAttribute('width', band);
          bandRect.setAttribute('visibility', 'visible');
        } else {
          marker.setAttribute('x1', x);
          marker.setAttribute('x2', x);
          marker.setAttribute('visibility', 'visible');
          dots.forEach((d) => {
            d.node.setAttribute('cx', x);
            d.node.setAttribute('cy', y(d.series.values[index]));
            d.node.setAttribute('visibility', 'visible');
          });
        }
        const heading = o.tooltipLabel ? o.tooltipLabel(o.labels[index], index) : labelFor(o, index);
        const nodes = [htmlEl('div', 'chart-tip-title', heading)];
        shown.forEach((s) => nodes.push(tipRow(s.color, s.label, fmt(s.values[index]))));
        const spoken = heading + ': ' + shown.map((s) => `${s.label} ${fmt(s.values[index])}`).join(', ');
        const pos = point || anchor(index);
        tooltip.show(nodes, pos.x, pos.y, spoken);
      },
      clear() {
        marker.setAttribute('visibility', 'hidden');
        bandRect.setAttribute('visibility', 'hidden');
        dots.forEach((d) => d.node.setAttribute('visibility', 'hidden'));
        tooltip.hide();
      },
      click: o.onClick ? (index) => o.onClick({ index, label: o.labels[index] }) : null,
    });
    if (o.onClick) svg.classList.add('is-clickable');

    if (all.length > 1) {
      makeLegend(container, all.map((s) => ({ key: s.key, label: s.label, color: s.color, dashed: s.dashed })), state, () => redraw(container, state, false));
    }
    makeTable(
      container, o.ariaLabel || '', [''].concat(all.map((s) => s.label)),
      o.labels.map((_, i) => [labelFor(o, i)].concat(all.map((s) => fmt(s.values[i])))),
    );
  }

  // ---------------------------------------------------------------- horizontal bars

  function drawHBar(container, state, o, width, animate) {
    const items = o.items.map((it, i) => Object.assign({}, it, { color: resolveColor(it.color, 0) }));
    const rowH = 32;
    const height = items.length * rowH + 4;
    const labelW = Math.max(70, Math.min(170, Math.round(width * 0.38)));
    const valueW = 78;
    const barX = labelW + 8;
    const barW = Math.max(20, width - barX - valueW);
    const max = Math.max(...items.map((it) => number(it.value)), 0) || 1;
    const fmt = o.format || ((v) => String(v));
    const maxChars = Math.floor(labelW / 6.4);

    const svg = rootSvg(width, height, describe(o), false);
    svg.removeAttribute('role');
    svg.setAttribute('role', 'group');
    const body = svgEl('g', { class: animate && !reducedMotion() ? 'chart-grow-x' : '' });
    const tooltip = makeTooltip(container);

    items.forEach((it, i) => {
      const y = i * rowH + 2;
      const value = number(it.value);
      const len = Math.max(value > 0 ? 3 : 0, (value / max) * barW);
      const row = svgEl('g', {
        class: 'chart-row' + (o.onClick ? ' is-clickable' : ''), tabindex: 0,
        role: o.onClick ? 'button' : 'img', 'aria-label': `${it.label}: ${fmt(value)}`,
      });
      row.append(svgEl('rect', { x: 0, y, width, height: rowH - 4, class: 'chart-row-hit' }));
      row.append(svgEl('text', { x: 0, y: y + rowH / 2 + 1, class: 'chart-label' }, truncate(it.label, maxChars)));
      row.append(svgEl('rect', { x: barX, y: y + 6, width: barW, height: rowH - 16, rx: 4, class: 'chart-track' }));
      row.append(svgEl('rect', { x: barX, y: y + 6, width: len, height: rowH - 16, rx: 4, fill: it.color, class: 'chart-bar' }));
      row.append(svgEl('text', { x: barX + barW + 8, y: y + rowH / 2 + 1, class: 'chart-value' }, fmt(value)));

      const show = (event) => {
        const rect = container.getBoundingClientRect();
        const point = event && event.clientX !== undefined && event.type !== 'focus'
          ? { x: event.clientX - rect.left, y: event.clientY - rect.top }
          : { x: barX + len, y: y + rowH / 2 };
        const nodes = [htmlEl('div', 'chart-tip-title', it.label), tipRow(it.color, o.valueLabel || '', fmt(value))];
        (it.detail || []).forEach((d) => nodes.push(tipRow(null, d.label, d.value)));
        tooltip.show(nodes, point.x, point.y, `${it.label}: ${fmt(value)}`);
      };
      row.addEventListener('pointermove', show);
      row.addEventListener('pointerleave', () => document.activeElement !== row && tooltip.hide());
      row.addEventListener('focus', show);
      row.addEventListener('blur', () => tooltip.hide());
      row.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') tooltip.hide();
        else if (o.onClick && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          o.onClick({ index: i, key: it.key, label: it.label });
        }
      });
      if (o.onClick) row.addEventListener('click', () => o.onClick({ index: i, key: it.key, label: it.label }));
      body.append(row);
    });
    svg.append(body);
    container.insertBefore(svg, container.firstChild);

    makeTable(container, o.ariaLabel || '', ['', o.valueLabel || ''], items.map((it) => [it.label, fmt(number(it.value))]));
  }

  // ---------------------------------------------------------------- donut

  function drawDonut(container, state, o, width, animate) {
    const all = o.items.map((it, i) => Object.assign({}, it, { color: resolveColor(it.color, i) }));
    const shown = all.filter((it) => !state.hidden.has(it.key) && number(it.value) > 0);
    const total = shown.reduce((sum, it) => sum + number(it.value), 0);
    const fmt = o.format || ((v) => String(v));
    const size = Math.max(140, Math.min(200, width));
    const stroke = Math.round(size * 0.17);
    const r = (size - stroke) / 2 - 2;
    const circ = 2 * Math.PI * r;
    const cx = size / 2;

    const wrap = htmlEl('div', 'chart-donut');
    const svg = rootSvg(size, size, describe(o), false);
    svg.setAttribute('role', 'group');
    const tooltip = makeTooltip(container);
    const ring = svgEl('g', { class: animate && !reducedMotion() ? 'chart-pop' : '' });
    ring.append(svgEl('circle', { cx, cy: cx, r, fill: 'none', class: 'chart-track-ring', 'stroke-width': stroke }));

    const gap = shown.length > 1 ? 2 : 0;
    let offset = 0;
    shown.forEach((it) => {
      const value = number(it.value);
      const len = (value / total) * circ;
      const slice = svgEl('circle', {
        cx, cy: cx, r, fill: 'none', stroke: it.color, 'stroke-width': stroke, class: 'chart-slice' + (o.onClick ? ' is-clickable' : ''),
        'stroke-dasharray': `${Math.max(0, len - gap)} ${circ - Math.max(0, len - gap)}`, 'stroke-dashoffset': -offset,
        transform: `rotate(-90 ${cx} ${cx})`, tabindex: 0, role: o.onClick ? 'button' : 'img',
        'aria-label': `${it.label}: ${fmt(value)} (${Math.round((value / total) * 100)}%)`,
      });
      const show = (event) => {
        const rect = container.getBoundingClientRect();
        const point = event && event.type !== 'focus' && event.clientX !== undefined
          ? { x: event.clientX - rect.left, y: event.clientY - rect.top }
          : { x: svg.getBoundingClientRect().left - rect.left + cx, y: svg.getBoundingClientRect().top - rect.top + cx };
        tooltip.show(
          [htmlEl('div', 'chart-tip-title', it.label), tipRow(it.color, fmt(value), `${Math.round((value / total) * 1000) / 10}%`)],
          point.x, point.y, `${it.label}: ${fmt(value)}`,
        );
      };
      slice.addEventListener('pointermove', show);
      slice.addEventListener('pointerleave', () => document.activeElement !== slice && tooltip.hide());
      slice.addEventListener('focus', show);
      slice.addEventListener('blur', () => tooltip.hide());
      slice.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') tooltip.hide();
        else if (o.onClick && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          o.onClick({ key: it.key, label: it.label });
        }
      });
      if (o.onClick) slice.addEventListener('click', () => o.onClick({ key: it.key, label: it.label }));
      ring.append(slice);
      offset += len;
    });
    svg.append(ring);

    const center = svgEl('g', { class: 'chart-center' });
    center.append(svgEl('text', { x: cx, y: cx + 2, 'text-anchor': 'middle', class: 'chart-center-value' }, o.centerValue ? o.centerValue(total) : fmt(total)));
    if (o.centerLabel) center.append(svgEl('text', { x: cx, y: cx + 20, 'text-anchor': 'middle', class: 'chart-center-label' }, o.centerLabel));
    svg.append(center);
    wrap.append(svg);

    const legendHost = htmlEl('div', 'chart-donut-legend');
    wrap.append(legendHost);
    container.insertBefore(wrap, container.firstChild);
    makeLegend(
      legendHost,
      all.map((it) => ({ key: it.key, label: it.label, color: it.color, value: fmt(number(it.value)) })),
      state, () => redraw(container, state, false),
    );
    makeTable(container, o.ariaLabel || '', ['', o.valueLabel || ''], all.map((it) => [it.label, fmt(number(it.value))]));
  }

  // ---------------------------------------------------------------- orchestration

  const RENDERERS = {
    line: { hasData: indexedHasData, draw: (c, s, o, w, a) => drawIndexed(c, s, o, w, a, 'line') },
    bar: { hasData: indexedHasData, draw: (c, s, o, w, a) => drawIndexed(c, s, o, w, a, 'bar') },
    hbar: { hasData: (o) => o.items.some((it) => number(it.value) > 0), draw: drawHBar },
    donut: { hasData: (o) => o.items.some((it) => number(it.value) > 0), draw: drawDonut },
  };

  function redraw(container, state, animate) {
    const width = Math.round(container.clientWidth);
    state.width = width;
    container.replaceChildren();
    container.classList.add('chart');
    container.dataset.chart = state.type;
    if (width <= 0) return; // hidden: the ResizeObserver draws it when it becomes visible

    const renderer = RENDERERS[state.type];
    if (!renderer.hasData(state.opts)) {
      emptyState(container, state.opts.emptyText);
      return;
    }
    renderer.draw(container, state, state.opts, width, animate);
  }

  function render(container, type, opts) {
    let state = states.get(container);
    if (!state) {
      state = { hidden: new Set(), observer: null, width: 0, raf: 0 };
      states.set(container, state);
    }
    state.type = type;
    state.opts = Object.assign({ labels: [], series: [], items: [] }, opts);

    if (!state.observer && typeof ResizeObserver === 'function') {
      state.observer = new ResizeObserver(() => {
        const width = Math.round(container.clientWidth);
        if (width === state.width) return;
        cancelAnimationFrame(state.raf);
        state.raf = requestAnimationFrame(() => redraw(container, state, state.width === 0));
      });
      state.observer.observe(container);
    }
    redraw(container, state, true);
    return { redraw: () => redraw(container, state, false), destroy: () => destroy(container) };
  }

  function destroy(container) {
    const state = states.get(container);
    if (!state) return;
    if (state.observer) state.observer.disconnect();
    cancelAnimationFrame(state.raf);
    states.delete(container);
    container.replaceChildren();
  }

  window.Charts = {
    line: (container, opts) => render(container, 'line', opts),
    bar: (container, opts) => render(container, 'bar', opts),
    hbar: (container, opts) => render(container, 'hbar', opts),
    donut: (container, opts) => render(container, 'donut', opts),
    destroy,
    compact,
    niceMax,
  };
})();
