"""Pydantic schemas for `data/*.yaml` content files (Spec §5.4)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from panem_shared.enums import CollectibleRarity, DayPhase, Gender, LocationKind, Season

# Forum tags = one per location + `Open` + `Closed`, capped at 20 tags
# per Discord forum (Plan §1.3), so at most 18 locations per district.
MAX_LOCATIONS_PER_DISTRICT = 18


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: LocationKind
    capacity: int = 100
    illicit: bool = False
    restricted: bool = False
    job_ids: list[str] = Field(default_factory=list)
    access_items: list[str] = Field(default_factory=list)
    access_jobs: list[str] = Field(default_factory=list)
    radius: int = 60


class DistrictQuota(BaseModel):
    model_config = ConfigDict(extra="forbid")

    good: str
    amount: float


class DistrictCulture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tone: list[str] = Field(default_factory=list)
    idioms: list[str] = Field(default_factory=list)
    taboos: list[str] = Field(default_factory=list)
    peacekeeper_pressure: float = Field(default=0.3, ge=0.0, le=1.0)
    capitol_loyalty: float = Field(default=0.3, ge=0.0, le=1.0)


class DistrictMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str
    width: int
    height: int
    location_coords: dict[str, tuple[int, int]]


class District(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0, le=12)
    name: str
    industry: str
    produces: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    illicit_produces: list[str] = Field(default_factory=list)
    """Contraband goods this district's illicit jobs (`Character.job_is_
    illicit`) can produce -- unlike `produces`/`imports`, these never flow
    through the national Capitol-cut/redistribution pipeline
    (`panem_sim.systems.economy`): a district's black market only ever
    stocks what its own illicit workers made that day."""
    quota: DistrictQuota | None = None
    population_base: int
    culture: DistrictCulture
    locations: list[Location]
    map: DistrictMap

    @model_validator(mode="after")
    def _check_locations(self) -> District:
        if len(self.locations) > MAX_LOCATIONS_PER_DISTRICT:
            raise ValueError(
                f"district {self.id} has {len(self.locations)} locations, "
                f"max is {MAX_LOCATIONS_PER_DISTRICT} (Plan §1.3 forum tag cap)"
            )

        ids = [loc.id for loc in self.locations]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"district {self.id} has duplicate location ids: {sorted(dupes)}")

        # Discord forum tags are keyed by name, not id (FR-SCN-1: "tag name =
        # location name"), and Discord rejects duplicate tag names outright --
        # two locations with different ids but the same name still collide.
        names = [loc.name for loc in self.locations]
        name_dupes = {n for n in names if names.count(n) > 1}
        if name_dupes:
            raise ValueError(
                f"district {self.id} has duplicate location names: {sorted(name_dupes)}"
            )

        missing_coords = [i for i in ids if i not in self.map.location_coords]
        if missing_coords:
            raise ValueError(
                f"district {self.id} locations missing map.location_coords: {missing_coords} "
                "(Spec §5.4)"
            )

        # Every district YAML, the Capitol (id 0) included, needs a station
        # (FR-LOC-7) and at least one public location for the default spawn
        # location on character approval (FR-CHR-4).
        kinds = [loc.kind for loc in self.locations]
        if LocationKind.STATION not in kinds:
            raise ValueError(f"district {self.id} has no `kind: station` location (FR-LOC-7)")
        if LocationKind.PUBLIC not in kinds:
            raise ValueError(f"district {self.id} has no `kind: public` location (FR-CHR-4)")
        # A `kind: jail` location isn't required at the schema level (every
        # real district under `data/` ships exactly one -- enforced by
        # `tests/unit/test_content_loader.py` instead, the same way the
        # style checks below don't hard-require every content-authored
        # kind). Keeping it a soft convention rather than a third required
        # kind here avoids forcing every hand-built `District(...)` test
        # fixture across the suite (many of them predate jail entirely) to
        # carry one just to satisfy validation; `panem_shared.jail.find_
        # jail_location` and its callers already treat "no jail location"
        # as a normal, handled case rather than an invariant violation.
        jail_kinds = [k for k in kinds if k == LocationKind.JAIL]
        if len(jail_kinds) > 1:
            raise ValueError(f"district {self.id} has {len(jail_kinds)} `kind: jail` locations")

        return self


