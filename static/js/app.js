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
      const sameId = el.dataset.panel === id;
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
   * confirmDialog({ title, message, confirmText, cancelText, tone: 'danger'|'primary', input: { label, required } })
   * Resolves to false when cancelled, true (or the input text when `input` is set) when confirmed.
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
    const input = el.querySelector('#appConfirmInput');
    inputWrap.hidden = !opts.input;
    input.value = '';
    if (opts.input) el.querySelector('#appConfirmInputLabel').textContent = opts.input.label || '';

    const modal = bootstrap.Modal.getOrCreateInstance(el);
    return new Promise((resolve) => {
      let result = false;
      const onOk = () => {
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
        input.classList.remove('is-invalid');
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
