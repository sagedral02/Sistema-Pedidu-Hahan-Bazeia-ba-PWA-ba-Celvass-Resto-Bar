"""Print a new VAPID key pair for Web Push (put the values in the environment / .env)."""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


class Command(BaseCommand):
    help = "Generate a VAPID key pair (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY) for push notifications."

    def handle(self, *args, **options):
        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_numbers().private_value.to_bytes(32, 'big')
        public = key.public_key().public_bytes(serialization.Encoding.X962,
                                                serialization.PublicFormat.UncompressedPoint)
        self.stdout.write(f"VAPID_PUBLIC_KEY={_b64(public)}")
        self.stdout.write(f"VAPID_PRIVATE_KEY={_b64(private)}")
        self.stdout.write("VAPID_SUBJECT=mailto:admin@celvass.tl")
        self.stdout.write(self.style.WARNING("Keep the private key secret. Changing keys invalidates existing subscriptions."))
