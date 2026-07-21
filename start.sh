#!/usr/bin/env bash
set -euo pipefail

# 1. Start Tailscale in USERSPACE mode (Render containers can't make a TUN
#    device) and expose a SOCKS5 proxy on localhost:1055.
tailscaled \
  --tun=userspace-networking \
  --socks5-server=localhost:1055 \
  --state=/tmp/tailscaled.state &

# 2. Join the tailnet with an EPHEMERAL, TAGGED auth key.
#    Ephemeral => the node cleans itself up when this container dies/redeploys.
#    Tagged    => ACLs can scope what this node is allowed to reach.
tailscale up \
  --authkey="${TS_AUTHKEY}" \
  --hostname=render-site \
  --accept-routes

# 3. Start the web app. ONE worker is required: each browser's SSH shell
#    lives in that worker's memory, tied to its Socket.IO session.
exec gunicorn \
  --worker-class gthread \
  --workers 1 \
  --threads 20 \
  --bind "0.0.0.0:${PORT:-10000}" \
  --timeout 120 \
  "app:app"
