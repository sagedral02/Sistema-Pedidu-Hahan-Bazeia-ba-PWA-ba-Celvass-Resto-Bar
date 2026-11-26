/**
 * Thank-you page after payment: star ratings for the dishes the table received, and a countdown back to
 * the home page that pauses as soon as the guest starts rating.
 */
(function () {
  'use strict';

  const state = { cfg: null, left: 0, timer: null, paused: false, busy: false };

  function paintStars(item) {
    const value = parseInt(item.dataset.stars, 10) || 0;
    item.querySelectorAll('.rate-star').forEach((btn) => {
      const on = Number(btn.dataset.value) <= value;
      btn.classList.toggle('is-on', on);
      btn.setAttribute('aria-checked', Number(btn.dataset.value) === value ? 'true' : 'false');
    });
  }

  function pause() {
    if (state.paused) return;
    state.paused = true;
    clearInterval(state.timer);
    const box = document.getElementById('thanks-countdown-box');
    if (box) box.classList.add('d-none');
  }

  function tick() {
    state.left -= 1;
    const out = document.getElementById('thanks-countdown');
    if (out) out.textContent = String(Math.max(0, state.left));
    if (state.left <= 0) {
      clearInterval(state.timer);
      window.location.replace(state.cfg.homeUrl || '/');
    }
  }

  async function submit() {
    if (state.busy) return;
    const ratings = Array.from(document.querySelectorAll('.rate-item'))
      .map((item) => ({
        menu_item_id: item.dataset.menuItem,
        stars: parseInt(item.dataset.stars, 10) || 0,
        comment: (item.querySelector('input')?.value || '').trim(),
      }))
      .filter((r) => r.stars > 0);
    if (!ratings.length) {
      showToast(t('thx_rate_pick'), 'warning');
      return;
    }
    state.busy = true;
    const btn = document.getElementById('rate-submit');
    if (btn) btn.disabled = true;
    try {
      await apiPost(state.cfg.ratingsUrl, { ratings });
      showToast(t('thx_rate_saved'), 'success');
      if (btn) btn.innerHTML = `<i class="fa-solid fa-check" aria-hidden="true"></i><span>${escapeHtml(t('thx_rate_saved'))}</span>`;
      setTimeout(() => window.location.replace(state.cfg.homeUrl || '/'), 2500);
    } catch (e) {
      showToast(e.message, 'error');
      if (btn) btn.disabled = false;
    } finally {
      state.busy = false;
    }
  }

  function init(cfg) {
    state.cfg = cfg;
    state.left = Number(cfg.countdown) || 25;
    document.querySelectorAll('.rate-item').forEach((item) => {
      paintStars(item);
      item.addEventListener('click', (e) => {
        const btn = e.target.closest('.rate-star');
        if (!btn) return;
        pause();
        item.dataset.stars = btn.dataset.value;
        paintStars(item);
      });
      item.querySelector('input')?.addEventListener('focus', pause);
    });
    state.timer = setInterval(tick, 1000);
  }

  window.Thanks = { init, pause, submit };
})();
