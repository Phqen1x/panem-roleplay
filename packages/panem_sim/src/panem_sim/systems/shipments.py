"""Contraband shipments arriving at each district's Rail Station
(`LocationKind.STATION`, already authored by every district). Unlike
`panem_shared.poaching`'s always-available opportunity, a shipment is a
discoverable, time-boxed one: each tick, a district with no shipment
currently sitting has a small chance (`constants.SHIPMENT_SPAWN_CHANCE_
PER_TICK`) of one arriving, and it's gone -- claimed by peacekeepers, not
a player -- after `constants.SHIPMENT_WINDOW_TICKS` if nobody's hit it by
then. `panem_shared.shipments.apply_shipment_outcome` deletes a
successfully- or unsuccessfully-attempted shipment's row itself (outside
the tick loop, the same way jail/crime consequences elsewhere in this
codebase apply immediately rather than waiting for the next tick); this
system only handles the spawn and the "nobody came" expiry.
"""

from __future__ import annotations

from panem_shared import constants
from panem_shared.db.models import Shipment
from panem_shared.enums import LocationKind
from panem_shared.events import AnyWorldEvent, Bulletin
from panem_sim.state import TickContext, WorldState


def run(state: WorldState, ctx: TickContext) -> list[AnyWorldEvent]:
    events: list[AnyWorldEvent] = []
    active_districts = {shipment.district_id for shipment in state.shipments.values()}

    for district in ctx.content.districts.values():
        if district.id in active_districts:
            continue
        station = next(
            (loc for loc in district.locations if loc.kind == LocationKind.STATION), None
        )
        if station is None:
            continue
        if ctx.rng.random() >= constants.SHIPMENT_SPAWN_CHANCE_PER_TICK:
            continue
        good_id = ctx.rng.choice(constants.SHIPMENT_LOOT_GOOD_IDS)
        qty = ctx.rng.randint(*constants.SHIPMENT_LOOT_QTY_RANGE)
        state.new_shipments.append(
            Shipment(
                district_id=district.id,
                location_id=station.id,
                good_id=good_id,
                qty=qty,
                spawned_tick=ctx.tick,
                expires_tick=ctx.tick + constants.SHIPMENT_WINDOW_TICKS,
            )
        )
        events.append(
            Bulletin(
                tick=ctx.tick,
                district_id=district.id,
                text=(
                    f"A supply train has stopped at {station.name} -- "
                    "guards look thin tonight."
                ),
            )
        )

    for shipment in state.shipments.values():
        if shipment.expires_tick <= ctx.tick:
            state.deleted_shipment_ids.append(shipment.id)

    return events