class Good(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    name: str
    base_price: float = Field(gt=0)
    perishable: bool = False
    category: str
    rationed: bool = False
    hunger_value: float = Field(default=0.0, ge=0.0)
    """How much `Character.hunger` a Vitals-tab `/eat` of one unit relieves
    (0.0 = not edible, the default for every non-food good). Doubled by
    `constants.COOK_BONUS_MULTIPLIER` when eaten via the cook/bake minigame
    with a landed bonus (`cook_method` must be set for that to apply) --
    see `panem_shared.sustenance`."""
    thirst_value: float = Field(default=0.0, ge=0.0)
    """Same shape as `hunger_value`, for `/drink` (0.0 = not drinkable).
    No minigame/bonus applies to drinking."""
    heal_value: float = Field(
        default=0.0,
        ge=0.0,
        validation_alias=AliasChoices("heal_value", "health_value"),
    )
    """How much `Character.health` a Vitals-tab `/heal` of one unit restores
    (0.0 = not healing, the default for every non-medical good). Bounded by
    `constants.HEALTH_MAX` -- see `panem_shared.sustenance`."""
    cook_method: Literal["stove", "oven"] | None = None
    """Which Vitals-tab minigame (`static/games/cook.js` for `"stove"`,
    `bake.js` for `"oven"`) can be played on this good for the cook bonus.
    `None` means straight-eat only, no bonus available -- also `None` for
    every non-edible good (`hunger_value == 0.0`)."""

    @property
    def health_value(self) -> float:
        return self.heal_value


class JobOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    output_mult: float = 1.0
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_effect: dict[str, Any] | None = None
    rep_delta: int = 0
    wage_mult: float = 1.0


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    district: int = Field(ge=0, le=12)
    title: str
    workplace: str
    wage: float = Field(ge=0)
    produces: dict[str, float] = Field(default_factory=dict)
    shift_phase: DayPhase
    slots: int = Field(gt=0)
    legal: bool = True
    staff_only: bool = False
    """A job players can't reach via `/job apply` (e.g. a district's mentor
    slot) -- only staff can assign it, via `/staff give job`."""
    min_reputation: float | None = None
    ladder_next: str | None = None
    ladder_requirement: dict[str, Any] | None = None
    peacekeeper_attention: float | None = Field(default=None, ge=0.0, le=1.0)
    options: list[JobOption]
    foreman_npc_id: str | None = None

    @model_validator(mode="after")
    def _check_options(self) -> Job:
        if len(self.options) != 3:
            raise ValueError(
                f"job {self.id} must define exactly 3 options (FR-JOB-3), got {len(self.options)}"
            )
        return self


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_: int = Field(alias="from", ge=0, le=12)
    to: int = Field(ge=0, le=12)
    good: str
    capacity: float = Field(ge=0)


class NpcContent(BaseModel):
    """A hand-authored (or generator-authored, `scripts/npc_generate.py`)
    resident living in `data/npcs/d<district>.yaml` (Plan §6.1/§11, Spec
    §5.4) -- real identity/flavor text for a specific NPC, as opposed to
    the fully-synthetic population `panem_sim.world.seed_npcs` falls back
    to for any district with no authored file. `backstory`/`appearance`
    are display-only flavor text, never simulated, so they live here in
    content rather than as DB columns on `Npc`."""

    model_config = ConfigDict(extra="forbid")

    id: str
    district: int = Field(ge=0, le=12)
    name: str
    age: int = Field(ge=12, le=90)
    gender: Gender | None = None
    """Unset means `panem_sim.world._seed_authored_npcs` assigns one at
    random (same rng as the fully-synthetic population), so pre-existing
    authored files with no `gender` line still get a real value rather
    than silently reading as "they/their" forever."""
    job_id: str | None = None
    home_location_id: str
    traits: list[str] = Field(default_factory=list)
    backstory: str = Field(max_length=1500)
    appearance: str = Field(default="", max_length=400)
    black_market_contact: bool = False
    """Marks this NPC as their district's black-market "fence" (contraband
    system) -- `panem_bot.services.blackmarket.resolve_fence` gates
    `/blackmarket` access behind a character's `RelationshipRow` stance
    with *this specific* NPC, not just anyone in the district."""


class PetSpecies(BaseModel):
    """A purchasable pet species (`data/pets.yaml`) -- the Capitol Market's
    pet store catalog (`panem_api.map3d_routes`'s `/pets/*` routes). `model`
    is the vendored Cube Pets asset key (`static/assets3d/pets/animal-
    {model}.glb`), kept separate from `id` so flavor/pricing can be reskinned
    without touching the asset reference, though today every species uses
    its own model 1:1."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    model: str
    price: float = Field(gt=0)
    description: str = Field(default="", max_length=200)


class FurniturePiece(BaseModel):
    """One vendored Furniture Kit asset (`data/furniture.yaml`) --
    `static/assets3d/furniture/{item_key}.glb`. `structural` pieces (walls,
    floors, doorways, stairs) are only ever placed by the default-layout
    generator (`panem_shared.housing3d.default_layout`) to build a
    property's fixed room shell; the housing-interior catalog the dashboard
    offers for free placement (`panem_api.map3d_routes`'s `/housing3d/*`
    catalog route) excludes them, same split a real interior designer would
    draw between "the room" and "what's in it"."""

    model_config = ConfigDict(extra="forbid")

    item_key: str
    name: str
    category: str
    structural: bool = False
    footprint_w: float = Field(default=1.0, gt=0)
    footprint_d: float = Field(default=1.0, gt=0)


class CollectibleDrop(BaseModel):
    """One `Hobby`'s weighted entry in `data/collectibles.yaml` -- `weight`
    is relative within the hobby's own table only (never compared across
    hobbies), and `min_level` (a `JobLevel` value, reused from the job
    mastery ladder -- see `panem_shared.hobby_levels`) keeps a hobby's
    rarest/most setting-specific finds out of reach until a character has
    actually put in sessions, the same way `panem_shared.stealing`'s
    burgle targets tier by job level."""

    model_config = ConfigDict(extra="forbid")

    collectible_id: str
    weight: float = Field(gt=0)
    min_level: str = "apprentice"


class Hobby(BaseModel):
    """A `data/hobbies.yaml` entry -- one of the up to 3 a character may
    pick (`CharacterHobby`, `panem_shared.hobbies`). `tool_good_id` is a
    `data/goods.yaml` id (a rod, trowel, mixing bowl, ...): owning at
    least one unit in `Inventory` raises the timing minigame's margin for
    error, but it's never required to play at all -- bare-handed always
    works, just worse (Spec doesn't gate hobbies behind a purchase)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    verb: str
    """Flavors the session prompt, e.g. "cast your line", "turn the
    soil", "knead the dough" -- `panem_bot.cogs.hobbies` interpolates it
    directly into the timing-minigame embed."""
    description: str = Field(default="", max_length=300)
    tool_good_id: str | None = None
    drops: list[CollectibleDrop] = Field(default_factory=list)
    rare_find_chain: str | None = None
    """A `panem_shared.personal_events` chain kind (e.g. `"lost_item"`) a
    rare/legendary catch can kick off instead of going straight to
    inventory -- `None` means this hobby never triggers one."""


class Collectible(BaseModel):
    """A `data/collectibles.yaml` entry -- the museum-displayable item a
    `CollectibleInstance` row points at by `id`. `hobby_id` is `None` for
    a collectible only ever granted by a personal/global event, never a
    plain hobby session roll."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    hobby_id: str | None = None
    rarity: CollectibleRarity = CollectibleRarity.COMMON
    wing: str = "general"
    """Default museum grouping (`panem_shared.museum`'s "by_wing" preset)
    -- a themed room name like "Aquatic Life" or "Oddities & Curiosities"."""
    description: str = Field(default="", max_length=300)
    donation_reward: int = Field(default=10, ge=0)


class SeasonRange(BaseModel):
    """One season's `(low, high)` Fahrenheit range for a `DistrictClimate`
    -- `panem_shared.weather.generate_forecast_values` picks each day's
    actual forecast uniformly within it."""

    model_config = ConfigDict(extra="forbid")

    low: int
    high: int


class DistrictClimate(BaseModel):
    """A `data/climate.yaml` entry -- one per district, keyed by `Season`
    rather than month so authoring stays independent of `DAYS_PER_MONTH`/
    `TICKS_PER_DAY`. `inclement` lists which `WeatherKind`s this
    district's weather can ever roll and in which seasons -- a district
    with no winters authored for `flash_flood` simply never gets one in
    winter, no separate exclusion list needed."""

    model_config = ConfigDict(extra="forbid")

    district_id: int = Field(ge=0, le=12)
    spring: SeasonRange
    summer: SeasonRange
    autumn: SeasonRange
    winter: SeasonRange
    inclement: dict[Season, list[str]] = Field(default_factory=dict)

    def range_for(self, season: Season) -> SeasonRange:
        return {
            Season.SPRING: self.spring,
            Season.SUMMER: self.summer,
            Season.AUTUMN: self.autumn,
            Season.WINTER: self.winter,
        }[season]
