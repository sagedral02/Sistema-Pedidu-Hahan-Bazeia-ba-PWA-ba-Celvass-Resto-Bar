"""Push the idle-table alert even when no cashier screen is open (run it from cron, e.g. every 5 minutes)."""
from django.core.management.base import BaseCommand

from apps.api.serializers import RestaurantTableSerializer
from apps.tables.progress import idle_minutes_setting, raise_idle_alerts
from apps.tables.selectors import list_tables_with_status


class Command(BaseCommand):
    help = "Flag unpaid tables without activity past the admin limit and notify the cashiers (once per session)."

    def handle(self, *args, **options):
        rows = RestaurantTableSerializer(list_tables_with_status(), many=True,
                                         context={'idle_minutes': idle_minutes_setting()}).data
        count = raise_idle_alerts(rows)
        self.stdout.write(f"{count} new idle table alert(s).")
