#!/bin/sh
set -eu

case "${1:-web}" in
  web)
    DATABASE_URL="${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL is required}" \
      python manage.py migrate --noinput --settings=config.settings.production
    python manage.py collectstatic --noinput --settings=config.settings.production
    exec gunicorn config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-2}" \
      --threads "${GUNICORN_THREADS:-2}" \
      --timeout "${GUNICORN_TIMEOUT:-120}" \
      --access-logfile - \
      --error-logfile -
    ;;
  worker)
    exec celery -A config worker \
      --loglevel "${CELERY_LOG_LEVEL:-INFO}" \
      --concurrency "${CELERY_WORKER_CONCURRENCY:-2}"
    ;;
  *) exec "$@" ;;
esac
