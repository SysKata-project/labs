#!/bin/sh
set -eu
engine=${CONTAINER_ENGINE:-podman}
name=syskata-labs-test-db
case "${1:-}" in
 start)
  "$engine" run -d --name "$name" -p 127.0.0.1:55433:5432 \
    -e POSTGRES_USER=syskata -e POSTGRES_DB=syskata_labs \
    -e POSTGRES_HOST_AUTH_METHOD=trust docker.io/library/postgres:17.11 >/dev/null
  for count in $(seq 1 30); do
    if "$engine" exec "$name" pg_isready -U syskata -d syskata_labs >/dev/null 2>&1; then exit 0; fi
    sleep 1
  done
  exit 1;;
 stop) "$engine" rm -f "$name" >/dev/null;;
 *) echo 'Usage: scripts/test-database.sh start|stop' >&2; exit 2;;
esac
