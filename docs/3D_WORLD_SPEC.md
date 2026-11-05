# Panem 3D World & Roleplay Spec

**Status:** Draft — companion to this project's existing Plan/Spec documents
(cited throughout the codebase as `Plan §N` / `Spec §N`, FR-XXX requirement
IDs), not a replacement for them. Those documents define the simulation,
economy, and Discord-bot RP system this spec builds a 3D layer *on top of*.
Where this document says "the sim," "the economy," "RP modes," or similar,
it means the systems those documents already define and `panem_sim`/
`panem_shared` already implement — this spec does not re-litigate them.

**Scope of this document:** turn the single, Capitol-only 3D map built so
far (`panem_shared.map3d_layout`, `panem_api.map3d_routes`, `static/map3d/
*.js`) into a full 3D presence layer across all thirteen districts, with
real NPC and player interaction happening *in* that 3D space instead of
only through Discord commands and a 2D schematic. It is long because the
ask is large; §12 breaks it into shippable phases, and nothing here is
meant to land as one PR.

Functional requirements introduced here are numbered **FR-3D-nnn** to stay
out of the existing FR-XXX namespace's way.

---

## 1. Vision

Panem: The Long Year is, today, a Discord-bot-first persistent world: slash
commands, embeds, a web dashboard with a 2D schematic map, and — as of the
most recent work — a single 3D district (the Capitol) you can walk a
block-built character around, talk to nobody yet, buy a pet, and decorate
a house.

The vision this spec describes: **every district is a place**, not a
dropdown entry. A player's character has a body that stands somewhere, in
a city or town shaped like that district's own industry and culture, among
NPCs who are physically *there* — at the mine entrance during a shift, at
the Hob after dark, walking home down Residential Row — not just rows in a
database with a `location_id` column. Talking to an NPC, working a shift,
fencing stolen goods, meeting another player to trade: all of it has a
place it visibly happens, in a 3D world that feels like Panem, not a
generic plaza with reskinned boxes.

Discord remains the authoritative social surface — proxying, `/talk`,
scenes, moderation, the written word. The 3D world is a second, synchronized
way to *be* in Panem, not a replacement for the first.

---

## 2. Current State (baseline this spec builds on)

As of this writing, the following already exists and this spec treats as
foundation, not something to redo:

