import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

if not os.environ.get("WORKLEDGER_SECRET_KEY") or os.environ["WORKLEDGER_SECRET_KEY"].startswith(
    "CHANGE_ME"
):
    raise ImproperlyConfigured("WORKLEDGER_SECRET_KEY must be set to a non-placeholder value")
if not os.environ.get("DATABASE_URL"):
    raise ImproperlyConfigured("DATABASE_URL must be set")

SECURE_SSL_REDIRECT = os.environ.get("WORKLEDGER_SSL_REDIRECT", "false").lower() == "true"
