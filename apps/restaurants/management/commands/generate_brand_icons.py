from django.core.management.base import BaseCommand

from apps.restaurants.branding import write_default_icons


class Command(BaseCommand):
    help = "Render the default fork-and-knife app icons (favicon, PWA, maskable, apple-touch) into static/icons."

    def add_arguments(self, parser):
        parser.add_argument('--target', default=None, help='Output folder (defaults to static/icons).')

    def handle(self, *args, **options):
        written = write_default_icons(options.get('target'))
        self.stdout.write(self.style.SUCCESS('Wrote: ' + ', '.join(written)))