- **One district in 3D.** `panem_shared.map3d_layout.build_city_layout`
  generates a Manhattan grid of building plots + streets for a `District`,
  using `DistrictMap.location_coords` to snap each named location to its
  nearest plot and filling every other plot with deterministic filler
  buildings. Only the Capitol (district 0) is wired to it
  (`static/tabs/map.js`'s district check); every other district still gets
  the original 2D schematic canvas.
- **A walkable, customizable block character.** `static/map3d/character.js`
  assembles a Blocky Characters body from four independent parts (head/
  torso/arms/legs), each with its own shape and color, persisted as
  `Character.block_parts` (JSONB). NPCs get a deterministic, stable
  (not semantically job-matched — the asset pack has no labels for that)
  body the same way (`panem_shared.block_models.assign_npc_appearance`).
- **Live, ephemeral, server-authoritative movement**, one room per
  district (`panem_api.map3d_routes.Map3DRoom`), built the same way the
  existing Party Room (`party_routes.PartyRoom`) already works: WASD
  input, a steady broadcast tick, client-side position easing. Not
  persisted to `Character.x`/`.y` (those stay owned by `panem_sim.
  systems.time`'s location-snap system).
- **NPCs visibly present and wandering** near their home location (small
  deterministic scatter + bounded wander), with name/job-title shown on
  proximity, but **no interaction beyond a name tag** — "map interaction
  can be added later" was the previous pass's own explicit scope cut, and
  §7 of this document is that later.
- **A pet store and decoratable house/apartment interiors**, both real,
  both already reachable from the 3D map (walk to the Market / your home
  plot, press E).
- **An asset library already vendored**, curated from Kenney's CC0
  packs, far beyond what's in use today: Blocky Characters, City Kit -
  Commercial/Roads (in use), Cube Pets, Furniture Kit (in use) — and a
  much larger unused library sitting in the `kenney-assets` source
  checkout this project draws from (Nature Kit, Mini Forest, City Kit -
  Suburban/Industrial, Factory Kit, Castle Kit, Watercraft Pack, Train
  Kit, Car Kit, Road Pack, Graveyard Kit, Retro Urban Kit, and more — see
  §6.2).

What does **not** exist yet, and this spec is the plan for: the other
twelve districts in 3D; any NPC interaction beyond a name tag; any player
action (work, crime, trade, scenes) happening *in* the 3D world rather
than through a Discord command or a dashboard tab; any visible connection
*between* districts (travel currently teleports a character's `district_
id` with no 3D representation at all).

---

## 3. Goals

- **G1.** Every district (0–12) renders as its own themed 3D world,
  generated the same way the Capitol's is (deterministic, content-driven,
  no district-specific code) but visually distinct per its industry/
  culture.
- **G2.** NPCs are interactable in 3D: walking up and pressing E opens a
  real dialogue surface tied into the existing relationship/dialogue
  systems, not a stub.
- **G3.** The core player loops that already exist as Discord commands/
  dashboard tabs (work, steal/burgle/poach, market trading, scenes) gain a
  3D-world entry point — walking to the right place and interacting —
  without removing their existing Discord-command/dashboard-tab entry
  points. 3D is additive.
- **G4.** Districts are connected: traveling between them is a real,
  visible transition (not an instant teleport), and the systems that
  already couple districts economically (`data/routes.yaml`'s trade
  routes) and socially (shared character/relationship/world-clock state)
  remain the connective tissue — see §5 for exactly what "federated"
  means here.
- **G5.** None of this regresses the existing Discord-first experience.
  A player who never opens the Activity can still play the entire game
  exactly as today.

## 4. Non-Goals (explicit scope boundaries)

Written down because "a fully fleshed out roleplaying game" is the kind
of ask that silently expands forever without a stated edge:

- **Not a seamless single open world.** Panem is a continent; thirteen
  districts rendered as one continuous 3D space is neither technically
  sane for a Discord Activity iframe nor true to the source material
  (districts are isolated, fenced, rail-connected). Each district is its
  own scene/instance — see §5.
- **Not real-time arena combat / an actual Hunger Games simulator.**
  The existing `Position` enum (Victor/Gamemaker/Governor) and Training
  Center locations are flavor and staff-granted status today; building an
  actual PvP arena game is a separate, much larger project and explicitly
  out of scope here. The Training Center stays a location you can stand
  in, not a game mode.
- **Not a voice/video layer.** Text RP (proxying, `/talk`, scenes) stays
  the writing surface. The 3D world adds presence and context, not a new
  chat medium.
- **Not a replacement for any existing Discord command or dashboard tab.**
  Every 3D interaction this spec adds has an existing non-3D equivalent
  that keeps working unmodified.
- **Not NPC pathfinding/AI in the general sense.** NPC movement in 3D is
  "walk the straight-line/road-snapped path between two points on a
  schedule," not obstacle avoidance, group behavior, or anything
  resembling game-AI navmeshes.
- **Not mobile-first.** The Activity should *work* on a phone (Discord
  Activities run there), but "feels great on mobile" performance tuning
  is a later-phase concern (§12), not a blocking requirement for any
  earlier phase.

---

## 5. Core Concept: Federated District Worlds

### 5.1 What "federated" means here

Each district is an **autonomous zone**: its own generated 3D scene, its
own live WebSocket room (`Map3DRoom`, already built this way — see §10.1),
its own NPC population walking around in it, loaded and rendered
independently of every other district. A player is only ever connected to
*one* zone's room at a time. There is no shared 3D space spanning
districts, no rendering two districts in the same scene, no cross-district
line of sight.

What makes them **not** completely separate — the shared substrate every
zone sits on top of:

| Shared system | Already exists as | Federation role |
|---|---|---|
| World clock / tick | `panem_sim`'s single tick loop, `WorldClock` | One simulated time across every district; a zone never runs its own clock. |
| Character identity | `User`/`Character` rows | A character is the same character everywhere; switching zones doesn't fork them. |
| Economy | `data/routes.yaml` trade routes, `MarketPrice`/`MarketOrder`, `panem_sim.systems.economy` | Districts already aren't economically isolated — District 7's lumber already flows to districts that import it. The 3D layer visualizes this (§8.5), doesn't invent it. |
| Relationships | `RelationshipRow` (character↔NPC, global, not per-district) | An NPC you've built trust with remembers you if you ever travel to where they are — relationships aren't zone-scoped. |
| Travel | `Character.in_transit_until_tick`/`.transit_destination_id`, `panem_shared.travel` | The literal connective tissue between zones — see §9. |
| Appearance/pets/housing | `Character.block_parts`, `Pet`, `PropertyInteriorItem` | A character's body, pets, and decorated home look the same regardless of which zone they're standing in. |
| Discord presence | Existing bot integration | Scene/engagement notifications, proxying, staff tools — unchanged, zone-agnostic. |

A useful mental model: each district's `Map3DRoom` is a **shard**; the
Postgres database and `panem_sim`'s tick loop are the **federation layer**
every shard reads from and writes through. This is not a new
architecture to invent — `Map3DRoom` was already built per-district-id for
exactly this reason (see that class's own docstring); this spec is "turn
that on for twelve more districts and give the rooms somewhere to send a
player between them."

### 5.2 Zone transitions

Moving between districts is **not** instant. A character already has
`in_transit_until_tick`/`transit_destination_id` (the existing Travel
system) — the 3D layer's job is to give that window something to show:

- Initiating travel (3D proximity to a Station location + an in-3D
  "board the train" prompt, or the existing `/travel` command/dashboard
  Travel tab — both remain valid entry points per G3/G5) disconnects the
  player's client from their current district's `Map3DRoom`.
- While `in_transit_until_tick` is in the future, the client shows a
  **transit scene**: not another full district, a lightweight shared
  scene (e.g. a train car interior, built from the vendored Train Kit)
  with no NPCs and no per-district asset loading, just the player's own
  character and a countdown to arrival. This is cheap specifically so it
  never needs to be "a fourteenth zone" with its own content burden.
- On arrival (`in_transit_until_tick` reached, resolved the same way the
  existing Travel system already resolves it), the client connects to
  the destination district's `Map3DRoom` and loads that zone.
- A player who closes the Activity mid-transit and reopens it later
  (or never opens the Activity at all) is unaffected — transit resolution
  is already a tick-driven, Activity-independent process; the 3D transit
  scene is purely cosmetic for whoever happens to be watching.

### 5.3 Cross-district visibility

Deliberately minimal, to keep zones genuinely autonomous:

- **No live cross-zone rendering** (§5.1). You cannot see District 4's
  square from District 2's.
- **District-level ambient facts can surface across zones** where the
  game already tracks them globally and it's cheap to show — e.g. a
  "district news" ticker sourced from `PanemHistoryEntry`/`DistrictLore`
  (already-existing, already-global systems), not a new cross-zone
  presence feed.
- **No cross-zone chat.** Scenes and proxying are already Discord-channel-
  scoped per district; the 3D layer doesn't add a new cross-district
  channel.

---

## 6. District World Generation

### 6.1 Generalizing the grid-city generator

`build_city_layout` already takes a `District` and is not Capitol-specific
in its logic (confirmed by its own existing tests). What's missing is
**theming**: every district currently resolves to the same City Kit -
Commercial building pool regardless of industry. This spec proposes:

- **FR-3D-001.** `DistrictMap` (content schema) gains an optional `theme`
  field (a short string key, e.g. `"lumber"`, `"coal"`, `"capitol"`).
  Omitted/unrecognized theme falls back to the current Commercial-kit
  behavior (so existing district YAML keeps working with zero edits until
  someone authors a theme for it — matches this project's existing
  "optional content, sensible fallback" convention, e.g. `load_routes`/
  `load_npcs`).
- **FR-3D-002.** `map3d_layout` gains a `_BUILDING_MODELS_BY_THEME: dict[
  str, dict[LocationKind, tuple[str, ...]]]` and a matching `_FILLER_POOL_
  BY_THEME`, keyed by `theme`. `build_city_layout` looks up the district's
  theme first, falls back to the existing Commercial-kit tables if the
  theme has no override for a given `LocationKind`. This keeps the
  function pure/deterministic/generic exactly as it is today — no
  per-district branching, just a wider lookup table.
- **FR-3D-003.** A location can declare itself **unbuilt** regardless of
  kind — e.g. District 12's `outskirts` location should render as woods,
  not a filler building. Add an optional `render` hint to `Location`
  content (`"building"` default, `"open"` for a themed natural/open area
  with no structure, decorated instead by a small scatter of theme-
  appropriate props — see §6.2's Nature Kit/Mini Forest entries).
- **FR-3D-004.** Asset vendoring for new themes follows the exact process
  already used (`vendor_assets.py`-style curation script, cross-checked
  against every theme table entry so nothing in code points at an
  unvendored file — the same verification step already done for the
  Capitol's assets). New kits are vendored per-theme, not all at once, so
  each district's 3D rollout (§12) only adds the assets it actually uses.

### 6.2 District theming table

Concrete starting proposal, matched to each district's canon industry/
culture and to asset kits already present in the `kenney-assets` source
checkout (none of this is vendored yet — §12 phases it in):

| District | Industry | Theme | Primary kit(s) | Notes |
|---|---|---|---|---|
| 0 Capitol | governance | `capitol` | City Kit - Commercial (already vendored) | Unchanged; Castle Kit accents for the Justice Hall/government buildings could be a later polish pass. |
| 1 | luxury goods | `luxury` | City Kit - Commercial (reuse) | Reuse the Capitol's kit with awning/parasol detail pieces (already vendored, currently unplaced) leaned on more heavily — District One is wealthy, not architecturally distinct from the Capitol in canon. |
| 2 | masonry/stone, Peacekeeper training | `masonry` | Castle Kit, Modular Buildings | Stone-built, martial feel; the quarry/training-center workplace gets Castle Kit walls. |
| 3 | electronics | `tech` | Factory Kit, Space Kit (detail pieces only) | Industrial with a technological edge — factories, not futuristic. |
| 4 | fishing | `coastal` | Watercraft Pack, Nature Kit | Docks, boats at the harbor-equivalent location; the only district needing water-adjacent terrain treatment (flat "sea" plane + Watercraft props). |
| 5 | power | `industrial-power` | Factory Kit, Space Station Kit (detail pieces only) | Power-plant-adjacent industrial — reuse Factory Kit's heavier pieces. |
| 6 | transportation | `transit` | Car Kit, Train Kit, Road Pack | The one district that should visibly *be* a transit hub — appropriate given it's also the thematic home of the cross-district Train Kit transit scene (§5.2, §9.1). |
| 7 | lumber | `forest` | Mini Forest, Nature Kit (Classic), Building Kit | Heavy tree coverage around the city grid's edges, sawmill built from Building Kit. |
| 8 | textiles | `mill-town` | City Kit - Suburban, Factory Kit | Suburban residential feel with mill buildings — not vendored at all yet (City Kit - Suburban is unused today). |
| 9 | grain | `farmland` | Nature Kit, City Kit - Suburban | Fields at the district edge, modest town center. |
| 10 | livestock | `ranch` | Nature Kit, City Kit - Suburban | Similar treatment to 9, pastoral prop set instead of fields. |
| 11 | produce/orchards | `orchard` | Nature Kit (heavy), City Kit - Suburban | The most agriculture-dense district — Nature Kit trees/crops as the dominant filler "building" pool via the `render: "open"` hint (FR-3D-003), actual structures sparse. |
| 12 | coal | `seam` | Retro Urban Kit, Factory Kit, Graveyard Kit (sparingly), Mini Forest (for the woods beyond the fence) | The poorest, grimmest district — Retro Urban's run-down buildings for the Seam, Mini Forest for the canon-important woods past the fence (`outskirts` location, `render: "open"`), a coal-mine-entrance built from Factory Kit. |

This table is a starting proposal for whoever authors each district's
content, not a locked spec — the mechanism (FR-3D-001/002/003) is the
actual deliverable; the table is guidance for using it.

### 6.3 Content authoring model

No new authoring tool is proposed. Theme assignment is a one-line
`map.theme:` addition to an existing `data/districts/d{n}.yaml` file,
same editing workflow as every other district content change already in
this project. The `render: "open"` hint (FR-3D-003) is likewise a one-line
addition to a `locations:` entry. Cross-validation (`_cross_validate` in
`panem_shared.content.loader`) gains a check that a declared `theme` is a
real key in `map3d_layout`'s theme tables, so a typo'd theme fails content
validation the same way an unknown `job_id` reference already does, rather
than silently falling back.

---

## 7. NPC System in 3D

This is the single biggest gap between "a walkable diorama" and "a
roleplaying game" — today's NPCs are scenery. This section is what makes
them not be.

### 7.1 Visible schedules & movement

`panem_sim.systems.schedule` already assigns each NPC a `location_id` on
some cadence, snapping `Npc.x`/`.y` to that location's coordinates
instantly. This spec does **not** propose changing that server-side model
(it's correct and sufficient for everything that isn't rendering). What it
adds is purely client/read-side:

- **FR-3D-010.** `panem_api.map3d_routes`'s NPC payload gains a
  `previous_location_id` alongside the current one (one extra column,
  `Npc.previous_location_id`, written by `panem_sim.systems.schedule`
  whenever `location_id` changes — the one small server-side change this
  section needs). When a connected `Map3DRoom` next serves that NPC, it
  animates a straight-line (road-snapped where practical — reuse the same
  plot/street grid `map3d_layout` already generates) walk from the old
  location's position to the new one over a fixed cosmetic duration
  (e.g. 20–40 seconds), rather than teleporting. Entirely a `Map3DRoom`-
  side tween; the authoritative `Npc.x`/`.y` jump is unaffected.
- This directly extends the existing NPC-wander tick (`Map3DRoom._advance_
  npcs`) rather than replacing it: wander is the idle state, a scheduled
  relocation is a one-shot walk that takes over, then idle/wander resumes
  at the new location.

### 7.2 3D dialogue & relationship-gated interaction

- **FR-3D-011.** Walking up to an NPC and pressing E (the existing
  interaction key, currently just a name/job tag) opens a dialogue panel
  that calls the **existing** `/talk` dialogue resolution path
  (`panem_bot.services.dialogue` or wherever that service currently
  lives) through a new thin `panem_api` endpoint, rather than building a
  second dialogue engine. The 3D panel is a new *client* for existing
  dialogue logic — same NPC personality/traits/relationship-aware
  response generation `/talk` already does, reached by walking up instead
  of typing a command.
- **FR-3D-012.** Relationship-gated content already exists server-side
  (e.g. black-market contact reveal gated on `RelationshipRow` stance).
  The 3D dialogue panel surfaces whatever the existing service already
  decides to reveal — no new gating logic, just a new place that logic's
  output is shown.
- **FR-3D-013.** A short, non-blocking speech-bubble/toast for ambient
  NPC chatter (`panem_sim.systems.npc_chatter`, if that system already
  produces district-ambient lines) appears above an NPC's head when
  they're within render range, independent of direct interaction —
  cosmetic flavor, not a new system, surfacing something the sim already
  generates.

### 7.3 Visible jobs

- **FR-3D-014.** An NPC whose current `location_id` is their `job_id`'s
  workplace and whose schedule has them "at work" plays a distinct idle
  animation variant (the Blocky Characters pack ships 27 clips per
  character including several that read as "working" — e.g. `interact-
  right`/`interact-left`/`holding-*` — picked deterministically per job
  category, same "honest, stable, not semantically perfect" posture
  `block_models` already documents for appearance). Purely cosmetic;
  no new job-state tracking needed beyond what schedule/shift systems
  already expose.

### 7.4 NPC appearance curation (future, not blocking)

`block_models.assign_npc_appearance`'s own docstring already flags this
honestly: the 18 shapes/palettes aren't labeled by role. This spec
doesn't attempt to fix that (needs an artist actually looking at the 18
previews and curating real per-job/per-trait mappings) — it's called out
here as a named future task (`FR-3D-015`, unassigned) so it isn't lost,
not because this document is solving it.

---

## 8. Player Interactions in 3D

Every item below is an **additional** entry point into an existing system
(G3) — none of this removes the Discord command or dashboard tab that
already does the same thing.

### 8.1 Work

- **FR-3D-020.** Standing at your character's own job's workplace
  location (already resolvable server-side — `job_id` → `workplace`
  location, same lookup `/work`'s own eligibility check already does) and
  pressing E launches the existing Work minigame (`work.html`/`work.js`)
  embedded as an in-3D overlay panel (reusing the overlay-host pattern
  `pets_panel.js`/`customizer.js` already establish) instead of only
  being reachable via the Discord `/work` command's launch button. Same
  endpoint (`/activity/work/{shift_id}/result`), new trigger.
- Proximity replaces `can_work_from_current_location`'s existing
  district/home check as the 3D entry condition — i.e. in 3D, "at the
  right place" means standing at the actual workplace plot, a strictly
  *more* specific version of the check that already gates `/work`.

### 8.2 Crime

- **FR-3D-021.** `/steal`'s target becomes "the nearest player/NPC within
  interaction range who isn't you," `/burgle`'s target becomes "the
  residential plot you're standing at, if someone's property is there,"
  `/poach`/`/lockpick`/shipment-interception similarly become proximity-
  triggered at their already-existing eligible locations (the district's
  `outskirts`/jail/etc.), each still resolving through the exact same
  `panem_shared.stealing`/`panem_shared.jail` service calls and the
  existing `crime.html` minigame — only the targeting/launch UX moves
  into 3D. The Discord/dashboard Crime tab's own targeting (name
  autocomplete, owner dropdown) keeps working unmodified (G3).
- **FR-3D-022.** A district's Peacekeeper presence (`culture.peacekeeper_
  pressure`, already a per-district content value) spawns a small number
  of non-interactive Peacekeeper-dressed NPCs patrolling near government/
  jail locations, scaled by that value — ambient tension, cosmetic only,
  no new mechanic (the actual arrest/heat system is entirely unchanged,
  server-side, as today).

### 8.3 Trade

- **FR-3D-023.** Standing near another connected player and pressing a
  trade-specific interaction key (distinct from E, to avoid colliding
  with "talk to the NPC who happens to also be nearby") opens the
  existing `/pay`/`/trade` flow's UI as a 3D overlay panel, prefilled with
  the other player's character — same `Trade` table, same service layer,
  new discovery path (walk up to them) instead of typing their name.

### 8.4 Scenes & RP visualization

- **FR-3D-024.** A `Scene`/engagement already has a location. While it's
  open, participating characters' 3D avatars gain a small visible marker
  (e.g. a ring/icon) at that location so other players in the zone can
  see "RP is happening here" and walk over — pure presence signal, the
  scene's actual content stays in Discord/the proxy system exactly as
  today; 3D never renders RP text.
- **FR-3D-025.** A short, auto-fading chat bubble above a character's
  head when they proxy a short message (length-capped, e.g. under ~80
  characters) in a channel the zone's players are also "in" — ambient
  flavor matching what's already being said in Discord, not a new
  message surface; long-form RP stays exclusively in Discord/scenes. This
  item is explicitly the most speculative/lowest-priority item in this
  section (§12 phases it last) because getting the Discord-channel-to-
  zone mapping and spam/abuse posture right needs its own design pass.

### 8.5 Market/Blackmarket physical stalls

- **FR-3D-026.** The Market location already generated in every district
  (and the illicit one, e.g. District 12's Hob/District 7's Hollow Stump
  — already content-modeled as a `kind: market`, `illicit: true`
  location) gets dressed with Mini Market kit pieces (already vendored
  for the Capitol's pet store, currently otherwise unused) and a 3D entry
  point into the existing `/market`/`/blackmarket` buy/sell flow,
  mirroring exactly how the pet store already does this (§2) — same
  pattern, extended to goods trading.

### 8.6 Housing & 8.7 Pets

Already built (§2), generically (not Capitol-specific) — extending to
every district is a consequence of §6's theming work, not new engineering.
No new FR items; flagged here only for completeness of "what happens to
every existing 3D feature once all 13 districts exist."

---

## 9. Travel: the connective tissue

### 9.1 Trains

Canon-appropriate and already asset-ready: the vendored library includes
a Train Kit, currently unused.

- **FR-3D-030.** District 6 (transportation, §6.2) and every district's
  `station`-kind location (already present in every district's content —
  confirmed present in both District 7 and District 12's location lists
  inspected while writing this spec) render actual train-kit rolling
  stock at the platform.
- **FR-3D-031.** The transit scene described in §5.2 is the interior of
  one train car, built once (not per-district), shared by every in-
  transit player regardless of origin/destination — the cheap, zone-
  independent "non-zone" this spec deliberately keeps minimal.

### 9.2 Transit state

Entirely a consumer of the existing `Character.in_transit_until_tick`/
`.transit_destination_id` fields and whatever resolves them today
(`panem_shared.travel` / the sim's own tick handling) — no new travel-
state modeling. See §5.2 for the client-side behavior.

### 9.3 What travel does *not* do

Does not make two districts simultaneously renderable, does not let a
player "look back" at the district they left, does not change how travel
time/eligibility is computed server-side.

---

## 10. Technical Architecture

### 10.1 Backend: `Map3DRoom` as the federation unit

`Map3DRoom` is already keyed per `district_id` and already lazily created
per the `build_room_router` closure's `rooms: dict[int, Map3DRoom]`. Scaling
this to 13 districts needs:

- **FR-3D-040.** No architectural change to `Map3DRoom` itself — the
  existing per-district dict-of-rooms, single-process, single-event-loop
  design already generalizes (it was never actually Capitol-specific;
  only the *caller* — `tabs/map.js`'s district check — restricts it
  today). Removing that restriction (§12 Phase A) is a frontend change,
  not a backend one.
- **FR-3D-041.** Each room's own tick loop (`Map3DRoom._run`) already
  only runs while it has connections (`while self.connections:`) and
  exits otherwise — an empty district's room costs nothing when nobody's
  there, which matters once there are 13 of them instead of 1.
- **FR-3D-042.** The NPC roster per room (`seed_npcs`) already queries
  by `district_id` — no change needed; this already scales to "however
  many districts get connected to."

### 10.2 Frontend: zone loading & asset streaming

- **FR-3D-043.** `static/tabs/map.js`'s district check (`CAPITOL_DISTRICT_
  ID`) becomes a per-district capability flag driven by content (does
  this district have a `theme`? — see §6.1's fallback behavior: every
  district technically *can* render in 3D today via the Commercial-kit
  fallback, so this is really a rollout gate, not a technical one) rather
  than a single hardcoded id.
- **FR-3D-044.** `static/map3d/loader.js`'s existing per-URL model/texture
  cache already means switching districts only fetches assets the
  previous district didn't already use — no new caching layer needed, but
  worth stating as a requirement: a theme's asset set should be fetched
  lazily (on first building of that theme needing it), not prefetched for
  all 13 districts on first Activity load.
- **FR-3D-045 (future, not blocking any phase in §12).** If per-zone
  draw-call counts become a real problem (§10.5), convert the repeated
  road-tile/filler-building instances to `THREE.InstancedMesh` — flagged
  as a known lever, not implemented speculatively.

### 10.3 Data model changes

Net new, beyond what §6–§9 already called out inline:

| Change | Where | Reason |
|---|---|---|
| `DistrictMap.theme: str \| None` | content schema | FR-3D-001 |
| `Location.render: Literal["building","open"] = "building"` | content schema | FR-3D-003 |
| `Npc.previous_location_id: str \| None` | DB migration | FR-3D-010 |
| *(no new tables)* | — | Every other item in this spec reuses existing tables (`Scene`, `Trade`, `RelationshipRow`, `Character.in_transit_until_tick`, etc.) by design — see G3/G5. |

Deliberately small. This spec's size is in *wiring existing systems into a
new client*, not in new persisted state.

### 10.4 API surface additions

New endpoints needed, grouped by section, all under the existing
`panem_api.map3d_routes`-style per-feature router pattern:

- `GET /activity/dashboard/map3d/{district_id}/layout` — **unchanged**,
  already generic.
- `POST /activity/dashboard/map3d/{character_id}/talk` (§7.2) — thin
  wrapper around the existing dialogue service.
- `POST /activity/dashboard/map3d/{character_id}/work/launch` (§8.1) —
  resolves proximity eligibility, otherwise delegates to the existing
  shift-opening logic `/work` already uses.
- `POST /activity/dashboard/map3d/{character_id}/crime/{kind}/launch`
  (§8.2) — proximity-resolved target, otherwise identical to the
  existing `*-start` crime endpoints already in `dashboard_routes.py`.
- `POST /activity/dashboard/map3d/{character_id}/trade/open` (§8.3).
- `WS /ws/map3d/{district_id}/room` — **unchanged**, already generic;
  gains `previous_location_id` on NPC payloads (FR-3D-010) and scene-
  marker data (FR-3D-024) in its snapshot.

### 10.5 Performance budget

Stated as a requirement, not just a hope:

- **FR-3D-046.** A district's 3D scene (all buildings + roads + NPCs +
  connected players) should stay under roughly 800 draw calls on first
  load — comfortably under what a Discord Activity iframe handles on a
  mid-range laptop, with headroom before FR-3D-045's instancing lever is
  needed. The existing Capitol grid (§2) sits around 500; this is a
  budget to measure new themed districts against, not a hard cap to
  enforce in code.
- **FR-3D-047.** Switching districts should fully unmount the previous
  `Map3DRoom` connection, renderer scene graph, and any in-flight model/
  texture loads before mounting the next — `tabs/map.js`'s existing
  `teardown3D()` pattern, generalized rather than reinvented.

---

## 11. Security / Moderation / Trust Posture

Unchanged, explicitly: this spec adds no new auth model. Every new
endpoint in §10.4 uses the exact same discord_id/character_id ownership
check (`_resolve_owned_character`) every existing `map3d_routes` endpoint
already uses, with the same documented gap (no real Discord token
verification) the rest of `panem_api` already has. Moderation tools
(staff commands, bans, kills) are untouched — they operate on the same
`Character`/`Npc` rows regardless of which zone, or whether any zone, a
player happens to be standing in.

---

## 12. Phased Roadmap

Each phase is independently shippable and leaves the game in a complete,
playable state — not a sequence of half-finished features.

**Phase A — All districts, no new interaction.**
Ship FR-3D-001/002/003/004 (theming mechanism + fallback) and FR-3D-043
(remove the Capitol-only gate). Every district renders in 3D using the
Commercial-kit fallback at minimum; districts with an authored `theme`
(start with 2–3, e.g. District 12 and District 7, the two most visually
distinct from the Capitol) get their real look. Acceptance: every
district's Map tab shows a walkable 3D city; NPCs wander; existing
pet-store/housing-interior flows work in every district, not just the
Capitol.

**Phase B — NPCs you can talk to.**
FR-3D-010/011/012/013. Acceptance: walking up to any NPC and pressing E
opens real dialogue, relationship-gated content included, matching what
`/talk` already produces for that NPC.

**Phase C — Work and Crime in 3D.**
FR-3D-020/021/022. Acceptance: a character can complete a full work shift
and a full steal/burgle attempt without ever leaving the 3D view or
typing a Discord command, with identical outcomes to doing it the
existing way.

**Phase D — Travel made visible.**
FR-3D-030/031, §5.2's transition scene, §9. Acceptance: traveling between
two districts shows a real transit scene for the window `in_transit_
until_tick` already defines, landing the player connected to the
destination district's room on arrival.

**Phase E — Trade and Scene presence.**
FR-3D-023/024/026. Acceptance: two players can discover each other in 3D
and complete a trade; an open scene is visible as a marker to other
players in the same zone; market/blackmarket goods trading has a 3D entry
point in every district with a market location.

**Phase F — Remaining districts' themes + polish.**
Finish authoring `theme`s for every district not yet covered in Phase A,
FR-3D-014 (visible jobs), FR-3D-025 (chat bubbles, lowest priority per
§8.4), FR-3D-045/046/047 performance work if the budget in §10.5 is being
exceeded in practice.

Each phase's own PR(s) should extend `tests/unit/test_map3d.py` the same
way the existing 3D work already has (pure-function coverage for anything
deterministic, real-Postgres end-to-end verification for anything
DB-backed) and get an equivalent README changelog entry, matching this
project's existing documentation convention.

---

## 13. Open Questions & Risks

- **Authoring burden.** Thirteen districts' worth of theme tuning (§6.2)
  is real content work, not just engineering — the table in §6.2 is a
  starting point, likely to need iteration once someone actually looks at
  a themed district rendered.
- **Asset licensing.** All proposed new kits are from the same CC0 Kenney
  source already in use — no new licensing risk, but each new kit needs
  the same curate-and-cross-check vendoring pass (§6.1) already
  established, which is real (if mechanical) work per kit.
- **Draw-call budget in practice.** §10.5's numbers are estimates;
  Phase A should include an actual measurement pass across a couple of
  themed districts before Phase F's polish work is scoped in detail.
  FR-3D-045 (instancing) is the lever if the budget doesn't hold.
  Measurement requires a real browser (this spec, like the work it builds
  on, was authored without the ability to render WebGL directly — Phase A
  planning should budget time for an actual visual/perf pass, not assume
  the numbers here are exact).
- **NPC-chatter/chat-bubble scope (§8.4, §8.5).** The exact mapping from
  "a Discord channel's proxied message" to "which zone's players see a
  bubble" needs its own small design pass before FR-3D-025 is built —
  flagged as the most speculative item in this document on purpose.
  **This is advisory, not a decision Claude is authorized to make** —
  whoever scopes Phase E's PR should write that two-paragraph mapping
  decision explicitly before building it.
- **District 6's double duty.** Theming District 6 as the canon
  transportation hub (§6.2) and also being where the shared transit scene
  conceptually "lives" (§9.1) is a nice canon fit but not load-bearing —
  the transit scene is explicitly zone-independent (§5.2/§9.1), so this
  is flavor, not an architectural dependency worth protecting later.

---

## 14. Functional Requirements Index

| ID | Section | One-line summary |
|---|---|---|
| FR-3D-001 | 6.1 | `DistrictMap.theme` content field |
| FR-3D-002 | 6.1 | Per-theme building/filler model tables |
| FR-3D-003 | 6.1 | `Location.render: "open"` hint |
| FR-3D-004 | 6.1 | Per-theme asset vendoring + cross-check |
| FR-3D-010 | 7.1 | `Npc.previous_location_id`, animated relocation |
| FR-3D-011 | 7.2 | 3D dialogue panel via existing `/talk` service |
| FR-3D-012 | 7.2 | Relationship-gated content surfaced, not re-implemented |
| FR-3D-013 | 7.2 | Ambient NPC chatter bubbles |
| FR-3D-014 | 7.3 | Visible "at work" NPC animation variant |
| FR-3D-015 | 7.4 | (Unassigned) curated per-job NPC appearance mapping |
| FR-3D-020 | 8.1 | In-3D Work minigame launch |
| FR-3D-021 | 8.2 | Proximity-targeted Crime minigame launch |
| FR-3D-022 | 8.2 | Ambient Peacekeeper patrol density |
| FR-3D-023 | 8.3 | In-3D Trade panel launch |
| FR-3D-024 | 8.4 | Scene/engagement presence marker |
| FR-3D-025 | 8.4 | Proxied-message chat bubbles (lowest priority) |
| FR-3D-026 | 8.5 | In-3D Market/Blackmarket entry point |
| FR-3D-030 | 9.1 | Train rolling stock at station locations |
| FR-3D-031 | 9.1 | Shared, zone-independent transit scene |
| FR-3D-040 | 10.1 | `Map3DRoom` generalization (no code change needed) |
| FR-3D-041 | 10.1 | Per-room tick loop idles when empty |
| FR-3D-042 | 10.1 | NPC roster query already generic |
| FR-3D-043 | 10.2 | Remove Capitol-only district gate |
| FR-3D-044 | 10.2 | Lazy per-theme asset fetching |
| FR-3D-045 | 10.2 | `InstancedMesh` lever (future, not blocking) |
| FR-3D-046 | 10.5 | ~800 draw-call budget per district |
| FR-3D-047 | 10.5 | Full zone teardown on district switch |
