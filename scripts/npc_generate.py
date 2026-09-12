#!/usr/bin/env python3
"""Generate real, authored NPC content (`data/npcs/d<district>.yaml`, Plan
§6.1/§11, Spec §5.4) from name/trait pools plus each district's own
industry/culture -- deterministic per district + world-seed, same pattern
as `panem_sim.rng.seed_rng`.

This is templated procedural prose, not hand-written literary backstory:
combinatorial sentences built from a fixed bank of district-flavored and
trait-flavored fragments (`_ORIGIN_TEMPLATES`/`_TRAIT_FLAVOR`/
`_JOB_TEMPLATES` below), picked deterministically per NPC. It's meant as
a real, permanent starting point staff/writers can hand-edit afterward
(`data/npcs/*.yaml` is just YAML) -- not a substitute for genuine
authored content, and not the same thing as Phase 6's planned LLM
dialogue generation.

Idempotent per district: re-running overwrites that district's own
`data/npcs/d<id>.yaml` file (`capitol.yaml` for the Capitol) with a
freshly (but deterministically, for a given `--seed`) generated set --
running it twice with the same seed produces byte-identical output.
`panem_sim.world.seed_npcs` only consults these files the *first* time a
district is ever seeded into a fresh world, so regenerating content here
has no effect on an already-running world without a DB wipe.

Usage:
    uv run python scripts/npc_generate.py                # every district
    uv run python scripts/npc_generate.py --district 3   # one district
    uv run python scripts/npc_generate.py --seed my-seed  # different pool
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import yaml

from panem_shared import constants
from panem_shared.content.loader import ContentBundle, load_content
from panem_shared.content.names import sample_names
from panem_shared.content.schemas import District, Job
from panem_shared.content.traits import sample_traits
from panem_sim.rng import seed_rng
from panem_sim.world import _assign_job, _home_location

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

_ORIGIN_TEMPLATES = (
    "{name} was born and raised right here in {district}, {tone} like most of the "
    "{industry} families around them.",
    "{name} grew up in {district}'s back streets, the kind of {industry} household where "
    "everyone pitches in young.",
    "Nobody in {name}'s family has ever left {district}; the {industry} work runs in the "
    "blood, generation after generation.",
    "{name} came up in a {tone} corner of {district}, one of the many faces the "
    "{industry} trade never seems to run short of.",
    "{name}'s earliest memory is the smell of {industry} work -- {district} doesn't let "
    "you forget what feeds it.",
)

_JOB_TEMPLATES = (
    "These days {name} works as {article}{title} at {workplace}, and has the calluses to prove it.",
    "{name} spends most days as {article}{title}, putting in hours at {workplace} like "
    "everyone else on the block.",
    "{name} took up work as {article}{title} at {workplace} -- steady enough, if nothing "
    "to brag about.",
)

_UNEMPLOYED_TEMPLATES = (
    "{name} scrapes by without steady work, picking up whatever odd jobs {district} has to offer.",
    "There's no formal post for {name} right now -- just favors traded and hours filled "
    "however they can be.",
    "{name} hasn't found steady work; the days get filled with small errands and smaller change.",
)

_TRAIT_FLAVOR: dict[str, str] = {
    "stoic": "doesn't say much, and what little they do say tends to be the truth",
    "gossipy": "knows everyone's business before they've finished telling it",
    "hot-tempered": "has a short fuse that's gotten them in trouble more than once",
    "kind": "will give away a meal they can't spare without a second thought",
    "suspicious": "trusts almost no one, and says so plainly",
    "cheerful": "somehow keeps smiling no matter how the week's gone",
    "bitter": "carries an old grudge like it happened yesterday",
    "brave": "walks toward trouble other people walk away from",
    "cowardly": "keeps to the back of any crowd, just in case",
    "proud": "won't ask for help even when they plainly need it",
    "humble": "shrugs off compliments like they don't quite fit",
    "sharp-tongued": "has a tongue sharper than most peacekeepers' patience",
    "gentle": "handles even strangers like something breakable",
    "ambitious": "is always angling for something better than this",
    "lazy": "does exactly as much as gets by, and not a step more",
    "loyal": "would stand by a friend even when it costs them",
    "paranoid": "checks over their shoulder more than anyone should have to",
    "reckless": "takes chances that make their neighbors wince",
    "cautious": "weighs every decision twice before making it once",
    "witty": "has a line ready for every occasion, wanted or not",
    "shy": "goes quiet the moment a stranger's in the room",
    "boastful": "never tells a story without making themself the hero of it",
    "patient": "can outwait anyone, on anything",
    "vengeful": "doesn't forget a wrong, and doesn't forgive one either",
}

_APPEARANCE_TEMPLATES = (
    "A {age}-year-old with {industry}-worn hands and tired eyes.",
    "{age} years old, lean and weathered from years of {industry} work.",
    "Looks older than {age} -- {district} does that to people.",
    "A {age}-year-old who still moves like someone half that age.",
)


def _article(word: str) -> str:
    return "an " if word[:1].lower() in "aeiou" else "a "


def _industry_desc(industry: str) -> str:
    return industry.replace("_", " ")


def _backstory(
    rng: random.Random, district: District, job: Job | None, traits: list[str], name: str
) -> str:
    industry = _industry_desc(district.industry)
    tone = rng.choice(district.culture.tone) if district.culture.tone else "hardworking"
    origin = rng.choice(_ORIGIN_TEMPLATES).format(
        name=name, district=district.name, industry=industry, tone=tone
    )

    if job is not None:
        workplace = next(
            (loc.name for loc in district.locations if loc.id == job.workplace), job.workplace
        )
        occupation = rng.choice(_JOB_TEMPLATES).format(
            name=name, article=_article(job.title), title=job.title, workplace=workplace
        )
    else:
        occupation = rng.choice(_UNEMPLOYED_TEMPLATES).format(name=name, district=district.name)

    trait_lines = [_TRAIT_FLAVOR[t] for t in traits if t in _TRAIT_FLAVOR]
    personality = ""
    if trait_lines:
        personality = f"{name} {trait_lines[0]}"
        if len(trait_lines) > 1:
            personality += f", and {trait_lines[1]}"
        personality += "."

    return " ".join(part for part in (origin, occupation, personality) if part)


def _appearance(rng: random.Random, district: District, age: int) -> str:
    return rng.choice(_APPEARANCE_TEMPLATES).format(
        age=age, industry=_industry_desc(district.industry), district=district.name
    )


def generate_district_npcs(
    district: District, district_jobs: list[Job], world_seed: str
) -> list[dict]:
    """Same draw order as `panem_sim.world.seed_npcs`'s synthetic path
    (home, age, job, traits) so a generated NPC looks exactly like what
    that fallback would otherwise have produced for the same seed --
    this only makes it permanent, authored content instead of ephemeral
    per-boot randomness."""
    rng = seed_rng(world_seed, f"npcs:{district.id}")
    names = sample_names(rng, constants.SYNTHETIC_NPCS_PER_DISTRICT)
    entries = []
    for n in range(1, constants.SYNTHETIC_NPCS_PER_DISTRICT + 1):
        name = names[n - 1]
        home = _home_location(district, rng)
        age = rng.randint(18, 65)
        job_id = _assign_job(rng, district_jobs)
        job = next((j for j in district_jobs if j.id == job_id), None)
        traits = sample_traits(rng)
        entries.append(
            {
                "id": f"d{district.id}_npc_{n:03d}",
                "district": district.id,
                "name": name,
                "age": age,
                "job_id": job_id,
                "home_location_id": home.id,
                "traits": traits,
                "backstory": _backstory(rng, district, job, traits, name),
                "appearance": _appearance(rng, district, age),
            }
        )
    return entries


def _district_filename(district_id: int) -> str:
    return "capitol.yaml" if district_id == 0 else f"d{district_id}.yaml"


def write_district_npcs(data_dir: Path, district: District, entries: list[dict]) -> Path:
    npcs_dir = data_dir / "npcs"
    npcs_dir.mkdir(parents=True, exist_ok=True)
    path = npcs_dir / _district_filename(district.id)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(entries, fh, sort_keys=False, allow_unicode=True, width=100)
    return path


def generate_all(
    content: ContentBundle, data_dir: Path, world_seed: str, only_district: int | None
) -> None:
    districts = (
        [content.district(only_district)]
        if only_district is not None
        else list(content.districts.values())
    )
    for district in sorted(districts, key=lambda d: d.id):
        entries = generate_district_npcs(
            district, content.jobs_for_district(district.id), world_seed
        )
        path = write_district_npcs(data_dir, district, entries)
        print(f"Wrote {len(entries)} NPCs for district {district.id} ({district.name}) -> {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--district", type=int, default=None, help="Only generate this district id (0-12)"
    )
    parser.add_argument(
        "--seed",
        type=str,
        default="panem-long-year",
        help="World seed to derive names/traits/backstories from (default matches "
        "Settings.world_seed's default)",
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    content = load_content(args.data_dir)
    generate_all(content, args.data_dir, args.seed, args.district)


if __name__ == "__main__":
    main()
