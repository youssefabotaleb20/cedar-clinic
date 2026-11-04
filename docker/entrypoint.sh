#!/bin/sh
# Default container command. Compose and Kubernetes run migrations and
# reminders as separate services/jobs, so both extras are off by default.
# Single-container hosts (Hugging Face Spaces) switch them on with env vars.
set -e

if [ "${RUN_MIGRATIONS_ON_START:-0}" = "1" ]; then
  python manage.py migrate --noinput
  python manage.py seed_demo
fi

if [ "${RUN_REMINDER_LOOP:-0}" = "1" ]; then
  (while true; do python manage.py send_reminders || true; sleep "${REMINDER_INTERVAL_SECONDS:-600}"; done) &
fi

exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-3}" \
  --access-logfile -
