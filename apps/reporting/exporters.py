"""
Rendering of ReportData for the page (JSON), CSV and PDF.

All three go through `build_display`, which translates labels and formats values once.
"""
import csv
import io
from datetime import datetime
from decimal import Decimal
from itertools import islice
from typing import Any, Dict, Iterator, Optional

from django.template.loader import render_to_string
from django.utils import timezone

from .labels import label, normalize_lang
from .reports import Label, Period, ReportData

PDF_MAX_ROWS = 1500
# Landscape keeps wide reports (7 columns) readable on paper.
PDF_LANDSCAPE_FROM_COLUMNS = 6
_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _neutralize(text: str) -> str:
    """Stop spreadsheet apps from running a user-entered value as a formula (CSV injection)."""
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def format_period(period: Period) -> str:
    start = period.start.strftime('%d/%m/%Y')
    if period.start == period.end:
        return start
    return f"{start} - {period.end.strftime('%d/%m/%Y')}"


def render_cell(value: Any, kind: str, lang: str, target: str = 'display') -> str:
    """
    Format one value. `target` is 'display' (page and PDF, human friendly) or 'csv'
    (machine friendly: ISO dates, plain numbers, no currency symbol).
    """
    if value is None:
        return ''
    if isinstance(value, Label):
        return label(lang, value.key)
    if isinstance(value, str) and kind in ('money', 'percent', 'int', 'decimal'):
        return value  # placeholder such as '-' (e.g. a shift that is still open)

    csv_target = target == 'csv'
    if kind == 'decimal':
        return f'{Decimal(value).quantize(Decimal("0.1")):.1f}'
    if kind == 'money':
        amount = Decimal(value).quantize(Decimal('0.01'))
        return f'{amount:.2f}' if csv_target else f'${amount:,.2f}'
    if kind == 'percent':
        pct = Decimal(value).quantize(Decimal('0.1'))
        return f'{pct:.1f}' if csv_target else f'{pct:.1f}%'
    if kind == 'int':
        return str(int(value)) if csv_target else f'{int(value):,}'
    if kind == 'date':
        if isinstance(value, datetime):
            value = timezone.localtime(value).date()
        return value.isoformat() if csv_target else value.strftime('%d/%m/%Y')
    if kind == 'datetime':
        if isinstance(value, datetime) and timezone.is_aware(value):
            value = timezone.localtime(value)
        fmt = '%Y-%m-%d %H:%M' if csv_target else '%d/%m/%Y %H:%M'
        return value.strftime(fmt)

    text = str(value)
    return _neutralize(text) if csv_target else text


def build_display(report: ReportData, lang: str, target: str = 'display', limit: Optional[int] = None) -> Dict[str, Any]:
    """
    Translate and format a report. With `limit`, only the first `limit` rows are rendered and
    `truncated` tells whether more were available (used by the PDF, which cannot print unbounded lists).
    """
    lang = normalize_lang(lang)
    source = iter(report.rows) if limit is None else islice(report.rows, limit + 1)
    rows = [[render_cell(v, c.kind, lang, target) for v, c in zip(row, report.columns)] for row in source]
    truncated = limit is not None and len(rows) > limit
    if truncated:
        rows = rows[:limit]
    data = {
        'type': report.report_type,
        'title': label(lang, report.title_key),
        'period_label': format_period(report.period),
        'scope_own': report.scoped_to_user,
        'filters_text': report.filters_text,
        'summary': [
            {'label': label(lang, s.label_key), 'value': render_cell(s.value, s.kind, lang, target)}
            for s in report.summary
        ],
        'columns': [{'label': label(lang, c.label_key), 'align': c.align} for c in report.columns],
        'rows': rows,
        'truncated': truncated,
    }
    if report.drills is not None:
        data['drills'] = report.drills
    return data


def export_filename(report: ReportData, extension: str) -> str:
    p = report.period
    return f'laporan-{report.report_type}-{p.start.isoformat()}_{p.end.isoformat()}.{extension}'


def to_csv_bytes(report: ReportData, lang: str) -> bytes:
    """UTF-8 with BOM so Excel opens accented characters correctly."""
    data = build_display(report, lang, target='csv')
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow([c['label'] for c in data['columns']])
    writer.writerows(data['rows'])
    return buffer.getvalue().encode('utf-8-sig')


def iter_csv_chunks(report: ReportData, lang: str, batch: int = 200) -> Iterator[bytes]:
    """
    CSV as UTF-8 with BOM, produced in small chunks so exports of any size stream from the database
    without being held in memory. Values use the machine-friendly format (ISO dates, plain numbers).
    """
    lang = normalize_lang(lang)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow([label(lang, c.label_key) for c in report.columns])
    pending = 0
    first = True

    def flush() -> bytes:
        nonlocal first
        chunk = buffer.getvalue().encode('utf-8')
        buffer.seek(0)
        buffer.truncate(0)
        if first:
            first = False
            return b'\xef\xbb\xbf' + chunk
        return chunk

    for row in report.rows:
        writer.writerow([render_cell(v, c.kind, lang, 'csv') for v, c in zip(row, report.columns)])
        pending += 1
        if pending >= batch:
            yield flush()
            pending = 0
    yield flush()


def to_pdf_bytes(report: ReportData, lang: str, restaurant_name: str, printed_by: str,
                 printed_at: datetime = None) -> bytes:
    from xhtml2pdf import pisa

    lang = normalize_lang(lang)
    data = build_display(report, lang, target='display', limit=PDF_MAX_ROWS)
    truncated = data['truncated']
    landscape = len(report.columns) >= PDF_LANDSCAPE_FROM_COLUMNS
    printed_at = timezone.localtime(printed_at or timezone.now())
    ends = [c['align'] == 'end' for c in data['columns']]
    pdf_rows = [
        [{'text': text, 'end': end} for text, end in zip(row, ends)]
        for row in data['rows']
    ]

    html = render_to_string('reports/pdf.html', {
        'restaurant_name': restaurant_name,
        'title': data['title'],
        'period_label': data['period_label'],
        'summary': data['summary'],
        'columns': data['columns'],
        'rows': pdf_rows,
        'scope_own': data['scope_own'],
        'filters_text': data['filters_text'],
        'truncated_note': label(lang, 'rpt_pdf_truncated', limit=PDF_MAX_ROWS) if truncated else '',
        'printed_at': printed_at.strftime('%d/%m/%Y %H:%M'),
        'printed_by': printed_by,
        'landscape': landscape,
        'labels': {
            'period': label(lang, 'rpt_pdf_period'),
            'printed_at': label(lang, 'rpt_pdf_printed_at'),
            'printed_by': label(lang, 'rpt_pdf_printed_by'),
            'page': label(lang, 'rpt_pdf_page'),
            'of': label(lang, 'rpt_pdf_of'),
            'no_data': label(lang, 'rpt_pdf_no_data'),
            'scope_own': label(lang, 'rpt_pdf_scope_own'),
            'report': label(lang, 'rpt_pdf_report'),
            'filters': label(lang, 'rpt_pdf_filters'),
        },
    })

    output = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=output, encoding='utf-8')
    if result.err:
        raise RuntimeError('PDF generation failed')
    return output.getvalue()
