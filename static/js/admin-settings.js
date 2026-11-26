/**
 * Admin > Restaurant settings: identity and logo (also the app icon), contact, Google Maps location,
 * opening hours per weekday, tax and receipt note. One PATCH saves the form; the logo has its own endpoint.
 * Server values are written with .value / textContent or escaped.
 */
(function () {
  'use strict';

  const URL = '/api/v1/admin/restaurant/';
  const LOGO_URL = '/api/v1/admin/restaurant/logo/';
  const FIELDS = ['name', 'short_name', 'legal_name', 'description', 'phone', 'whatsapp', 'email', 'website',
    'instagram', 'facebook', 'address', 'maps_url', 'coordinates', 'tax_percentage', 'receipt_note', 'idle_alert_minutes'];
  const $ = (id) => document.getElementById(id);
  const form = $('settings-form');
  if (!form) return;

  let data = null;
  let loaded = false;

  function field(name) {
    return form.elements.namedItem(name);
  }

  function renderStatus() {
    const el = $('set-open-status');
    if (!el || !data) return;
    const st = data.opening_status || {};
    el.className = 'badge-soft ' + (st.is_open ? 'tone-success' : 'tone-danger');
    el.textContent = st.is_open ? t('set_open_now') : `${t('set_closed_now')}${st.next_opening_label ? ' · ' + t('set_opens_at') + ' ' + st.next_opening_label : ''}`;
  }

  function renderLogo() {
    const box = $('set-logo-preview');
    const remove = $('set-logo-remove');
    if (data.has_logo) {
      box.innerHTML = `<img src="${escapeHtml(data.brand.logo_png)}" alt="">`;
    } else {
      box.innerHTML = '<i class="fa-solid fa-utensils"></i>';
    }
    remove.hidden = !data.has_logo;
  }

  function renderContactActions() {
    const el = $('set-contact-actions');
    const items = [];
    if (data.whatsapp_link) items.push(`<a class="btn btn-sm btn-success" href="${escapeHtml(data.whatsapp_link)}" target="_blank" rel="noopener"><i class="fa-brands fa-whatsapp" aria-hidden="true"></i>WhatsApp</a>`);
    if (data.tel_link) items.push(`<a class="btn btn-sm btn-secondary-app" href="${escapeHtml(data.tel_link)}"><i class="fa-solid fa-phone" aria-hidden="true"></i>${escapeHtml(t('set_call'))}</a>`);
    el.innerHTML = items.join('');
  }

  function renderMap() {
    const wrap = $('set-map-wrap');
    const frame = $('set-map');
    const open = $('set-map-open');
    if (data.maps_embed_url) {
      if (frame.getAttribute('src') !== data.maps_embed_url) frame.setAttribute('src', data.maps_embed_url);
      wrap.hidden = false;
    } else {
      wrap.hidden = true;
      frame.removeAttribute('src');
    }
    open.hidden = !data.maps_link;
    if (data.maps_link) open.href = data.maps_link;
  }

  function renderHours() {
    const el = $('set-hours');
    el.innerHTML = data.opening_hours.map((h) => {
      const open = h.configured ? !h.is_closed : false;
      const id = `set-day-${h.weekday}`;
      return `
        <div class="settings-day" data-day="${h.weekday}">
          <div class="form-check form-switch m-0">
            <input class="form-check-input" type="checkbox" role="switch" id="${id}" ${open ? 'checked' : ''} onchange="AdminSettings.toggleDay(${h.weekday}, this.checked)">
            <label class="form-check-label fw-semibold" for="${id}">${escapeHtml(t('set_day_' + h.weekday))}</label>
          </div>
          <div class="settings-day-times"${open ? '' : ' hidden'}>
            <input type="time" class="form-control form-control-sm" data-role="opens" value="${escapeHtml(h.opens_at || '10:00')}" aria-label="${escapeHtml(t('set_opens'))}">
            <span class="text-tertiary" aria-hidden="true">-</span>
            <input type="time" class="form-control form-control-sm" data-role="closes" value="${escapeHtml(h.closes_at || '22:00')}" aria-label="${escapeHtml(t('set_closes'))}">
          </div>
          <span class="badge-soft settings-day-closed"${open ? ' hidden' : ''}>${escapeHtml(t('set_day_closed'))}</span>
        </div>`;
    }).join('') + `<div class="form-check mt-2"><input class="form-check-input" type="checkbox" id="set-hours-always" ${data.opening_hours.some((h) => h.configured) ? '' : 'checked'} onchange="AdminSettings.toggleAlways(this.checked)"><label class="form-check-label" for="set-hours-always">${escapeHtml(t('set_hours_always'))}</label></div>`;
    toggleAlways($('set-hours-always').checked);
  }

  function toggleDay(day, open) {
    const row = form.querySelector(`.settings-day[data-day="${day}"]`);
    row.querySelector('.settings-day-times').hidden = !open;
    row.querySelector('.settings-day-closed').hidden = open;
  }

  function toggleAlways(always) {
    form.querySelectorAll('.settings-day').forEach((row) => row.classList.toggle('is-disabled', always));
    form.querySelectorAll('.settings-day input').forEach((input) => { input.disabled = always; });
  }

  function fill() {
    FIELDS.forEach((name) => {
      const input = field(name);
      if (input) input.value = data[name] ?? '';
    });
    renderStatus();
    renderLogo();
    renderContactActions();
    renderMap();
    renderHours();
  }

  async function load() {
    try {
      data = await apiGet(URL);
      loaded = true;
      fill();
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  function collectHours() {
    if ($('set-hours-always').checked) return [];
    return Array.from(form.querySelectorAll('.settings-day')).map((row) => {
      const open = row.querySelector('input[type="checkbox"]').checked;
      return {
        weekday: Number(row.dataset.day),
        is_closed: !open,
        opens_at: open ? row.querySelector('[data-role="opens"]').value : null,
        closes_at: open ? row.querySelector('[data-role="closes"]').value : null,
      };
    });
  }

  async function save() {
    const name = field('name');
    name.classList.toggle('is-invalid', !name.value.trim());
    if (!name.value.trim()) {
      name.focus();
      return;
    }
    const body = { opening_hours: collectHours() };
    FIELDS.forEach((key) => {
      const input = field(key);
      if (input) body[key] = input.value.trim();
    });
    const btn = $('set-save');
    btn.disabled = true;
    try {
      data = await apiRequest(URL, { method: 'PATCH', body: JSON.stringify(body) });
      fill();
      showToast(t('set_saved'), 'success');
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  async function uploadLogo(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('logo', file);
    try {
      data = await apiRequest(LOGO_URL, { method: 'POST', body: fd });
      fill();
      showToast(t('set_logo_saved'), 'success');
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      input.value = '';
    }
  }

  async function removeLogo(btn) {
    const ok = await confirmDialog({ title: t('set_logo_remove'), message: t('set_logo_remove_msg'), confirmText: t('set_logo_remove'), tone: 'danger' });
    if (!ok) return;
    btn.disabled = true;
    try {
      data = await apiRequest(LOGO_URL, { method: 'DELETE' });
      fill();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  window.AdminSettings = { load, save, uploadLogo, removeLogo, toggleDay, toggleAlways };
  window.addEventListener('app:panel', (e) => {
    if (e.detail.id === 'settings-panel' && !loaded) load();
  });
  window.addEventListener('languageChanged', () => {
    if (data) fill();
  });
})();
