#!/usr/bin/env bash
# =============================================================================
# One entrypoint, several roles. `command: worker` in compose picks the role.
# Keeping them in one image guarantees web and worker run identical code — a
# whole class of "works on the web node" bugs simply cannot happen.
# =============================================================================
set -euo pipefail

ROLE="${1:-web}"

wait_for() {
  local host="$1" port="$2" label="$3" attempts=0
  until python -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(('${host}', ${port}))
except OSError:
    sys.exit(1)
" 2>/dev/null; do
    attempts=$((attempts + 1))
    if [ "${attempts}" -ge 60 ]; then
      echo "FATAL: ${label} at ${host}:${port} never became reachable." >&2
      exit 1
    fi
    echo "waiting for ${label} (${host}:${port})… ${attempts}"
    sleep 2
  done
  echo "${label} is up."
}

wait_for "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}" "PostgreSQL"
wait_for "${REDIS_HOST:-redis}" "${REDIS_PORT:-6379}" "Redis"

case "${ROLE}" in
  web)
    # Migrations run only on the web role so N replicas do not race. In a real
    # rollout this moves to a dedicated one-shot job.
    if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
      python manage.py migrate --noinput
      python manage.py seed_roles
    fi
    python manage.py collectstatic --noinput
    exec daphne -b 0.0.0.0 -p 8000 config.asgi:application
    ;;

  worker)
    exec celery -A config worker \
      --loglevel="${CELERY_LOG_LEVEL:-info}" \
      --queues="${CELERY_QUEUES:-default,ai,periodic}" \
      --concurrency="${CELERY_CONCURRENCY:-4}"
    ;;

  worker-vision)
    # Vision tasks are memory-hungry; low concurrency avoids OOM on a CPU node.
    exec celery -A config worker \
      --loglevel="${CELERY_LOG_LEVEL:-info}" \
      --queues=vision \
      --concurrency="${VISION_CONCURRENCY:-1}"
    ;;

  beat)
    exec celery -A config beat \
      --loglevel="${CELERY_LOG_LEVEL:-info}" \
      --scheduler django_celery_beat.schedulers:DatabaseScheduler
    ;;

  shell)
    exec python manage.py shell
    ;;

  *)
    exec "$@"
    ;;
esac
