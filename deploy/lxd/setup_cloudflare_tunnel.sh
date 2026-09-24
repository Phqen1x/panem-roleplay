#!/usr/bin/env bash
# Exposes panem.api (localhost:8000 inside the container) at a real domain
# via a Cloudflare Tunnel, entirely from inside the LXD container -- no
# inbound port, no router/NAT config, no reverse proxy on the host. This
# replaces the "publish a port on the container" networking note in
# deploy/snap/README.md for anyone using Cloudflare for DNS already.
#
# One-time manual prerequisite (needs a browser, so it can't be scripted):
#   cloudflared tunnel login
# on your OWN machine, logging into the Cloudflare account that owns the
# domain. That writes ~/.cloudflared/cert.pem -- push it into the
# container before running this script:
#   lxc exec <container> -- mkdir -p /root/.cloudflared
#   lxc file push ~/.cloudflared/cert.pem <container>/root/.cloudflared/cert.pem
#
# Usage:
#   deploy/lxd/setup_cloudflare_tunnel.sh [container] <hostname> [tunnel-name]
#
# Example:
#   deploy/lxd/setup_cloudflare_tunnel.sh panem panem.example.com panem
#
# Idempotent: safe to re-run (e.g. after changing the hostname).

set -euo pipefail

CONTAINER="${1:-panem}"
HOSTNAME_ARG="${2:?Usage: $0 [container] <hostname> [tunnel-name]}"
TUNNEL_NAME="${3:-panem}"

if ! command -v lxc >/dev/null 2>&1; then
  echo "lxc not found. Install LXD first: sudo snap install lxd && sudo lxd init" >&2
  exit 1
fi

if ! lxc exec "$CONTAINER" -- test -f /root/.cloudflared/cert.pem 2>/dev/null; then
  cat >&2 <<EOF
Missing /root/.cloudflared/cert.pem in $CONTAINER.

Run this once on a machine with a browser (logs into the Cloudflare
account that owns your domain), then push the cert into the container:

  cloudflared tunnel login
  lxc exec $CONTAINER -- mkdir -p /root/.cloudflared
  lxc file push ~/.cloudflared/cert.pem $CONTAINER/root/.cloudflared/cert.pem

Then re-run this script.
EOF
  exit 1
fi

echo "==> Installing cloudflared in $CONTAINER"
lxc exec "$CONTAINER" -- bash -c '
set -e
if ! command -v cloudflared >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  mkdir -p /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq
  apt-get install -y -qq cloudflared >/dev/null
fi
'

echo "==> Creating tunnel '$TUNNEL_NAME' (no-op if it already exists) and routing DNS to $HOSTNAME_ARG"
lxc exec "$CONTAINER" -- bash -c "
set -e
cloudflared tunnel create '$TUNNEL_NAME' 2>/dev/null || true
cloudflared tunnel route dns '$TUNNEL_NAME' '$HOSTNAME_ARG'
"

TUNNEL_JSON="$(lxc exec "$CONTAINER" -- cloudflared tunnel list -o json)"
TUNNEL_ID="$(python3 -c "
import json, sys
data = json.loads('''$TUNNEL_JSON''')
print(next(t['id'] for t in data if t['name'] == '$TUNNEL_NAME'))
")"

echo "==> Writing /root/.cloudflared/config.yml (tunnel $TUNNEL_ID -> http://localhost:8000)"
lxc exec "$CONTAINER" -- bash -c "cat > /root/.cloudflared/config.yml <<'INNER'
tunnel: $TUNNEL_ID
credentials-file: /root/.cloudflared/$TUNNEL_ID.json
ingress:
  - hostname: $HOSTNAME_ARG
    service: http://localhost:8000
  - service: http_status:404
INNER
"

echo "==> Installing and starting the cloudflared systemd service"
lxc exec "$CONTAINER" -- cloudflared service install || true
lxc exec "$CONTAINER" -- systemctl enable --now cloudflared

cat <<EOF

Done. https://$HOSTNAME_ARG now tunnels straight to panem.api's
localhost:8000 inside $CONTAINER via Cloudflare's edge -- nothing needed
open on this host or your router.

Last step: point ACTIVITY_PUBLIC_URL at it and restart the api daemon:

  lxc exec $CONTAINER -- sed -i 's#^ACTIVITY_PUBLIC_URL=.*#ACTIVITY_PUBLIC_URL=https://$HOSTNAME_ARG#' /var/snap/panem/current/env
  lxc exec $CONTAINER -- snap restart panem.api

Re-run this script any time (e.g. after a hostname change); it reuses the
existing tunnel and overwrites config.yml in place.
EOF
