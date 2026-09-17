from __future__ import annotations

import pytest

from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import blackmarket as blackmarket_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    Good,
    Location,
    NpcContent,
)
from panem_shared.db.models import (
    Character,
    DistrictState,
    Inventory,
    MarketOrder,
    MarketPrice,
    RelationshipRow,
)
from panem_shared.enums import CharacterStatus, OwnerKind, Stance


class FixedRng:
    def __init__(self, value: float) -> None:
        self._value = value

    def random(self) -> float:
        return self._value


def make_district() -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
        Location(id="black_market", name="The Underground Exchange", kind="market", illicit=True),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=1,
        name="District 1",
        industry="x",
        produces=["coal"],
        imports=["grain"],
        illicit_produces=["contraband_weapons"],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )


def make_goods() -> dict[str, Good]:
    return {
        "contraband_weapons": Good(
            id="contraband_weapons",
            name="Contraband Weapons",
            base_price=35.0,
            category="contraband",
        ),
    }


def make_fence(**overrides: object) -> NpcContent:
    defaults: dict[str, object] = dict(
        id="d1_fence",
        district=1,
        name="Fence",
        age=40,
        home_location_id="square",
        backstory="A fence.",
        black_market_contact=True,
    )
    defaults.update(overrides)
    return NpcContent(**defaults)  # type: ignore[arg-type]


def make_npcs(*fences: NpcContent) -> dict[str, NpcContent]:
    return {npc.id: npc for npc in fences}


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        money=100,
        reputation=0.0,
        location_id="black_market",
        jail_count=0,
        jailed_until_tick=None,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


async def add_trust(session, character_id: int, npc_id: str, stance: str) -> None:
    session.add(
        RelationshipRow(
            subject_kind=OwnerKind.CHARACTER.value,
            subject_id=str(character_id),
            object_kind=OwnerKind.NPC.value,
            object_id=npc_id,
            stance=stance,
        )
    )
    await session.flush()


class TestResolveFence:
    def test_finds_the_flagged_npc(self):
        fence = make_fence()
        result = blackmarket_svc.resolve_fence(1, make_npcs(fence))
        assert result.id == "d1_fence"

    def test_no_fence_raises_not_found(self):
        with pytest.raises(NotFound) as exc_info:
            blackmarket_svc.resolve_fence(1, make_npcs())
        assert exc_info.value.reason_key == "blackmarket_no_fence"


class TestResolveBlackMarketLocation:
    def test_at_the_illicit_location_succeeds(self):
        district = make_district()
        character = make_character(location_id="black_market")
        blackmarket_svc.resolve_black_market_location(character, district)  # no raise

    def test_elsewhere_refuses(self):
        district = make_district()
        character = make_character(location_id="square")
        with pytest.raises(NotAllowed) as exc_info:
            blackmarket_svc.resolve_black_market_location(character, district)
        assert exc_info.value.reason_key == "blackmarket_not_at_market"


class TestCheckCanTrade:
    async def test_trusted_stance_allowed(self, db_session):
        character = make_character()
        fence = make_fence()
        await add_trust(db_session, character.id, fence.id, Stance.LIKES.value)
        await blackmarket_svc.check_can_trade(db_session, character, fence)  # no raise

    async def test_stranger_refused(self, db_session):
        character = make_character()
        fence = make_fence()
        with pytest.raises(NotAllowed) as exc_info:
            await blackmarket_svc.check_can_trade(db_session, character, fence)
        assert exc_info.value.reason_key == "blackmarket_not_trusted"

    async def test_neutral_stance_still_refused(self, db_session):
        character = make_character()
        fence = make_fence()
        await add_trust(db_session, character.id, fence.id, Stance.NEUTRAL.value)
        with pytest.raises(NotAllowed):
            await blackmarket_svc.check_can_trade(db_session, character, fence)

    async def test_non_approved_character_refused(self, db_session):
        character = make_character(status=CharacterStatus.PENDING.value)
        fence = make_fence()
        await add_trust(db_session, character.id, fence.id, Stance.LOVES.value)
        with pytest.raises(NotAllowed) as exc_info:
            await blackmarket_svc.check_can_trade(db_session, character, fence)
        assert exc_info.value.reason_key == "character_not_approved"


