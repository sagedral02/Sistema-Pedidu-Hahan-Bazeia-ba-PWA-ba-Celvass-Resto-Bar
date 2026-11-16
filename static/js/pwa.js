/**
 * Service worker registration and install prompt.
 * The install banner is shown only when the browser offers installation (or on iOS Safari,
 * which needs manual steps) and the user has not dismissed it in the last 7 days.
 */
(function () {
  'use strict';

  const DISMISS_KEY = 'celvass_pwa_dismissed';
  const DISMISS_MS = 7 * 24 * 3600 * 1000;
  let deferredPrompt = null;

  const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  const recentlyDismissed = () => {
    const ts = parseInt(localStorage.getItem(DISMISS_KEY) || '0', 10);
    return Date.now() - ts < DISMISS_MS;
  };
  const isStaffPage = () => ['admin', 'cashier', 'kitchen'].includes(document.body?.dataset.role);

  function banner() {
    return document.getElementById('pwa-install-banner');
  }

  function showBanner() {
    if (isStandalone() || recentlyDismissed() || isStaffPage()) return;
    banner()?.classList.add('is-visible');
  }

  function hideBanner() {
    banner()?.classList.remove('is-visible');
  }

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((err) => console.warn('SW registration failed', err));
    });
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    showBanner();
  });

  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    hideBanner();
  });

  document.addEventListener('DOMContentLoaded', () => {
    if (isIOS() && !isStandalone()) setTimeout(showBanner, 1500);
  });

  function dismissPwaBanner() {
    localStorage.setItem(DISMISS_KEY, String(Date.now()));
    hideBanner();
  }

  function installPWA() {
    if (deferredPrompt) {
      deferredPrompt.prompt();
      deferredPrompt.userChoice.finally(() => {
        deferredPrompt = null;
        hideBanner();
      });
      return;
    }
    showGuide();
  }

  function showGuide() {
    let el = document.getElementById('pwaGuideModal');
    if (!el) {
      const tr = (k, f) => (typeof t === 'function' && t(k) !== k ? t(k) : f);
      const steps = isIOS()
        ? [tr('pwa_ios_step1', 'Hanehan butaun Share iha Safari.'), tr('pwa_ios_step2', 'Hili "Add to Home Screen".')]
        : [tr('pwa_android_step1', 'Loke menu browser (⋮).'), tr('pwa_android_step2', 'Hili "Install app" ka "Add to Home screen".')];
      const wrap = document.createElement('div');
      wrap.innerHTML = `
        <div class="modal fade modal-sheet" id="pwaGuideModal" tabindex="-1" aria-labelledby="pwaGuideTitle" aria-hidden="true">
          <div class="modal-dialog modal-dialog-centered" style="max-width:400px">
            <div class="modal-content">
              <div class="modal-body p-4">
                <h2 class="modal-title mb-3" id="pwaGuideTitle"></h2>
                <ol class="steps mb-0"></ol>
              </div>
              <div class="modal-footer"><button type="button" class="btn btn-primary w-100" data-bs-dismiss="modal"></button></div>
            </div>
          </div>
        </div>`;
      document.body.appendChild(wrap.firstElementChild);
      el = document.getElementById('pwaGuideModal');
      el.querySelector('#pwaGuideTitle').textContent = tr('install_pwa_text', 'Instala aplikasaun');
      el.querySelector('.modal-footer .btn').textContent = tr('understood', 'Entende ona');
      const list = el.querySelector('ol');
      steps.forEach((s) => {
        const li = document.createElement('li');
        li.textContent = s;
        list.appendChild(li);
      });
    }
    bootstrap.Modal.getOrCreateInstance(el).show();
  }

  window.installPWA = installPWA;
  window.dismissPwaBanner = dismissPwaBanner;
})();
