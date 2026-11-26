"""
Server-side labels for the Audit tab of the reports page (column headers, KPI names, system actor).

Merged into LABELS by labels.py. Headings inside the detail panel and the filter texts are page chrome and
live in static/js/i18n.js.
"""

AUDIT_LABELS = {
    'tet': {
        'rpt_col_action': 'Asaun',
        'rpt_col_actor': 'Atór',
        'rpt_col_entity': 'Entidade',
        'rpt_sum_events': 'Eventu',
        'rpt_sum_actors': 'Atór',
        'rpt_sum_entities': 'Tipu entidade',
        'rpt_audit_system': 'Sistema',
    },
    'pt': {
        'rpt_col_action': 'Ação',
        'rpt_col_actor': 'Autor',
        'rpt_col_entity': 'Entidade',
        'rpt_sum_events': 'Eventos',
        'rpt_sum_actors': 'Autores',
        'rpt_sum_entities': 'Tipos de entidade',
        'rpt_audit_system': 'Sistema',
    },
    'en': {
        'rpt_col_action': 'Action',
        'rpt_col_actor': 'Actor',
        'rpt_col_entity': 'Entity',
        'rpt_sum_events': 'Events',
        'rpt_sum_actors': 'Actors',
        'rpt_sum_entities': 'Entity types',
        'rpt_audit_system': 'System',
    },
    'id': {
        'rpt_col_action': 'Aksi',
        'rpt_col_actor': 'Pelaku',
        'rpt_col_entity': 'Entitas',
        'rpt_sum_events': 'Event',
        'rpt_sum_actors': 'Pelaku',
        'rpt_sum_entities': 'Jenis entitas',
        'rpt_audit_system': 'Sistem',
    },
}
