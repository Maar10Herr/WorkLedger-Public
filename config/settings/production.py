import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

secret_key = os.environ.get("WORKLEDGER_SECRET_KEY", "")
if (
    not secret_key
    or secret_key.startswith("CHANGE_ME")
    or len(secret_key) < 50
    or len(set(secret_key)) < 5
):
    raise ImproperlyConfigured("WORKLEDGER_SECRET_KEY must be set to a non-placeholder value")
if not os.environ.get("DATABASE_URL"):
    raise ImproperlyConfigured("DATABASE_URL must be set")

if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("WORKLEDGER_ALLOWED_HOSTS must contain at least one host")
if "*" in ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("WORKLEDGER_ALLOWED_HOSTS must not contain '*'")
if "*" in CSRF_TRUSTED_ORIGINS:  # noqa: F405
    raise ImproperlyConfigured("WORKLEDGER_CSRF_TRUSTED_ORIGINS must not contain '*'")

SECURE_SSL_REDIRECT = env_bool("WORKLEDGER_SSL_REDIRECT", False)  # noqa: F405
if SECURE_SSL_REDIRECT and not SESSION_COOKIE_SECURE:  # noqa: F405
    raise ImproperlyConfigured(
        "WORKLEDGER_SECURE_COOKIES must be true when WORKLEDGER_SSL_REDIRECT is true"
    )
SECURE_HSTS_SECONDS = (
    env_int("WORKLEDGER_HSTS_SECONDS", 31_536_000, minimum=1)  # noqa: F405
    if SECURE_SSL_REDIRECT
    else 0
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = (
    env_bool("WORKLEDGER_HSTS_INCLUDE_SUBDOMAINS", False)  # noqa: F405
    if SECURE_SSL_REDIRECT
    else False
)
SECURE_HSTS_PRELOAD = (
    env_bool("WORKLEDGER_HSTS_PRELOAD", False)  # noqa: F405
    if SECURE_SSL_REDIRECT
    else False
)
