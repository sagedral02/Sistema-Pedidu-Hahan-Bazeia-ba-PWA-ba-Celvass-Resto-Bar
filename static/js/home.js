/**
 * Home page (read-only menu): category chips, search and the highlight rows that follow the chip.
 * Ordering happens on the table page after scanning the table QR code.
 */
(function () {
  'use strict';

  let active = 'all';
  const search = document.getElementById('menu-search');
  const empty = document.getElementById('menu-empty');

  function apply() {
    const q = (search?.value || '').toLowerCase().trim();
    document.querySelectorAll('.menu-highlights[data-highlight-for]').forEach((box) => {
      box.classList.toggle('d-none', Boolean(q) || box.dataset.highlightFor !== active);
    });
    let visible = 0;
    document.querySelectorAll('.menu-item-col').forEach((col) => {
      const show = (active === 'all' || col.dataset.category === active)
        && (!q || (col.dataset.name || '').includes(q) || (col.dataset.desc || '').includes(q));
      col.hidden = !show;
      if (show) visible += 1;
    });
    if (empty) empty.hidden = visible > 0 || !document.querySelector('.menu-item-col');
  }

  document.querySelectorAll('.chip[data-category]').forEach((chip) => {
    chip.addEventListener('click', () => {
      active = chip.dataset.category || 'all';
      document.querySelectorAll('.chip[data-category]').forEach((c) => {
        const on = c.dataset.category === active;
        c.classList.toggle('active', on);
        c.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      apply();
    });
  });
  if (search) search.addEventListener('input', apply);
})();
