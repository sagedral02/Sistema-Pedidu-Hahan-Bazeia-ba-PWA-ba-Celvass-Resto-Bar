/**
 * API client (CSRF, error normalisation, i18n error codes) and toast notifications.
 */

function getCsrfToken() {
  const cookieValue = document.cookie
    .split('; ')
    .find((row) => row.startsWith('csrftoken='))
    ?.split('=')[1];
  return cookieValue || document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
}

const TOAST_ICONS = {
  success: 'fa-circle-check',
  error: 'fa-circle-exclamation',
  warning: 'fa-triangle-exclamation',
  info: 'fa-circle-info',
};

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const tone = TOAST_ICONS[type] ? type : 'info';
  const toastEl = document.createElement('div');
  toastEl.className = `app-toast tone-${tone}`;
  toastEl.setAttribute('role', tone === 'error' ? 'alert' : 'status');

  const icon = document.createElement('i');
  icon.className = `fa-solid ${TOAST_ICONS[tone]}`;
  icon.setAttribute('aria-hidden', 'true');

  const body = document.createElement('div');
  body.className = 'app-toast-body';
  body.textContent = String(message ?? '');

  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'btn-close';
  close.setAttribute('aria-label', typeof t === 'function' ? t('close') : 'Taka');

  const remove = () => {
    toastEl.classList.add('is-leaving');
    setTimeout(() => toastEl.remove(), 200);
  };
  close.addEventListener('click', remove);

  toastEl.append(icon, body, close);
  container.appendChild(toastEl);

  while (container.children.length > 4) container.firstElementChild.remove();
  setTimeout(remove, tone === 'error' ? 6000 : 4000);
}

async function apiRequest(endpoint, options = {}) {
  const isForm = options.body instanceof FormData;
  const headers = {
    ...(isForm ? {} : { 'Content-Type': 'application/json' }),
    'X-CSRFToken': getCsrfToken(),
    ...(options.headers || {}),
  };

  const response = await fetch(endpoint, { credentials: 'same-origin', ...options, headers });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    const err = new Error(typeof t === 'function' ? t('error_request_failed') : 'Pedidu falla atu prosesa.');
    err.status = response.status;
    throw err;
  }

  if (!response.ok || data.success === false) {
    const code = data?.error?.code;
    const translated = typeof t === 'function' && code && t(code) !== code ? t(code) : null;
    const message = data?.error?.message || translated || (typeof t === 'function' ? t('error_request_failed') : 'Pedidu falla atu prosesa.');
    // Prefer the server message for business validation, translated text for generic codes.
    const preferTranslated = ['DEVICE_LOCKED', 'SERVER_ERROR', 'TABLE_SESSION_NOT_OPEN', 'IDEMPOTENCY_KEY_REQUIRED'].includes(code);
    const err = new Error(preferTranslated && translated ? translated : message);
    err.code = code;
    err.details = data?.error?.details || {};
    err.status = response.status;
    throw err;
  }
  return data.data !== undefined ? data.data : data;
}

async function apiGet(endpoint) {
  return apiRequest(endpoint, { method: 'GET' });
}

async function apiPost(endpoint, body = {}, headers = {}) {
  return apiRequest(endpoint, {
    method: 'POST',
    body: JSON.stringify(body),
    headers,
  });
}
