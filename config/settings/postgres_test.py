from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

from .base import database_from_url
from .test import *  # noqa: F403

url = os.environ.get("WORKLEDGER_TEST_DATABASE_URL")
if not url:
    raise ImproperlyConfigured("WORKLEDGER_TEST_DATABASE_URL is required")
DATABASES = {"default": database_from_url(url)}
DATABASES["default"]["CONN_MAX_AGE"] = 0
