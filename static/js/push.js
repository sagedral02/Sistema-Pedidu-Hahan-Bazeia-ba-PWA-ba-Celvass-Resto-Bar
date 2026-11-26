/**
 * Web Push opt-in (window.PushClient).
 *
 * Buttons with [data-push-enable="staff"] or [data-push-enable="guest"] ask for permission only when pressed.
 * Guest buttons also carry data-qr-token and, once the table is open, data-session-token.
 * When permission was already given, the subscription is refreshed silently (for example to move a guest
 * from "waiting for the table" to the opened session). Unsupported browsers simply do not see the button.
 * iPhone: push only works for the app added to the Home Screen (iOS 16.4+).
 */
(function () {
  'use strict';

  const tr = (key) => (typeof t === 'function' ? t(key) : key);
  let configPromise = null;

  function supported() {
    return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && window.isSecureContext;
  }

  function config() {
    if (!configPromise) {
      configPromise = fetch('/api/v1/push/config/', { credentials: 'same-origin' })
        .then((r) => r.json())
        .then((j) => (j && j.data) || { enabled: false })
        .catch(() => ({ enabled: false }));
    }
    return configPromise;
  }

  function keyBytes(base64) {
    const padded = (base64 + '='.repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(padded);
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function currentSubscription(cfg) {
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(cfg.public_key) });
    }
    return sub;
  }

  async function register(btn, sub) {
    const kind = btn.dataset.pushEnable;
    const body = { subscription: sub.toJSON(), lang: localStorage.getItem('celvas_lang') || 'tet' };
    let url = '/api/v1/staff/push/subscribe/';
    if (kind === 'guest') {
      url = '/api/v1/public/push/subscribe/';
      body.qr_token = btn.dataset.qrToken || '';
      if (btn.dataset.sessionToken) body.session_token = btn.dataset.sessionToken;
    }
    await apiPost(url, body);
  }

  function paint(btn, state) {
    btn.dataset.pushState = state;
    const label = btn.querySelector('[data-push-label]') || btn;
    if (state === 'on') {
      label.textContent = tr('push_on');
      btn.disabled = true;
    } else if (state === 'blocked') {
      label.textContent = tr('push_blocked');
      btn.disabled = true;
    } else {
      label.textContent = tr('push_enable');
      btn.disabled = false;
    }
  }

  async function enable(btn) {
    const cfg = await config();
    if (!cfg.enabled) return;
    try {
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') {
        paint(btn, permission === 'denied' ? 'blocked' : 'off');
        return;
      }
      const sub = await currentSubscription(cfg);
      await register(btn, sub);
      paint(btn, 'on');
      if (typeof showToast === 'function') showToast(tr('push_enabled_toast'), 'success');
    } catch (e) {
      paint(btn, 'off');
      if (typeof showToast === 'function') showToast(e.message || tr('push_failed'), 'error');
    }
  }

  async function init() {
    const buttons = Array.from(document.querySelectorAll('[data-push-enable]'));
    if (!buttons.length) return;
    if (!supported()) {
      buttons.forEach((b) => (b.hidden = true));
      return;
    }
    const cfg = await config();
    if (!cfg.enabled) {
      buttons.forEach((b) => (b.hidden = true));
      return;
    }
    for (const btn of buttons) {
      btn.hidden = false;
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        enable(btn);
      });
      if (Notification.permission === 'denied') {
        paint(btn, 'blocked');
      } else if (Notification.permission === 'granted') {
        try {
          await register(btn, await currentSubscription(cfg));
          paint(btn, 'on');
        } catch {
          paint(btn, 'off');
        }
      } else {
        paint(btn, 'off');
      }
    }
  }

  window.addEventListener('languageChanged', () => {
    document.querySelectorAll('[data-push-enable]').forEach((b) => paint(b, b.dataset.pushState || 'off'));
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  window.PushClient = { enable, supported };
})();
