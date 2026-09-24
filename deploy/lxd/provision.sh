#!/usr/bin/env bash
# Provisions ONE LXD container that runs Postgres + Redis + the panem snap
# together (deploy/snap/README.md's "bundle everything in one container"
# layout) on an external server. Idempotent: safe to re-run.
#
# This is the *production* counterpart to scripts/dev_lxd_setup.sh (which
# spins up two throwaway containers for `uv run`-against-a-live-guild dev
# work and never touches snapd) -- it does not replace it for local dev.
#
# Prerequisites on the server:
#   - LXD installed and initialized: sudo snap install lxd && sudo lxd init
#   - A built panem snap: cd snap && snapcraft (needs snapcraft installed;
#     see deploy/snap/README.md for the full build walkthrough)
#
# Usage:
#   scripts/../deploy/lxd/provision.sh [container-name] [path/to/panem_*.snap]
#
# Defaults: container-name=panem, snap file is the newest *.snap found in
# the repo root or ./snap/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER="${1:-panem}"
SNAP_FILE="${2:-}"
PG_USER=panem
PG_PASSWORD=panem
PG_DB=panem

if ! command -v lxc >/dev/null 2>&1; then
  echo "lxc not found. Install LXD first: sudo snap install lxd && sudo lxd init" >&2
  exit 1
fi

if [[ -z "$SNAP_FILE" ]]; then
  SNAP_FILE="$(ls -t ./*.snap ./snap/*.snap 2>/dev/null | head -n1 || true)"
fi
if [[ -z "$SNAP_FILE" || ! -f "$SNAP_FILE" ]]; then
  echo "No .snap file found/given. Build one first: cd snap && snapcraft" >&2
  echo "Then: $0 $CONTAINER path/to/panem_0.1.0_amd64.snap" >&2
  exit 1
fi

if ! lxc info "$CONTAINER" >/dev/null 2>&1; then
  echo "==> Launching $CONTAINER (nesting enabled, needed for snapd's mount namespace)"
  lxc launch ubuntu:24.04 "$CONTAINER" -c security.nesting=true
  echo "==> Waiting for network in $CONTAINER"
  for _ in $(seq 1 30); do
    lxc exec "$CONTAINER" -- getent hosts archive.ubuntu.com >/dev/null 2>&1 && break
    sleep 1
  done
else
  echo "==> $CONTAINER already exists -- reusing it, ensuring nesting is on"
  current_nesting=$(lxc config get "$CONTAINER" security.nesting || true)
  if [[ "$current_nesting" != "true" ]]; then
    lxc config set "$CONTAINER" security.nesting true
    echo "    (restarting $CONTAINER so the nesting change takes effect)"
    lxc restart "$CONTAINER"
    sleep 5
  fi
fi

echo "==> Provisioning Postgres + Redis in $CONTAINER"
lxc exec "$CONTAINER" -- bash -c "
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql redis-server >/dev/null

su postgres -c \"psql -tc \\\"SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'\\\"\" | grep -q 1 || \
  su postgres -c \"psql -c \\\"CREATE USER $PG_USER WITH PASSWORD '$PG_PASSWORD' SUPERUSER;\\\"\"
su postgres -c \"psql -tc \\\"SELECT 1 FROM pg_database WHERE datname='$PG_DB'\\\"\" | grep -q 1 || \
  su postgres -c \"psql -c \\\"CREATE DATABASE $PG_DB OWNER $PG_USER;\\\"\"
systemctl enable --now postgresql redis-server
# Both ship bound to 127.0.0.1 by default on Ubuntu -- correct here, since
# the app connecting to them lives in this same container (unlike
# scripts/dev_lxd_setup.sh's two-container dev layout, nothing needs to
# reach either service from outside this container at all).
"

echo "==> Ensuring snapd is present and seeded in $CONTAINER"
lxc exec "$CONTAINER" -- bash -c "
set -e
if ! command -v snap >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq snapd >/dev/null
fi
snap wait system seed.loaded
"

echo "==> Installing system libraries the embeddable Lemonade runtime needs"
lxc exec "$CONTAINER" -- bash -c "
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# lemond (lemonade/README.md's 'Nothing installed' path, run here by
# panem.lemonade -- deploy/snap/README.md) dynamically links against the
# AMD GPU DRM library unconditionally, even in --profile lite/CPU-only
# use with no GPU passthrough into this container at all -- without it,
# lemond fails to start at all with 'error while loading shared
# libraries: libdrm_amdgpu.so.1: cannot open shared object file', which a
# minimal ubuntu:24.04 LXD container doesn't ship by default. Installed
# before the snap below so panem.lemonade's first start already has it,
# instead of crash-looping until someone notices and installs it by hand.
apt-get install -y -qq libdrm-amdgpu1 >/dev/null
"

echo "==> Pushing and (re)installing the panem snap from $SNAP_FILE"
lxc file push "$SNAP_FILE" "$CONTAINER/root/panem.snap"
lxc exec "$CONTAINER" -- bash -c '
set -e
# --dangerous: this is a locally built, unsigned snap, not one from the
# Snap Store -- expected for a self-hosted sideload like this. `install`
# only works the first time; every re-run after that needs `refresh`.
if snap list panem >/dev/null 2>&1; then
  snap refresh --dangerous /root/panem.snap
else
  snap install --dangerous /root/panem.snap
fi
'

echo "==> Seeding \$SNAP_DATA/env (edit it before starting the daemons)"
lxc exec "$CONTAINER" -- mkdir -p /var/snap/panem/current
if ! lxc exec "$CONTAINER" -- test -f /var/snap/panem/current/env; then
  lxc file push "$SCRIPT_DIR/../snap/env.example" "$CONTAINER/var/snap/panem/current/env"
  lxc exec "$CONTAINER" -- chmod 600 /var/snap/panem/current/env
fi

cat <<EOF

Done. Remaining manual steps in $CONTAINER:

  lxc exec $CONTAINER -- snap stop panem          # daemons are already
                                                   # running/crash-looping
                                                   # without a real
                                                   # DISCORD_TOKEN yet
  lxc exec $CONTAINER -- nano /var/snap/panem/current/env   # fill in secrets
  lxc exec $CONTAINER -- panem.migrate            # alembic upgrade head
  lxc exec $CONTAINER -- snap start panem         # starts bot, sim, api

Logs:   lxc exec $CONTAINER -- snap logs panem.bot -f
Status: lxc exec $CONTAINER -- snap services panem

Re-run this script after building a new snap revision to push and
reinstall it in place (env and Postgres/Redis data are untouched).
EOF
