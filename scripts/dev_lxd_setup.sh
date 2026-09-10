#!/usr/bin/env bash
# Docker-free local dev environment: spins up Postgres + Redis in LXD
# containers for manually testing panem_bot against a live Discord guild.
#
# This is a dev convenience only -- it is NOT the Plan's deploy artifact
# (that's deploy/docker-compose.yml / deploy/systemd/, per Plan §0). Run
# this on the machine where you'll run `uv run python -m panem_bot.main`,
# not inside this repo's CI or a container that can't nest containers.
#
# Requires LXD installed and initialized (`sudo snap install lxd && lxd init`).
# Idempotent: safe to re-run after a reboot if container IPs changed.
#
# Usage: scripts/dev_lxd_setup.sh

set -euo pipefail

PG_CONTAINER=panem-postgres
REDIS_CONTAINER=panem-redis
PG_USER=panem
PG_PASSWORD=panem
PG_DB=panem

if ! command -v lxc >/dev/null 2>&1; then
  echo "lxc not found. Install LXD first: sudo snap install lxd && sudo lxd init" >&2
  exit 1
fi

launch_if_missing() {
  local name=$1
  if ! lxc info "$name" >/dev/null 2>&1; then
    echo "==> Launching $name"
    lxc launch ubuntu:24.04 "$name"
    echo "==> Waiting for network in $name"
    for _ in $(seq 1 30); do
      lxc exec "$name" -- getent hosts archive.ubuntu.com >/dev/null 2>&1 && break
      sleep 1
    done
  else
    echo "==> $name already exists, reusing it"
  fi
}

launch_if_missing "$PG_CONTAINER"
launch_if_missing "$REDIS_CONTAINER"

echo "==> Provisioning Postgres in $PG_CONTAINER"
lxc exec "$PG_CONTAINER" -- bash -c "
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql >/dev/null

su postgres -c \"psql -tc \\\"SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'\\\"\" | grep -q 1 || \
  su postgres -c \"psql -c \\\"CREATE USER $PG_USER WITH PASSWORD '$PG_PASSWORD' SUPERUSER;\\\"\"
su postgres -c \"psql -tc \\\"SELECT 1 FROM pg_database WHERE datname='$PG_DB'\\\"\" | grep -q 1 || \
  su postgres -c \"psql -c \\\"CREATE DATABASE $PG_DB OWNER $PG_USER;\\\"\"

PG_CONF=\$(su postgres -c 'psql -tAc \"SHOW config_file\"')
PG_HBA=\$(su postgres -c 'psql -tAc \"SHOW hba_file\"')
sed -i \"s/^#listen_addresses.*/listen_addresses = '*'/\" \"\$PG_CONF\"
grep -q 'panem dev subnet' \"\$PG_HBA\" || \
  echo 'host all all 0.0.0.0/0 md5 # panem dev subnet' >> \"\$PG_HBA\"
systemctl restart postgresql
"

echo "==> Provisioning Redis in $REDIS_CONTAINER"
lxc exec "$REDIS_CONTAINER" -- bash -c "
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq redis-server >/dev/null
sed -i 's/^bind .*/bind 0.0.0.0/' /etc/redis/redis.conf
sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf
systemctl restart redis-server
"

pg_ip=$(lxc list "$PG_CONTAINER" -c 4 --format csv | cut -d' ' -f1)
redis_ip=$(lxc list "$REDIS_CONTAINER" -c 4 --format csv | cut -d' ' -f1)

if [[ -z "$pg_ip" || -z "$redis_ip" ]]; then
  echo "Could not read a container IP -- check 'lxc list' manually (interface may not be eth0)." >&2
  exit 1
fi

cat <<EOF

Done. Put these in .env:

DATABASE_URL=postgresql+asyncpg://$PG_USER:$PG_PASSWORD@$pg_ip:5432/$PG_DB
REDIS_URL=redis://$redis_ip:6379/0

Container IPs can change after a host reboot or 'lxc restart'. Re-run this
script (idempotent) and update .env if the bot can't connect.

Teardown: lxc delete --force $PG_CONTAINER $REDIS_CONTAINER
EOF
