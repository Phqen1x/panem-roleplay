# Snap deploy (LXD container, external server)

Packages `panem_bot`/`panem_sim`/`panem_api` as one strictly confined snap
(`panem`) with three daemons, run inside an LXD container that also holds
Postgres and Redis (`deploy/lxd/provision.sh`). This is a third deploy
path alongside `deploy/docker-compose.yml` and `deploy/systemd/` -- pick
one, they're not meant to run side by side against the same database.

## Why a separate interpreter dance

`snap/snapcraft.yaml`'s `app` part does *not* use `uv sync`'s normal
`.venv` the way `deploy/Dockerfile` does. A uv-managed `.venv`'s
`bin/python` is a symlink back to a shared, absolute-path standalone
Python install, and its `pyvenv.cfg` records that same absolute path as
where to find the standard library at runtime -- neither survives being
copied from the snapcraft build environment into `/snap/panem/<rev>`.
Instead the build installs every dependency (including this workspace's
own four packages, non-editable) straight into the standalone
interpreter's own site-packages, then ships that whole interpreter tree
(genuinely relocatable by design -- that's what `python-build-standalone`,
what `uv python install` fetches, is *for*) as `$SNAP/python`. See the
comments in `snap/snapcraft.yaml` for the exact commands.

Because that install is non-editable, `packages/*/src/*/main.py` can no
longer assume it's running from inside a full checkout to find `data/`
(the old `Path(__file__).resolve().parents[4]` trick) -- `Settings.
data_dir` and `Settings.static_uploads_dir`
(`packages/panem_shared/src/panem_shared/settings.py`) exist for exactly
this, and the snap's wrapper scripts (`snap/local/bin/panem-*`) set them.
Every other deployment (`uv run`, Docker) leaves them unset and keeps
today's behavior.

## Build the snap

Needs `snapcraft` (and LXD or Multipass as its build backend) on the
machine doing the build -- not necessarily the server you'll deploy to:

```
sudo snap install snapcraft --classic
cd snap
snapcraft
```

Produces `panem_0.1.0_amd64.snap` in `snap/`. Build failures here are
almost always in the `override-build` step (`uv`/`uv python install`
network access, or an `snap/snapcraft.yaml` inline-comment typo) --
`snapcraft --debug` drops into a shell in the failed part's build
environment.

## Deploy to an external server's LXD container

On the target server (LXD installed and initialized: `sudo snap install
lxd && sudo lxd init`):

```
scp snap/panem_0.1.0_amd64.snap the-server:~/
ssh the-server
git clone <this repo>   # for deploy/lxd/provision.sh and deploy/snap/env.example
cd panem-roleplay
./deploy/lxd/provision.sh panem ~/panem_0.1.0_amd64.snap
```

This launches (or reuses) an `ubuntu:24.04` LXD container named `panem`
with `security.nesting=true` -- **required** for snapd's own mount
namespace to work inside an LXD container at all; without it `snap
install` fails or the daemons never actually start. It then:

1. apt-installs Postgres + Redis inside that same container, bound to
   `127.0.0.1` (nothing outside the container needs to reach them --
   compare `scripts/dev_lxd_setup.sh`'s two-container dev setup, which
   opens both up for cross-container access instead).
2. Ensures snapd is present and seeded.
3. Sideloads the snap (`snap install --dangerous`, since it's a local,
   unsigned build -- not published to the Snap Store).
4. Seeds `/var/snap/panem/current/env` from `deploy/snap/env.example` if
   it doesn't already exist.

Then, inside the container (the script prints these same steps at the
end):

```
lxc exec panem -- snap stop panem
lxc exec panem -- nano /var/snap/panem/current/env   # DISCORD_TOKEN etc.
lxc exec panem -- panem.migrate                       # alembic upgrade head
lxc exec panem -- snap start panem
```

Re-running `deploy/lxd/provision.sh` after building a new revision
`snap refresh --dangerous`s it in place; Postgres/Redis data and the env
file are untouched.

## Operating it

```
lxc exec panem -- snap services panem       # bot/sim/api status
lxc exec panem -- snap logs panem.bot -f     # journalctl-backed, per-app
lxc exec panem -- snap logs panem.sim -f
lxc exec panem -- snap logs panem.api -f
lxc exec panem -- snap restart panem.bot     # restart one daemon
lxc exec panem -- panem.migrate              # after a snap refresh that
                                              # ships a new migration
```

`panem.api` binds `$API_HOST:$API_PORT` (default `0.0.0.0:8000`) inside
the container. Reach it from outside with whichever of these fits:

- **Domain already on Cloudflare (recommended)**: run
  `deploy/lxd/setup_cloudflare_tunnel.sh panem your.domain.example` -- see
  its header comment for the one manual `cloudflared tunnel login` step it
  needs first. This runs `cloudflared` *inside* the container as a
  systemd service making an outbound connection to Cloudflare's edge, so
  nothing needs to be opened on this host or your router/NAT at all, and
  you get TLS for free. Then set `ACTIVITY_PUBLIC_URL=https://your.domain.example`
  in the env file (the script tells you the exact command) and `lxc exec
  panem -- snap restart panem.api`.
- **No Cloudflare / want a raw port instead**: publish a port on the
  container (`lxc config device add panem api-port proxy
  listen=tcp:0.0.0.0:8000 connect=tcp:127.0.0.1:8000`) or front it with a
  reverse proxy on the host, then point your own DNS/router at it and set
  `ACTIVITY_PUBLIC_URL` to whatever that externally reachable address
  turns out to be. You're on your own for TLS with this path.

## Interfaces / confinement

`panem.bot`/`panem.sim`/`panem.api` all plug `network` (outbound to
Postgres/Redis/Discord); `panem.api` additionally plugs `network-bind` to
listen. No other plugs are declared -- there's nothing under `$SNAP` that
needs to be written at runtime (`$SNAP_DATA` covers the env file and
`panem_api`'s writable uploads dir instead, both already outside the
confined, read-only `$SNAP` squashfs).
