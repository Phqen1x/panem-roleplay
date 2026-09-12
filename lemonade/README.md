# Panem-Omni: the sim's local AI, as a Lemonade OmniModel

[Lemonade](https://github.com/lemonade-sdk/lemonade) is a local AI server
(OpenAI-compatible, runs on your GPU/NPU/CPU). Its *omni models* bundle an
LLM with image, speech-to-text, text-to-speech and other models into one
virtual model (`recipe: "collection.omni"`); a chat request addressed to the
collection goes through Lemonade's OmniRouter, which hands the planner LLM
the collection's own system prompt plus the tools its components unlock and
executes the tool calls server-side.

`user.Panem-Omni-*` is that collection for this sim. Everything in this
directory is generated from, or validated against, the same `data/` and
`panem_shared.constants` the sim runs on, so the model's knowledge of Panem
can't drift from the world it narrates.

## What's in the box

| File | Purpose |
|------|---------|
| `system_prompt.md` | Hand-written prompt template: identity, tool policy, the request contract, canon, NPC voice rules, safety. `<<WORLD_ATLAS>>` and `<<WORLD_RULES>>` are filled at build time from `data/*.yaml` and `constants.py`; `{tool_list}` / `{tool_guidance}` are filled by Lemonade at request time from whichever components are present. |
| `components.json` | Component definitions vendored from Lemonade v11.9.0's built-in catalog so the collection files import even on a Lemonade whose catalog lacks a name (Lemonade keeps its own definition when it has one). |
| `Panem-Omni-Lite.json`, `Panem-Omni-Halo.json` | Generated, import-ready collection files: the exact body `POST /v1/pull` (or `lemonade import`, or the desktop app's *File > New Omni Model > From JSON*) accepts. Committed so they can be shared or uploaded to a Hugging Face repo named after the collection. |
| `embedded/config.json` | `lemond` overrides for the private, embedded instance (loopback only, no LAN beacon, models kept inside the app folder). |

Source of truth for all of it: `packages/panem_shared/src/panem_shared/lemonade/omni.py`
(profiles, rendering, the request contract) and `scripts/lemonade_omni.py` (the CLI).

## Profiles (which models, and why)

lemond picks the **first component labelled `chat`** as the planner, so the LLM
is always listed first. Roles are matched by label (`image`, `edit`, `tts`,
`transcription`, `vision`, `embeddings`).

| Role | Why Panem needs it | Lite (`user.Panem-Omni-Lite`, ~9.5 GB) | Halo (`user.Panem-Omni-Halo`, ~45 GB) |
|------|--------------------|------------------------------------------|----------------------------------------|
| Planner LLM (`chat`, `tool-calling`, `vision`) | NPC dialogue, narration, broadcasts, staff review; reads player-posted images directly (vision) | `Qwen3.5-4B-MTP-GGUF` | `Qwen3.6-35B-A3B-MTP-GGUF` |
| Image (`image`[, `edit`]) | NPC portraits for proxy avatars, establishing shots for ambient posts, district maps | `SD-Turbo` (4 steps) | `Flux-2-Klein-9B-GGUF` (gen + edit) |
| Speech-to-text (`transcription`) | Players' Discord voice messages in scenes | `Whisper-Base` | `Whisper-Large-v3-Turbo` |
| Text-to-speech (`tts`) | Capitol/district broadcasts read aloud (Activity / voice) | `kokoro-v1` | `kokoro-v1` |
| Embeddings (`embeddings`) | NPC memory retrieval (`MEMORY_CAP_PER_NPC`, `RETRIEVAL_K`) via `/v1/embeddings` | `nomic-embed-text-v1-GGUF` | `Qwen3-Embedding-0.6B-GGUF` |

The planner is loaded with `ctx_size` 16k (Lite) / 32k (Halo) and Qwen's
thinking phase disabled (`--chat-template-kwargs '{"enable_thinking": false}'`)
so a ≤90-word NPC line comes back fast; these are saved per model through
`POST /v1/models/{planner}/options`, exactly like `lemonade load --save-options`.

An alias `panem-omni` points at the active profile, so `LLM_MODEL=panem-omni`
never changes when you switch hardware.

## Run it locally

1. Install Lemonade Server (any one of these):
   - Ubuntu: `sudo snap install lemonade-server` or `sudo add-apt-repository ppa:lemonade-team/stable && sudo apt install lemonade-server`
   - Windows/macOS: the installer from Lemonade's releases page
   - Docker: `docker compose -f deploy/docker-compose.yml up -d lemonade`
   - Nothing installed: `uv run python scripts/lemonade_omni.py serve` downloads Embeddable Lemonade
     into `.lemonade/` and runs it (add `--register --download` to do step 3 in the same go)
2. `cp .env.example .env` -- the defaults already point at `http://127.0.0.1:13305/v1` and
   `LLM_MODEL=panem-omni`. Set `LLM_API_KEY` if your server runs with `LEMONADE_API_KEY`; pick
   `LEMONADE_PROFILE=halo` on a 32 GB+ GPU.
3. Register and download:

   ```bash
   uv run python scripts/lemonade_omni.py install     # register + pull every component
   uv run python scripts/lemonade_omni.py status      # what's registered / downloaded
   uv run python scripts/lemonade_omni.py smoke       # one in-character request, prints the reply
   ```

   `register` does the same without downloading (handy on a metered line; pull later with
   `lemonade pull user.Panem-Omni-Lite`). Private Hugging Face access: export `HF_TOKEN`
   in lemond's environment.
4. Talk to it from anything OpenAI-compatible:

   ```python
   from openai import OpenAI

   client = OpenAI(base_url="http://127.0.0.1:13305/v1", api_key="lemonade")
   client.chat.completions.create(model="panem-omni", messages=[...])
   ```

   Generated images/audio come back embedded in the assistant content as a markdown image
   with a `data:image/png;base64,...` URI or an `<audio>data:audio/mpeg;base64,...</audio>`
   tag; the bot turns those into Discord attachments.

The Lemonade desktop app (`sudo snap install lemonade` / `apt install lemonade-desktop`)
shows `Panem-Omni-*` under *Lemonade* in the chat picker and lets you tweak the components
or prompt in *File > New Omni Model*; export from there lands back in this format.

## The request contract

The sim never sends bare chat. Every request carries a header in its first `system`
message (lemond prepends the collection prompt to it), then the conversation turns
(`user` = the player's proxied line, `assistant` = the NPC's earlier lines).
`panem_shared.lemonade.omni.RequestContext` / `build_messages` produce it:

```
[MODE: dialogue]
[NPC] name: Old Ferro; age: 61; job: Hob Trader; home: District Twelve, The Seam; stance: likes; ...
[SCENE] district: District Twelve; location: The Hob; phase: evening; ...
[SPEAKER] name: Wren Calder; district: District Twelve; job: Miner; reputation: decent
[MEMORIES]
- Wren bought a jar of pickled beets on credit nine days ago and paid it off early.
[CONSTRAINTS] max_words=90
```

Modes: `dialogue`, `narrate`, `broadcast`, `portrait`, `establishing_shot`, `speak`,
`describe_image`, `npc_generate`, `review_character`, `staff`. Only the image/speech modes
may call tools; the prompt tells the model to answer plain dialogue with words only, to never
act for a player character, to reply with exactly `[REFUSE]` when a player pushes into
content it must not write (so the sim can fall back to a template line), and to return raw
JSON when `[CONSTRAINTS] format=json` is present (`LLM_JSON_MODE`).

`transcribe_audio` and `analyze_image` are client-side tools in Lemonade's OmniRouter (v1
server-side scope is image generation/editing and TTS): a vision planner reads attached
`image_url` parts directly, and voice messages are transcribed by the bot via
`POST /v1/audio/transcriptions` against the collection's Whisper component before the text
is sent as a normal turn.

## Rebuilding after content changes

```bash
uv run python scripts/lemonade_omni.py build          # regenerate Panem-Omni-*.json
uv run python scripts/lemonade_omni.py register       # push the new prompt to the server
```

`tests/unit/test_lemonade_omni.py` fails CI when the committed collection files are stale,
when the template loses a placeholder, or when the atlas stops covering every district,
location and job in `data/`.

## Embeddable Lemonade and the snap

Lemonade publishes an [embeddable build](https://github.com/lemonade-sdk/lemonade/blob/main/docs/embeddable/README.md)
(`lemond` + `lemonade` CLI + `resources/`, no UI) meant to be launched as a subprocess of
your own app with `LEMONADE_API_KEY=... lemond <dir> --port <port>`. Panem targets it two ways:

- **From a checkout**: `scripts/lemonade_omni.py serve` fetches the pinned release
  (`LEMONADE_EMBEDDABLE_VERSION`) into `LEMONADE_HOME` (default `.lemonade/`), seeds
  `embedded/config.json` into its data dir and runs it with `LLM_API_KEY` as the key.
- **For packaging**: `scripts/lemonade_omni.py bundle` produces a self-contained folder --
  `lemond`, `resources/`, `config.json` (models_dir=./models), `user_models.json` with the
  collection, `recipe_options.json` with the planner options, and `models/` with the weights --
  which is the deployment-ready layout from Lemonade's guide. `snap/snapcraft.yaml` is the
  scaffold that ships that layout plus the bot as two strictly-confined daemons
  (`panem-roleplay.lemond`, `panem-roleplay.bot`) with `panem-roleplay.setup` to register and
  download the OmniModel on first run and `panem-roleplay.lemonade` as the CLI. It has not
  been built on a snapcraft host yet; expect to iterate on `stage-packages` and GPU plugs.

Trim `resources/server_models.json` in the bundle to just the Panem components if you want
users to see nothing else, and pre-install backends with
`./lemonade backends install llamacpp:vulkan` (see Lemonade's embeddable backends guide).
