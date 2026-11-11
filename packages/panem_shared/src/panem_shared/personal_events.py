"""Personal event chains (Spec's own example: "fishing up a toy -> who
does this belong to? -> analyze -> find the person -> deliver -> get a
reward"). Only one chain kind exists today -- `"lost_item"`, startable
from any hobby that lists a `rare_find_chain` (`panem_shared.hobbies.
resolve_session`) -- but `PersonalEventChain.chain_kind`/`.state` are
generic enough that a second kind is just a new set of functions here,
no schema change.

`_LOST_ITEM_FLAVOR` is code-authored, not content-YAML, the same choice
`panem_shared.reactions.SCENARIOS` makes: three short strings per hobby
is easier to read and review as Python than as a new catalog file+schema+
loader for something this small.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_shared import constants, relationships
from panem_shared.content.schemas import Hobby
from panem_shared.db.models import Character, Npc, PersonalEventChain
from panem_shared.enums import EventChainStatus
from panem_shared.errors import NotAllowed, NotFound

_LOST_ITEM_FLAVOR: dict[str, tuple[str, str]] = {
    "fishing": ("a waterlogged toy soldier", "snagged on your line"),
    "gardening": ("a tarnished locket", "turned up while tilling the soil"),
    "baking": ("a sealed, hand-written recipe box", "wedged in the bottom of a flour sack"),
}
_DEFAULT_FLAVOR = ("a battered keepsake", "turned up out of nowhere")


async def has_active_chain(session: AsyncSession, character_id: int, chain_kind: str) -> bool:
    result = await session.execute(
        select(PersonalEventChain).where(
            PersonalEventChain.character_id == character_id,
            PersonalEventChain.chain_kind == chain_kind,
            PersonalEventChain.status == EventChainStatus.ACTIVE.value,
        )
    )
    return result.scalar_one_or_none() is not None


async def active_chains(session: AsyncSession, character_id: int) -> list[PersonalEventChain]:
    result = await session.execute(
        select(PersonalEventChain).where(
            PersonalEventChain.character_id == character_id,
            PersonalEventChain.status == EventChainStatus.ACTIVE.value,
        )
    )
    return list(result.scalars().all())


async def start_chain(
    session: AsyncSession,
    *,
    character: Character,
    chain_kind: str,
    current_tick: int,
    flavor_hobby: Hobby,
) -> PersonalEventChain:
    item_name, found_how = _LOST_ITEM_FLAVOR.get(flavor_hobby.id, _DEFAULT_FLAVOR)
    chain = PersonalEventChain(
        character_id=character.id,
        chain_kind=chain_kind,
        status=EventChainStatus.ACTIVE.value,
        step=0,
        state={
            "item_name": item_name,
            "found_how": found_how,
            "hobby_id": flavor_hobby.id,
            "found_district_id": character.current_district_id,
        },
        started_tick=current_tick,
        updated_tick=current_tick,
        expires_tick=current_tick + constants.LOST_ITEM_CHAIN_EXPIRY_DAYS * 24,
    )
    session.add(chain)
    return chain


async def _candidate_npcs(
    session: AsyncSession, district_id: int, rng: random.Random
) -> list[Npc]:
    result = await session.execute(
        select(Npc).where(Npc.district_id == district_id, Npc.alive.is_(True))
    )
    npcs = list(result.scalars().all())
    rng.shuffle(npcs)
    return npcs[: constants.LOST_ITEM_INVESTIGATE_CANDIDATES]


@dataclass(frozen=True, slots=True)
class InvestigateResult:
    owner_npc_name: str
    clue: str


async def investigate(
    session: AsyncSession,
    *,
    chain: PersonalEventChain,
    current_tick: int,
    rng: random.Random,
) -> InvestigateResult:
    """Step 0 -> 1: picks the "likely owner" from a handful of NPCs in the
    district the item was found in. Raises `NotAllowed` if this chain has
    already moved past this step, `NotFound` if that district somehow has
    no NPCs at all to suspect (no shipped district is this empty)."""
    if chain.step != 0:
        raise NotAllowed("chain_already_investigated")
    district_id = chain.state["found_district_id"]
    candidates = await _candidate_npcs(session, district_id, rng)
    if not candidates:
        raise NotFound("chain_no_candidates")
    owner = rng.choice(candidates)
    clue = f"{owner.name} works near {owner.home_location_id or owner.location_id or 'town'}."
    chain.state = {**chain.state, "target_npc_id": owner.id, "target_npc_name": owner.name}
    chain.step = 1
    chain.updated_tick = current_tick
    return InvestigateResult(owner_npc_name=owner.name, clue=clue)


@dataclass(frozen=True, slots=True)
class DeliverResult:
    npc_name: str
    money: int
    reputation: float


async def deliver(
    session: AsyncSession,
    *,
    character: Character,
    chain: PersonalEventChain,
    current_tick: int,
) -> DeliverResult:
    """Step 1 -> completed: requires the character to currently be in the
    suspected owner's home district. Raises `NotAllowed` with the
    district the character still needs to reach if they aren't there
    yet, or if step 0 (`investigate`) hasn't happened."""
    if chain.step < 1:
        raise NotAllowed("chain_not_investigated")
    npc_id = chain.state.get("target_npc_id")
    npc = await session.get(Npc, npc_id) if npc_id else None
    if npc is None:
        raise NotFound("chain_target_gone")
    if character.current_district_id != npc.district_id:
        raise NotAllowed("chain_wrong_district", district_id=npc.district_id)

    character.money += constants.LOST_ITEM_REWARD_MONEY
    character.reputation += constants.LOST_ITEM_REWARD_REPUTATION
    relationship = await relationships.get_or_create_relationship(
        session, ("character", str(character.id)), ("npc", npc.id)
    )
    relationships.apply_affinity_delta(
        relationship, constants.LOST_ITEM_REWARD_AFFINITY, current_tick=current_tick
    )

    chain.status = EventChainStatus.COMPLETED.value
    chain.step = 2
    chain.updated_tick = current_tick
    return DeliverResult(
        npc_name=npc.name,
        money=constants.LOST_ITEM_REWARD_MONEY,
        reputation=constants.LOST_ITEM_REWARD_REPUTATION,
    )


async def expire_stale_chains(
    session: AsyncSession, current_tick: int
) -> list[PersonalEventChain]:
    """`panem_sim.systems.personal_events`'s daily sweep -- an active
    chain past its `expires_tick` with no `investigate`/`deliver` just
    quietly expires rather than blocking a fresh one of the same kind
    forever."""
    result = await session.execute(
        select(PersonalEventChain).where(
            PersonalEventChain.status == EventChainStatus.ACTIVE.value,
            PersonalEventChain.expires_tick.is_not(None),
            PersonalEventChain.expires_tick <= current_tick,
        )
    )
    expired = list(result.scalars().all())
    for chain in expired:
        chain.status = EventChainStatus.EXPIRED.value
        chain.updated_tick = current_tick
    return expired