class TestBuy:
    async def test_happy_path(self, db_session):
        district = make_district()
        character = make_character(money=100)
        fence = make_fence()
        await add_trust(db_session, character.id, fence.id, Stance.LOVES.value)
        db_session.add(
            MarketPrice(
                district_id=1, good_id="contraband_weapons", price=35.0, supply=10.0, tick=0
            )
        )
        await db_session.flush()

        result = await blackmarket_svc.buy(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            npcs=make_npcs(fence),
            good_id="contraband_weapons",
            qty=2,
            tick=10,
            rng=FixedRng(0.99),
        )

        assert result.total == 70
        assert character.money == 30
        inv = await db_session.get(
            Inventory, (OwnerKind.CHARACTER.value, "1", "contraband_weapons")
        )
        assert inv.qty == 2
        row = await db_session.get(MarketPrice, (1, "contraband_weapons"))
        assert row.supply == 8.0
        orders = (await db_session.execute(MarketOrder.__table__.select())).all()
        assert len(orders) == 1

    async def test_not_trusted_refuses_before_any_mutation(self, db_session):
        district = make_district()
        character = make_character(money=100)
        fence = make_fence()
        db_session.add(
            MarketPrice(
                district_id=1, good_id="contraband_weapons", price=35.0, supply=10.0, tick=0
            )
        )
        await db_session.flush()

        with pytest.raises(NotAllowed) as exc_info:
            await blackmarket_svc.buy(
                db_session,
                character=character,
                district=district,
                goods=make_goods(),
                npcs=make_npcs(fence),
                good_id="contraband_weapons",
                qty=1,
                tick=10,
                rng=FixedRng(0.99),
            )
        assert exc_info.value.reason_key == "blackmarket_not_trusted"
        assert character.money == 100

    async def test_no_stock_at_all_refuses(self, db_session):
        district = make_district()
        character = make_character(money=100)
        fence = make_fence()
        await add_trust(db_session, character.id, fence.id, Stance.LOVES.value)

        with pytest.raises(NotAllowed) as exc_info:
            await blackmarket_svc.buy(
                db_session,
                character=character,
                district=district,
                goods=make_goods(),
                npcs=make_npcs(fence),
                good_id="contraband_weapons",
                qty=1,
                tick=10,
                rng=FixedRng(0.99),
            )
        assert exc_info.value.reason_key == "blackmarket_insufficient_stock"
        assert "Contraband" in t(exc_info.value.reason_key, **exc_info.value.fmt)

    async def test_illicit_detection_applies_consequence(self, db_session):
        district = make_district()
        character = make_character(money=100, jailed_until_tick=None)
        fence = make_fence()
        await add_trust(db_session, character.id, fence.id, Stance.LOVES.value)
        db_session.add(
            MarketPrice(
                district_id=1, good_id="contraband_weapons", price=35.0, supply=10.0, tick=0
            )
        )
        db_session.add(DistrictState(district_id=1, peacekeeper_pressure=0.3))
        await db_session.flush()

        result = await blackmarket_svc.buy(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            npcs=make_npcs(fence),
            good_id="contraband_weapons",
            qty=1,
            tick=10,
            rng=FixedRng(0.0),
        )

        assert result.caught is True
        assert character.jailed_until_tick == constants.MARKET_ILLICIT_JAIL_TICKS
        district_row = await db_session.get(DistrictState, 1)
        assert district_row.peacekeeper_pressure == 0.3 + blackmarket_svc.BLACKMARKET_PRESSURE_DELTA


class TestSell:
    async def test_happy_path_returns_stock_and_pays_out(self, db_session):
        district = make_district()
        character = make_character(money=0)
        fence = make_fence()
        await add_trust(db_session, character.id, fence.id, Stance.LIKES.value)
        db_session.add(
            Inventory(
                owner_kind=OwnerKind.CHARACTER.value,
                owner_id="1",
                good_id="contraband_weapons",
                qty=5,
            )
        )
        db_session.add(
            MarketPrice(district_id=1, good_id="contraband_weapons", price=35.0, supply=3.0, tick=0)
        )
        await db_session.flush()

        result = await blackmarket_svc.sell(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            npcs=make_npcs(fence),
            good_id="contraband_weapons",
            qty=2,
            tick=10,
            rng=FixedRng(0.99),
        )

        expected_total = round(2 * 35.0 * constants.SELL_DISCOUNT)
        assert result.total == expected_total
        assert character.money == expected_total
        row = await db_session.get(MarketPrice, (1, "contraband_weapons"))
        assert row.supply == 5.0
