/**
 * Celvass app shell: panel navigation, dialogs, HTML escaping and small helpers.
 * Loaded on every page after Bootstrap and i18n.js.
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------
  const ESCAPE_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '`': '&#96;' };

  function escapeHtml(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"'`]/g, (c) => ESCAPE_MAP[c]);
  }

  function formatMoney(value) {
    const n = parseFloat(value);
    return '$' + (Number.isFinite(n) ? n : 0).toFixed(2);
  }

  function formatTime(value) {
    if (!value) return '';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  window.escapeHtml = escapeHtml;
  window.formatMoney = formatMoney;
  window.formatTime = formatTime;

  // ---------------------------------------------------------------------
  // Panel navigation (hash based, shared by top nav, tab bar and "More" sheet)
  //   Trigger:  <button data-panel="menu-panel" [data-view="queue"]>
  //   Panel:    <section class="app-panel" id="menu-panel">
  //   View host (optional, inside panel): <div data-view-host data-view="queue">
  // ---------------------------------------------------------------------
  function allPanels() {
    return Array.from(document.querySelectorAll('.app-panel'));
  }

  function markTriggers(id, view) {
    document.querySelectorAll('[data-panel]').forEach((el) => {
      // A menu item can cover several panels (data-panel-group="a-panel b-panel"), e.g. Katalog = menu + categories.
      const group = el.dataset.panelGroup ? el.dataset.panelGroup.split(/\s+/) : [el.dataset.panel];
      const sameId = group.includes(id);
      const sameView = !el.dataset.view || !view || el.dataset.view === view;
      const active = sameId && sameView;
      el.classList.toggle('is-active', active);
      if (el.getAttribute('role') === 'tab') {
        el.setAttribute('aria-selected', active ? 'true' : 'false');
      } else if (active) {
        el.setAttribute('aria-current', 'page');
      } else {
        el.removeAttribute('aria-current');
      }
    });
  }

  function showPanel(id, view, options) {
    const opts = Object.assign({ updateHash: true, source: 'user' }, options || {});
    const panel = document.getElementById(id);
    if (!panel || !panel.classList.contains('app-panel')) return false;

    allPanels().forEach((p) => {
      p.hidden = p !== panel;
    });

    const host = panel.querySelector('[data-view-host]');
    if (host) {
      const resolved = view || host.dataset.view || host.dataset.defaultView || '';
      if (resolved) host.dataset.view = resolved;
      view = resolved;
    }

    markTriggers(id, view);

    if (opts.updateHash) {
      const hash = '#' + id + (view ? '/' + view : '');
      if (location.hash !== hash) history.replaceState(null, '', hash);
    }

    window.dispatchEvent(new CustomEvent('app:panel', { detail: { id, view: view || null, source: opts.source } }));
    return true;
  }

  window.showPanel = showPanel;

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-panel]');
    if (!trigger) return;
    event.preventDefault();

    // Standalone pages (e.g. reports) share the portal navigation. When the panel is not on this
    // page, go to the portal that owns it.
    const home = document.body.dataset.panelHome;
    if (home && !document.getElementById(trigger.dataset.panel)) {
      const view = trigger.dataset.view ? '/' + trigger.dataset.view : '';
      window.location.href = home + '#' + trigger.dataset.panel + view;
      return;
    }

    const changed = showPanel(trigger.dataset.panel, trigger.dataset.view);
    const sheet = trigger.closest('.offcanvas');
    if (sheet && window.bootstrap) {
      const instance = bootstrap.Offcanvas.getInstance(sheet);
      if (instance) instance.hide();
    }
    if (changed && window.matchMedia('(max-width: 767.98px)').matches) {
      window.scrollTo({ top: 0, behavior: 'auto' });
    }
  });

  function initPanels() {
    const panels = allPanels();
    if (!panels.length) return;

    // Panels that moved to another page: <body data-panel-redirects="audit-panel=/reports/#audit">.
    const redirects = (document.body.dataset.panelRedirects || '').split(/\s+/).filter(Boolean);
    const wanted = decodeURIComponent(location.hash.slice(1)).split('/')[0];
    for (const rule of redirects) {
      const [from, to] = rule.split('=');
      if (from && to && from === wanted) {
        location.replace(to);
        return;
      }
    }
    const [hashId, hashView] = decodeURIComponent(location.hash.slice(1)).split('/');
    if (hashId && showPanel(hashId, hashView, { updateHash: false, source: 'init' })) return;
    const fallback = document.body.dataset.defaultPanel || panels[0].id;
    showPanel(fallback, null, { updateHash: false, source: 'init' });
  }

  window.addEventListener('hashchange', () => {
    const [id, view] = decodeURIComponent(location.hash.slice(1)).split('/');
    if (id) showPanel(id, view, { updateHash: false, source: 'hash' });
  });

  // ---------------------------------------------------------------------
  // Confirmation dialog (replaces window.confirm)
  // ---------------------------------------------------------------------
  let confirmModalEl = null;

  function ensureConfirmModal() {
    if (confirmModalEl) return confirmModalEl;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = `
      <div class="modal fade modal-sheet" id="appConfirmModal" tabindex="-1" aria-labelledby="appConfirmTitle" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" style="max-width:420px">
          <div class="modal-content">
            <div class="modal-body p-4">
              <h2 class="modal-title mb-2" id="appConfirmTitle"></h2>
              <p class="text-secondary-app mb-0" id="appConfirmMessage"></p>
              <div class="mt-3" id="appConfirmInputWrap" hidden>
                <label class="form-label" for="appConfirmInput" id="appConfirmInputLabel"></label>
                <textarea class="form-control" id="appConfirmInput" rows="2"></textarea>
                <input type="number" inputmode="numeric" step="1" class="form-control tabular" id="appConfirmNumber" hidden>
                <div class="form-text" id="appConfirmInputHint" hidden></div>
              </div>
            </div>
            <div class="modal-footer gap-2">
              <button type="button" class="btn btn-secondary-app" data-bs-dismiss="modal" id="appConfirmCancel"></button>
              <button type="button" class="btn btn-primary" id="appConfirmOk"></button>
            </div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(wrapper.firstElementChild);
    confirmModalEl = document.getElementById('appConfirmModal');
    return confirmModalEl;
  }

  /**
   * confirmDialog({ title, message, confirmText, cancelText, tone: 'danger'|'primary',
   *                 input: { label, required, hint, value, type: 'number', min, max } })
   * Resolves to false when cancelled, true (or the input text / whole number when `input` is set) when confirmed.
   */
  function confirmDialog(options) {
    const opts = Object.assign({ tone: 'primary' }, options || {});
    const el = ensureConfirmModal();
    const tr = (key, fallback) => (typeof t === 'function' && t(key) !== key ? t(key) : fallback);

    el.querySelector('#appConfirmTitle').textContent = opts.title || tr('confirm_title', 'Konfirma');
    el.querySelector('#appConfirmMessage').textContent = opts.message || '';
    el.querySelector('#appConfirmCancel').textContent = opts.cancelText || tr('cancel', 'Kansela');
    const ok = el.querySelector('#appConfirmOk');
    ok.textContent = opts.confirmText || tr('confirm', 'Konfirma');
    ok.className = 'btn ' + (opts.tone === 'danger' ? 'btn-danger' : 'btn-primary');

    const inputWrap = el.querySelector('#appConfirmInputWrap');
    const textInput = el.querySelector('#appConfirmInput');
    const numberInput = el.querySelector('#appConfirmNumber');
    const hint = el.querySelector('#appConfirmInputHint');
    const numeric = Boolean(opts.input && opts.input.type === 'number');
    const input = numeric ? numberInput : textInput;
    inputWrap.hidden = !opts.input;
    textInput.hidden = numeric;
    numberInput.hidden = !numeric;
    textInput.value = '';
    numberInput.value = '';
    hint.hidden = !(opts.input && opts.input.hint);
    hint.textContent = (opts.input && opts.input.hint) || '';
    if (opts.input) {
      el.querySelector('#appConfirmInputLabel').textContent = opts.input.label || '';
      el.querySelector('#appConfirmInputLabel').setAttribute('for', input.id);
      if (numeric) {
        numberInput.min = opts.input.min ?? '';
        numberInput.max = opts.input.max ?? '';
      }
      if (opts.input.value !== undefined && opts.input.value !== null) input.value = String(opts.input.value);
    }

    const modal = bootstrap.Modal.getOrCreateInstance(el);
    return new Promise((resolve) => {
      let result = false;
      const onOk = () => {
        if (numeric) {
          const n = Number(numberInput.value);
          const min = opts.input.min ?? -Infinity;
          const max = opts.input.max ?? Infinity;
          if (!Number.isInteger(n) || n < min || n > max) {
            numberInput.classList.add('is-invalid');
            numberInput.focus();
            return;
          }
          result = n;
          modal.hide();
          return;
        }
        if (opts.input && opts.input.required && !input.value.trim()) {
          input.classList.add('is-invalid');
          input.focus();
          return;
        }
        result = opts.input ? input.value.trim() : true;
        modal.hide();
      };
      const onHidden = () => {
        ok.removeEventListener('click', onOk);
        el.removeEventListener('hidden.bs.modal', onHidden);
        textInput.classList.remove('is-invalid');
        numberInput.classList.remove('is-invalid');
        resolve(result);
      };
      ok.addEventListener('click', onOk);
      el.addEventListener('hidden.bs.modal', onHidden);
      el.addEventListener('shown.bs.modal', () => (opts.input ? input : ok).focus(), { once: true });
      modal.show();
    });
  }

  window.confirmDialog = confirmDialog;

  // ---------------------------------------------------------------------
  // Language menu, fullscreen, logout
  // ---------------------------------------------------------------------
  document.addEventListener('click', (event) => {
    const langBtn = event.target.closest('[data-set-lang]');
    if (langBtn && typeof setLanguage === 'function') {
      event.preventDefault();
      setLanguage(langBtn.dataset.setLang);
      return;
    }

    if (event.target.closest('[data-action="fullscreen"]')) {
      event.preventDefault();
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen?.().catch(() => {});
      } else {
        document.exitFullscreen?.().catch(() => {});
      }
      return;
    }

    const logout = event.target.closest('[data-logout]');
    if (logout && 'caches' in window) {
      event.preventDefault();
      const target = logout.getAttribute('href');
      caches
        .keys()
        .then((keys) => Promise.all(keys.filter((k) => k.startsWith('celvass-pages')).map((k) => caches.delete(k))))
        .catch(() => {})
        .finally(() => {
          window.location.href = target;
        });
    }
  });

  window.toggleFullscreen = function () {
    document.querySelector('[data-action="fullscreen"]')?.click();
  };

  // Keep a body class while a fixed bottom bar is present so content is never hidden behind it.
  function syncBottomBars() {
    document.body.classList.toggle('has-tabbar', Boolean(document.querySelector('.app-tabbar')));
  }

  document.addEventListener('DOMContentLoaded', () => {
    syncBottomBars();
    initPanels();
  });
})();
