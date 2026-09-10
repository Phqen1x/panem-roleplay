"""Pydantic schemas for `data/*.yaml` content files (Spec §5.4)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from panem_shared.enums import DayPhase, LocationKind

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

        return self


class Good(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    base_price: float = Field(gt=0)
    perishable: bool = False
    category: str
    rationed: bool = False
    kind: str = "commodity"  # "commodity" | "ticket" (Spec §2.4)

    @model_validator(mode="after")
    def _check_kind(self) -> Good:
        if self.kind not in {"commodity", "ticket"}:
            raise ValueError(
                f"good {self.id}: kind must be 'commodity' or 'ticket', got {self.kind!r}"
            )
        return self


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
