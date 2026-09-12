#!/usr/bin/env python3
"""Create, register and exercise Panem's Lemonade OmniModel (`user.Panem-Omni-*`).

Usage: uv run python scripts/lemonade_omni.py COMMAND [options]

Commands:
  build     Render lemonade/Panem-Omni-*.json from lemonade/system_prompt.md + data/
            (`--check` fails when the committed files are stale; CI runs this via pytest).
  register  Register the collection, its components, the planner's load options and the
            `panem-omni` alias on a running Lemonade server. Downloads nothing.
  install   `register`, then download every component (many GB; needs Hugging Face access).
  status    Server health, whether the collection is registered, and per-component download state.
  smoke     Send one in-character request to the collection and print the reply.
  serve     Run Embeddable Lemonade from LEMONADE_HOME (downloads the release tarball on first use).
  bundle    Build a self-contained embeddable folder with the models baked in, for packaging.

Server address and key come from .env: LLM_BASE_URL (default http://127.0.0.1:13305/v1) and
LLM_API_KEY (sent as `Authorization: Bearer`, also exported as LEMONADE_API_KEY to `serve`).
Profile comes from LEMONADE_PROFILE or `--profile`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from panem_shared.content.loader import load_content
from panem_shared.lemonade.omni import (
    ALIAS,
    PROFILES,
    OmniProfile,
    RequestContext,
    RequestMode,
    build_collection,
    build_messages,
    collection_filename,
    dump_collection,
    load_components_catalog,
    render_system_prompt,
    role_for,
)
from panem_shared.settings import Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
LEMONADE_DIR = REPO_ROOT / "lemonade"
PROMPT_PATH = LEMONADE_DIR / "system_prompt.md"
CATALOG_PATH = LEMONADE_DIR / "components.json"
EMBEDDED_CONFIG_PATH = LEMONADE_DIR / "embedded" / "config.json"
DEFAULT_BASE_URL = "http://127.0.0.1:13305/v1"
RELEASE_URL = "https://github.com/lemonade-sdk/lemonade/releases/download/v{version}/{asset}"


# --------------------------------------------------------------------------- #
# Tiny Lemonade HTTP client (stdlib only, so this runs from a bare `uv run`)
# --------------------------------------------------------------------------- #


class LemonadeError(RuntimeError):
    pass


class Lemonade:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 60.0) -> None:
        root = base_url.rstrip("/")
        for suffix in ("/api/v1", "/v1"):
            root = root.removesuffix(suffix)
        self.root = root
        self.api_key = api_key
        self.timeout = timeout

    def _open(self, method: str, path: str, body: Any = None, timeout: float | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.root + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise LemonadeError(f"{method} {path} -> HTTP {exc.code}: {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            raise LemonadeError(
                f"{method} {path} -> {exc.reason} (is Lemonade running at {self.root}?)"
            ) from exc

    def request(
        self, method: str, path: str, body: Any = None, timeout: float | None = None
    ) -> Any:
        with self._open(method, path, body, timeout) as resp:
            raw = resp.read().decode()
        return json.loads(raw) if raw.strip() else {}

    def health(self) -> dict[str, Any]:
        return dict(self.request("GET", "/v1/health"))

    def models(self) -> dict[str, dict[str, Any]]:
        data = self.request("GET", "/v1/models?show_all=true")["data"]
        return {m["id"]: m for m in data}

    def model(self, name: str) -> dict[str, Any] | None:
        try:
            return dict(self.request("GET", f"/v1/models/{name}"))
        except LemonadeError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def register(self, definition: dict[str, Any]) -> dict[str, Any]:
        return dict(self.request("POST", "/v1/models/register", definition))

    def pull(self, body: dict[str, Any], on_progress: Callable[[dict[str, Any]], None]) -> None:
        """Streams SSE progress; the pull itself can take an hour on a slow line."""
        with self._open("POST", "/v1/pull", {**body, "stream": True}, timeout=6 * 3600) as resp:
            event = ""
            for raw_line in resp:
                line = raw_line.decode(errors="replace").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line[5:].strip() or "{}")
                    if event == "error":
                        raise LemonadeError(f"pull failed: {payload.get('error', payload)}")
                    on_progress({"event": event, **payload})

    def set_options(self, model: str, options: dict[str, object]) -> dict[str, Any]:
        return dict(self.request("POST", f"/v1/models/{model}/options", options))

    def add_alias(self, alias: str, target: str) -> dict[str, Any]:
        return dict(self.request("POST", "/internal/aliases", {"alias": alias, "target": target}))

    def chat(self, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        return dict(self.request("POST", "/v1/chat/completions", body, timeout=timeout))


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #


def build_all() -> dict[str, tuple[Path, str]]:
    bundle = load_content(DATA_DIR)
    prompt = render_system_prompt(PROMPT_PATH.read_text(encoding="utf-8"), bundle)
    catalog = load_components_catalog(CATALOG_PATH)
    out: dict[str, tuple[Path, str]] = {}
    for key, profile in PROFILES.items():
        collection = build_collection(profile, system_prompt=prompt, catalog=catalog)
        out[key] = (LEMONADE_DIR / collection_filename(profile), dump_collection(collection))
    return out


def load_collection(profile: OmniProfile) -> dict[str, Any]:
    _, text = build_all()[profile.key]
    return dict(json.loads(text))


def cmd_build(args: argparse.Namespace) -> int:
    stale: list[Path] = []
    for path, text in build_all().values():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path)
            continue
        path.write_text(text, encoding="utf-8")
        words = len(json.loads(text)["system_prompt"].split())
        print(f"wrote {path.relative_to(REPO_ROOT)} (system prompt: {words} words)")
    if stale:
        names = ", ".join(str(p.relative_to(REPO_ROOT)) for p in stale)
        print(
            f"stale: {names} -- run `uv run python scripts/lemonade_omni.py build`", file=sys.stderr
        )
        return 1
    if args.check:
        print("collection files are up to date")
    return 0


# --------------------------------------------------------------------------- #
# Register / install / status
# --------------------------------------------------------------------------- #


def client_from(settings: Settings, args: argparse.Namespace) -> Lemonade:
    base_url = getattr(args, "base_url", None) or settings.llm_base_url or DEFAULT_BASE_URL
    return Lemonade(base_url, settings.llm_api_key)


def profile_from(settings: Settings, args: argparse.Namespace) -> OmniProfile:
    key = getattr(args, "profile", None) or settings.lemonade_profile
    try:
        return PROFILES[key]
    except KeyError:
        raise SystemExit(f"unknown profile {key!r}; choose from {', '.join(PROFILES)}") from None


def register_collection(api: Lemonade, profile: OmniProfile, *, download: bool) -> None:
    collection = load_collection(profile)
    known = api.models()

    # Components Lemonade already ships keep their built-in definition ("local
    # wins"); only unknown ones need the vendored definition registered first.
    for definition in collection["models"]:
        name = definition["model_name"]
        if name in known:
            continue
        print(f"registering component user.{name} (not in this Lemonade's catalog)")
        api.register({**definition, "model_name": f"user.{name}"})

    body = {k: v for k, v in collection.items() if k != "models"}
    if download:
        print(
            f"pulling {profile.model_name} ({collection['size']} GB across {len(collection['components'])} components)"
        )
        last = ""

        def progress(evt: dict[str, Any]) -> None:
            nonlocal last
            if evt.get("event") == "progress":
                line = f"  {evt.get('file', '?')} {evt.get('percent', 0)}% ({evt.get('file_index', '?')}/{evt.get('total_files', '?')})"
                if line != last:
                    print(line, end="\r", flush=True)
                    last = line
            elif evt.get("event") == "complete":
                print("\n  download complete")

        api.pull(body, progress)
    else:
        api.register(body)
    print(f"registered {profile.model_name}: components={collection['components']}")

    saved = api.set_options(profile.planner, dict(profile.planner_options))
    print(f"planner {profile.planner} options saved: {saved.get('saved', saved)}")

    try:
        api.add_alias(ALIAS, profile.model_name)
        print(f"alias {ALIAS} -> {profile.model_name}")
    except LemonadeError as exc:
        print(
            f"could not set alias ({exc}); run `lemonade alias add {ALIAS} {profile.model_name}` "
            "or set LLM_MODEL to the profile's model_name",
            file=sys.stderr,
        )


def cmd_register(args: argparse.Namespace) -> int:
    settings = get_settings()
    register_collection(
        client_from(settings, args), profile_from(settings, args), download=args.download
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    api = client_from(settings, args)
    profile = profile_from(settings, args)
    health = api.health()
    print(
        f"lemonade {health.get('version')} at {api.root}: {health.get('status')}; loaded={health.get('all_models_loaded')}"
    )
    info = api.model(profile.model_name)
    if info is None:
        print(f"{profile.model_name}: NOT registered -- run `scripts/lemonade_omni.py register`")
        return 1
    prompt_ok = "{tool_list}" in (info.get("system_prompt") or "")
    print(
        f"{profile.model_name}: registered, downloaded={info.get('downloaded')}, system prompt {'ok' if prompt_ok else 'MISSING'}"
    )
    ready = True
    for name in profile.components:
        comp = api.model(name) or {}
        labels = list(comp.get("labels", []))
        downloaded = bool(comp.get("downloaded"))
        ready &= downloaded
        print(
            f"  {name:32s} {role_for(labels) or '?':13s} {'downloaded' if downloaded else 'missing'}  {comp.get('size', '?')} GB"
        )
    try:
        aliases = api.request("GET", "/internal/aliases").get("aliases", [])
    except LemonadeError:
        aliases = []  # /internal/* needs the admin key when one is configured
    target = next((a["target"] for a in aliases if a.get("alias") == ALIAS), None)
    print(f"alias {ALIAS} -> {target or 'unset'}")
    if not ready:
        print("some components are not downloaded -- run `scripts/lemonade_omni.py install`")
    return 0 if ready and prompt_ok else 1


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #


SAMPLE_CONTEXT = RequestContext(
    mode=RequestMode.DIALOGUE,
    npc={
        "name": "Old Ferro",
        "age": "61",
        "job": "Hob Trader",
        "home": "District Twelve, The Seam",
        "personality": "gruff, shrewd, sentimental about the mines he left with a bad lung",
        "mood": "wary; a Peacekeeper patrol passed the Hob an hour ago",
        "stance": "likes",
        "secret": "he fences medicine skimmed from the Justice Building's ration store",
    },
    scene={
        "district": "District Twelve",
        "location": "The Hob",
        "phase": "evening",
        "ambience": "cold drizzle, lamp smoke, a dozen traders packing up",
        "present": "Ferro, the speaker, two miners arguing over a knife",
        "recent": "the coal quota came in short this month",
    },
    speaker={
        "name": "Wren Calder",
        "district": "District Twelve",
        "job": "Miner",
        "reputation": "decent; known to pay her debts",
    },
    memories=(
        "Wren bought a jar of pickled beets on credit nine days ago and paid it off early.",
        "Wren asked, quietly, whether anyone sells morphling here. Ferro said no.",
    ),
)

SAMPLE_TURNS = [
    {
        "role": "user",
        "content": "*shakes the rain off her jacket* Evening, Ferro. Got anything hot, or did the patrol scare it all off?",
    }
]


def cmd_smoke(args: argparse.Namespace) -> int:
    settings = get_settings()
    api = client_from(settings, args)
    profile = profile_from(settings, args)
    model = args.model or settings.llm_model or profile.model_name
    ctx = (
        SAMPLE_CONTEXT
        if args.mode == RequestMode.DIALOGUE.value
        else RequestContext(mode=RequestMode(args.mode), scene=SAMPLE_CONTEXT.scene)
    )
    turns = SAMPLE_TURNS if args.message is None else [{"role": "user", "content": args.message}]
    body: dict[str, Any] = {
        "model": model,
        "messages": build_messages(ctx, turns),
        "max_tokens": args.max_tokens,
        "temperature": 0.8,
        "stream": False,
    }
    print(f"-> {model} [{ctx.mode.value}]")
    for message in body["messages"]:
        print(f"   {message['role']}: {message['content']}")
    started = time.monotonic()
    reply = api.chat(body, timeout=args.timeout)
    elapsed = time.monotonic() - started
    choice = reply["choices"][0]
    content = choice["message"].get("content") or ""
    print(
        f"<- ({elapsed:.1f}s, finish={choice.get('finish_reason')}, {len(content.split())} words)"
    )
    print(content[:4000])
    if "<audio>" in content:
        print("[reply embeds generated audio as a data URI]")
    return 0


# --------------------------------------------------------------------------- #
# Embeddable Lemonade: serve / bundle
# --------------------------------------------------------------------------- #


def embeddable_asset(version: str) -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux":
        arch = "arm64" if machine in ("aarch64", "arm64") else "x64"
        return f"lemonade-embeddable-{version}-ubuntu-{arch}.tar.gz"
    if system == "Darwin":
        return f"lemonade-embeddable-{version}-macos-arm64.tar.gz"
    if system == "Windows":
        return f"lemonade-embeddable-{version}-windows-x64.zip"
    raise SystemExit(f"no Embeddable Lemonade build for {system}/{machine}")


def ensure_embeddable(home: Path, version: str) -> Path:
    """Returns the directory holding `lemond` (+ `lemonade`, `resources/`)."""
    exe = "lemond.exe" if platform.system() == "Windows" else "lemond"
    for candidate in (
        home,
        home / "embeddable",
        *sorted(home.glob("embeddable/lemonade-embeddable-*")),
    ):
        if (candidate / exe).exists():
            return candidate
    asset = embeddable_asset(version)
    url = RELEASE_URL.format(version=version, asset=asset)
    archive = home / "embeddable" / asset
    archive.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    urllib.request.urlretrieve(url, archive)
    if asset.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(archive.parent)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(archive.parent, filter="data")
    archive.unlink()
    for candidate in sorted(home.glob("embeddable/lemonade-embeddable-*")):
        if (candidate / exe).exists():
            print(f"embeddable lemonade {version} unpacked at {candidate}")
            return candidate
    raise SystemExit(f"{asset} did not contain {exe}")


def prepare_data_dir(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    config = data_dir / "config.json"
    if not config.exists():
        shutil.copyfile(EMBEDDED_CONFIG_PATH, config)


@contextlib.contextmanager
def running_lemond(
    settings: Settings, *, embeddable: Path, data_dir: Path, port: int
) -> Iterator[Lemonade]:
    prepare_data_dir(data_dir)
    exe = embeddable / ("lemond.exe" if platform.system() == "Windows" else "lemond")
    env = dict(os.environ)
    if settings.llm_api_key:
        env["LEMONADE_API_KEY"] = settings.llm_api_key
    # lemond refuses to start without a writable runtime dir (systemd would
    # normally provide one); a private one under the data dir mirrors what
    # Lemonade's own Docker image does.
    if not env.get("XDG_RUNTIME_DIR"):
        runtime_dir = data_dir / "run"
        runtime_dir.mkdir(mode=0o700, exist_ok=True)
        env["XDG_RUNTIME_DIR"] = str(runtime_dir)
    cmd = [str(exe), str(data_dir), "--port", str(port), "--no-broadcast"]
    log = (data_dir / "lemond.out").open("ab")
    proc = subprocess.Popen(cmd, cwd=data_dir, env=env, stdout=log, stderr=subprocess.STDOUT)
    api = Lemonade(f"http://127.0.0.1:{port}", settings.llm_api_key)
    try:
        for _ in range(60):
            if proc.poll() is not None:
                raise SystemExit(
                    f"lemond exited with {proc.returncode}; see {data_dir / 'lemond.out'}"
                )
            with contextlib.suppress(LemonadeError):
                api.health()
                break
            time.sleep(1)
        else:
            raise SystemExit("lemond did not become healthy within 60s")
        yield api
    finally:
        stop_process(proc)
        log.close()


def stop_process(proc: subprocess.Popen[bytes], grace: float = 15.0) -> None:
    """Terminate and wait, shrugging off the extra Ctrl-C people press while
    a process is already shutting down."""
    if proc.poll() is not None:
        return
    proc.terminate()
    deadline = time.monotonic() + grace
    while proc.poll() is None and time.monotonic() < deadline:
        with contextlib.suppress(subprocess.TimeoutExpired, KeyboardInterrupt):
            proc.wait(timeout=0.5)
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def cmd_serve(args: argparse.Namespace) -> int:
    settings = get_settings()
    home = Path(settings.lemonade_home or REPO_ROOT / ".lemonade")
    embeddable = ensure_embeddable(home, settings.lemonade_embeddable_version)
    data_dir = home / "data"
    with running_lemond(settings, embeddable=embeddable, data_dir=data_dir, port=args.port) as api:
        print(
            f"lemond {api.health().get('version')} serving http://127.0.0.1:{args.port}/v1 (data: {data_dir})"
        )
        if args.register:
            register_collection(api, profile_from(settings, args), download=args.download)
        print("press Ctrl-C to stop")
        # systemd / snapd stop daemons with SIGTERM; route it through the same
        # KeyboardInterrupt path so lemond is shut down instead of orphaned.
        signal.signal(signal.SIGTERM, lambda *_: _raise_interrupt())
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("stopping lemond")
    return 0


def _raise_interrupt() -> None:
    raise KeyboardInterrupt


def cmd_bundle(args: argparse.Namespace) -> int:
    settings = get_settings()
    home = Path(settings.lemonade_home or REPO_ROOT / ".lemonade")
    embeddable = ensure_embeddable(home, settings.lemonade_embeddable_version)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("lemond", "lemonade", "lemond.exe", "lemonade.exe", "LICENSE"):
        if (embeddable / name).exists():
            shutil.copy2(embeddable / name, out / name)
    shutil.copytree(embeddable / "resources", out / "resources", dirs_exist_ok=True)
    profile = profile_from(settings, args)
    with running_lemond(settings, embeddable=out, data_dir=out, port=args.port) as api:
        register_collection(api, profile, download=not args.no_download)
    for stray in ("lemond.out",):
        (out / stray).unlink(missing_ok=True)
    print(
        f"bundle ready at {out}: ship this folder (models_dir=./models keeps the weights private)"
    )
    print(
        "  layout: lemond, lemonade, resources/, config.json, user_models.json, recipe_options.json, models/, bin/"
    )
    print("  run it: LEMONADE_API_KEY=... ./lemond ./ --port 13305 --no-broadcast")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_server_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--base-url",
            help="Lemonade base URL (default: LLM_BASE_URL or %(default)s)",
            default=None,
        )
        p.add_argument(
            "--profile", choices=sorted(PROFILES), default=None, help="default: LEMONADE_PROFILE"
        )

    p_build = sub.add_parser("build", help="render lemonade/Panem-Omni-*.json")
    p_build.add_argument(
        "--check", action="store_true", help="fail if the committed files are stale"
    )
    p_build.set_defaults(func=cmd_build)

    p_register = sub.add_parser("register", help="register the collection without downloading")
    add_server_opts(p_register)
    p_register.set_defaults(func=cmd_register, download=False)

    p_install = sub.add_parser("install", help="register and download every component")
    add_server_opts(p_install)
    p_install.set_defaults(func=cmd_register, download=True)

    p_status = sub.add_parser("status", help="show registration and download state")
    add_server_opts(p_status)
    p_status.set_defaults(func=cmd_status)

    p_smoke = sub.add_parser("smoke", help="send a sample request to the collection")
    add_server_opts(p_smoke)
    p_smoke.add_argument(
        "--model", default=None, help="default: LLM_MODEL, else the profile's model_name"
    )
    p_smoke.add_argument(
        "--mode", choices=[m.value for m in RequestMode], default=RequestMode.DIALOGUE.value
    )
    p_smoke.add_argument("--message", default=None, help="override the sample player line")
    p_smoke.add_argument("--max-tokens", type=int, default=400)
    p_smoke.add_argument(
        "--timeout", type=float, default=600.0, help="first call includes model load"
    )
    p_smoke.set_defaults(func=cmd_smoke)

    p_serve = sub.add_parser("serve", help="run Embeddable Lemonade from LEMONADE_HOME")
    p_serve.add_argument("--port", type=int, default=13305)
    p_serve.add_argument("--profile", choices=sorted(PROFILES), default=None)
    p_serve.add_argument(
        "--register", action="store_true", help="also register the collection once up"
    )
    p_serve.add_argument(
        "--download", action="store_true", help="with --register: download components too"
    )
    p_serve.set_defaults(func=cmd_serve)

    p_bundle = sub.add_parser("bundle", help="build a self-contained embeddable folder")
    p_bundle.add_argument("--out", default=str(REPO_ROOT / ".lemonade" / "bundle"))
    p_bundle.add_argument(
        "--port", type=int, default=13399, help="temporary port used while baking"
    )
    p_bundle.add_argument("--profile", choices=sorted(PROFILES), default=None)
    p_bundle.add_argument(
        "--no-download", action="store_true", help="register only; pull models later"
    )
    p_bundle.set_defaults(func=cmd_bundle)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except LemonadeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
