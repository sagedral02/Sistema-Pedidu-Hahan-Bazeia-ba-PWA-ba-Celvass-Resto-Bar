/**
 * Shared receipt rendering and printing (80 mm thermal paper).
 * Used by the cashier POS (cashier.js) and by the Riwayat detail panel (reports-history.js).
 *
 *   Receipt.html(payment)            -> receipt markup (all values escaped)
 *   Receipt.printHtml(html, title)   -> opens a print window with the receipt styles
 *   Receipt.print(payment)           -> html() + printHtml()
 *
 * `payment` has the shape returned by /api/v1/cashier/table-sessions/<id>/payments/:
 *   { payment_code, amount, tendered_amount, change_amount, paid_at, table_name, table_code, items[] }
 *
 * Receipt.printer can be replaced (tests do) to intercept the actual print call.
 */
(function () {
  'use strict';

  const esc = (value) => (typeof window.escapeHtml === 'function' ? window.escapeHtml(value) : String(value ?? ''));
  const money = (value) => (typeof window.formatMoney === 'function' ? window.formatMoney(value) : '$' + value);
  const tr = (key) => (typeof window.t === 'function' ? window.t(key) : key);

  function html(payment) {
    const r = window.CELVASS_RESTAURANT || {};
    const rows = (payment.items || [])
      .map(
        (i) => `
        <tr>
          <td>${esc(i.name)}${i.note ? `<br><small>* ${esc(i.note)}</small>` : ''}</td>
          <td style="text-align:center">${esc(i.quantity)}</td>
          <td style="text-align:right">${money(i.subtotal)}</td>
        </tr>`
      )
      .join('');
    const paidAt = payment.paid_at ? new Date(payment.paid_at).toLocaleString() : '';
    return `
      <div class="receipt-paper" id="printable-receipt">
        <div style="text-align:center">
          <strong style="font-size:14px">${esc((r.name || 'Celvass Resto & Bar').toUpperCase())}</strong><br>
          ${r.address ? `<small>${esc(r.address)}</small><br>` : ''}
          ${r.phone ? `<small>Tel: ${esc(r.phone)}</small>` : ''}
        </div>
        <div style="border-top:1px dashed #000;border-bottom:1px dashed #000;margin:8px 0;padding:4px 0">
          <div>${esc(tr('th_table'))}: ${esc(payment.table_name || payment.table_code || '-')}</div>
          <div>${esc(tr('th_bill_code'))}: ${esc(payment.payment_code)}</div>
          <div>${esc(tr('date_time'))}: ${esc(paidAt)}</div>
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead><tr style="border-bottom:1px dashed #000"><th style="text-align:left">Item</th><th>Qtd</th><th style="text-align:right">${esc(tr('subtotal'))}</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div style="border-top:1px dashed #000;margin-top:6px;padding-top:4px">
          <div style="display:flex;justify-content:space-between;font-weight:bold"><span>${esc(tr('total').toUpperCase())}</span><span>${money(payment.amount)}</span></div>
          <div style="display:flex;justify-content:space-between"><span>${esc(tr('tendered_short'))}</span><span>${money(payment.tendered_amount)}</span></div>
          <div style="display:flex;justify-content:space-between;font-weight:bold"><span>${esc(tr('change_label'))}</span><span>${money(payment.change_amount)}</span></div>
        </div>
        <div style="text-align:center;margin-top:10px"><small>${esc(tr('receipt_thanks'))}</small></div>
      </div>`;
  }

  const Receipt = {
    html,

    /** Default printer: print the popup, then close it. Replaceable for tests. */
    printer(win) {
      win.focus();
      win.print();
      setTimeout(() => win.close(), 500);
    },

    printHtml(innerHtml, title) {
      const w = window.open('', '_blank', 'width=380,height=640');
      if (!w) {
        if (typeof window.showToast === 'function') window.showToast(tr('popup_blocked'), 'warning');
        return false;
      }
      w.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>${esc(title)}</title>
      <style>
        @page { size: 80mm auto; margin: 0; }
        html, body { margin: 0; padding: 6px; width: 76mm; font-family: 'Courier New', monospace; font-size: 11px; line-height: 1.3; color: #000; background: #fff; }
        .receipt-paper { border: 0 !important; padding: 0 !important; max-width: none !important; }
        table { width: 100%; border-collapse: collapse; } td, th { padding: 2px 0; vertical-align: top; }
      </style></head><body>${innerHtml}</body></html>`);
      w.document.close();
      w.onload = () => Receipt.printer(w);
      return true;
    },

    print(payment) {
      return Receipt.printHtml(html(payment), tr('receipt_title'));
    },
  };

  window.Receipt = Receipt;
})();
