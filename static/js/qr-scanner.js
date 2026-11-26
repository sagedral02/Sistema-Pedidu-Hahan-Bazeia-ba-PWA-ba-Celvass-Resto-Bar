/**
 * In-app QR scanner for table codes (window.QrScanner.open()).
 *
 * Uses the native BarcodeDetector when the browser has it, otherwise loads the self-hosted jsQR
 * (static/vendor/jsqr) on demand. Only links to this site of the form /t/<token>/ are accepted, so a
 * scanned code can never send the guest to another website. The camera is stopped when the dialog closes.
 */
(function () {
  'use strict';

  const JSQR_URL = '/static/vendor/jsqr/jsQR.js';
  const TABLE_PATH = /^\/t\/([A-Za-z0-9_-]{8,128})\/?$/;
  const tr = (key) => (typeof t === 'function' ? t(key) : key);

  let modalEl = null;
  let stream = null;
  let running = false;
  let detector = null;
  let jsqrPromise = null;
  let canvas = null;
  let runId = 0;

  /** Returns the same-origin table path for a scanned text / pasted link, or null. */
  function tableUrl(text) {
    const raw = String(text || '').trim();
    if (!raw) return null;
    let url;
    try {
      url = new URL(raw, window.location.origin);
    } catch {
      return null;
    }
    if (url.origin !== window.location.origin) return null;
    const m = TABLE_PATH.exec(url.pathname);
    return m ? `/t/${m[1]}/` : null;
  }

  function ensureModal() {
    if (modalEl) return modalEl;
    const wrap = document.createElement('div');
    wrap.innerHTML = `
      <div class="modal fade modal-sheet" id="qrScanModal" tabindex="-1" aria-labelledby="qrScanTitle" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered" style="max-width:440px">
          <div class="modal-content">
            <div class="modal-header">
              <h2 class="modal-title" id="qrScanTitle"></h2>
              <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
              <div class="qr-scan-view" id="qrScanView">
                <video id="qrScanVideo" playsinline muted></video>
                <span class="qr-scan-frame" aria-hidden="true"></span>
              </div>
              <p class="fs-7 text-tertiary mt-2 mb-0" id="qrScanStatus" role="status" aria-live="polite"></p>
              <form class="mt-3" id="qrScanManual" novalidate>
                <label class="form-label fs-7" for="qrScanInput" id="qrScanManualLabel"></label>
                <div class="input-group">
                  <input type="url" class="form-control" id="qrScanInput" inputmode="url" autocomplete="off" placeholder="https://…/t/…">
                  <button type="submit" class="btn btn-primary" id="qrScanGo"></button>
                </div>
              </form>
            </div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(wrap.firstElementChild);
    modalEl = document.getElementById('qrScanModal');
    modalEl.addEventListener('hidden.bs.modal', stop);
    modalEl.querySelector('#qrScanManual').addEventListener('submit', (e) => {
      e.preventDefault();
      accept(modalEl.querySelector('#qrScanInput').value, true);
    });
    return modalEl;
  }

  function setStatus(key, tone) {
    const el = modalEl.querySelector('#qrScanStatus');
    el.textContent = tr(key);
    el.className = `fs-7 mt-2 mb-0 ${tone === 'error' ? 'text-danger' : 'text-tertiary'}`;
  }

  function texts() {
    modalEl.querySelector('#qrScanTitle').textContent = tr('qr_scan_title');
    modalEl.querySelector('.btn-close').setAttribute('aria-label', tr('cancel'));
    modalEl.querySelector('#qrScanManualLabel').textContent = tr('qr_scan_manual');
    modalEl.querySelector('#qrScanGo').textContent = tr('qr_scan_go');
  }

  function accept(text, manual) {
    const path = tableUrl(text);
    if (!path) {
      setStatus('qr_scan_not_table', 'error');
      if (manual) modalEl.querySelector('#qrScanInput').classList.add('is-invalid');
      return false;
    }
    stop();
    setStatus('qr_scan_opening');
    window.location.href = path;
    return true;
  }

  function loadJsQr() {
    if (window.jsQR) return Promise.resolve(window.jsQR);
    if (!jsqrPromise) {
      jsqrPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = JSQR_URL;
        s.onload = () => (window.jsQR ? resolve(window.jsQR) : reject(new Error('jsQR')));
        s.onerror = reject;
        document.head.appendChild(s);
      });
    }
    return jsqrPromise;
  }

  async function makeDetector() {
    if ('BarcodeDetector' in window) {
      try {
        const formats = await window.BarcodeDetector.getSupportedFormats();
        if (formats.includes('qr_code')) {
          const native = new window.BarcodeDetector({ formats: ['qr_code'] });
          return async (video) => {
            const codes = await native.detect(video);
            return codes.length ? codes[0].rawValue : null;
          };
        }
      } catch {
        /* fall back to jsQR */
      }
    }
    const jsQR = await loadJsQr();
    canvas = canvas || document.createElement('canvas');
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    return async (video) => {
      const w = video.videoWidth;
      const h = video.videoHeight;
      if (!w || !h) return null;
      const scale = Math.min(1, 640 / Math.max(w, h));
      canvas.width = Math.round(w * scale);
      canvas.height = Math.round(h * scale);
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const code = jsQR(img.data, img.width, img.height, { inversionAttempts: 'dontInvert' });
      return code ? code.data : null;
    };
  }

  async function loop(video) {
    if (!running) return;
    try {
      const text = await detector(video);
      if (text && accept(text, false)) return;
    } catch {
      /* keep scanning */
    }
    if (running) setTimeout(() => loop(video), 250);
  }

  async function start() {
    const myRun = runId;
    const video = modalEl.querySelector('#qrScanVideo');
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.isSecureContext) {
      modalEl.querySelector('#qrScanView').hidden = true;
      setStatus('qr_scan_no_camera', 'error');
      return;
    }
    modalEl.querySelector('#qrScanView').hidden = false;
    setStatus('qr_scan_starting');
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
    } catch (e) {
      modalEl.querySelector('#qrScanView').hidden = true;
      setStatus(e && e.name === 'NotAllowedError' ? 'qr_scan_denied' : 'qr_scan_no_camera', 'error');
      return;
    }
    if (myRun !== runId) {
      // The dialog was closed while the browser asked for the camera permission.
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
      return;
    }
    video.srcObject = stream;
    try {
      await video.play();
    } catch {
      /* autoplay policies: the stream still renders on most devices */
    }
    try {
      detector = await makeDetector();
    } catch {
      setStatus('qr_scan_no_camera', 'error');
      return;
    }
    running = true;
    setStatus('qr_scan_point');
    loop(video);
  }

  function stop() {
    runId += 1;
    running = false;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    if (modalEl) {
      const video = modalEl.querySelector('#qrScanVideo');
      if (video) video.srcObject = null;
    }
  }

  function open() {
    ensureModal();
    texts();
    const input = modalEl.querySelector('#qrScanInput');
    input.value = '';
    input.classList.remove('is-invalid');
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
    start();
  }

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-qr-scan]');
    if (!trigger) return;
    event.preventDefault();
    open();
  });

  window.QrScanner = { open, stop, tableUrl };
})();
