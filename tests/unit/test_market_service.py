from __future__ import annotations

import pytest

from panem_bot.errors import NotAllowed, NotFound
from panem_bot.services import market as market_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.content.schemas import (
    District,
    DistrictCulture,
    DistrictMap,
    Good,
    Location,
)
from panem_shared.db.models import Character, DistrictState, Inventory, MarketOrder, MarketPrice
from panem_shared.enums import CharacterStatus, OwnerKind


class FixedRng:
    """A stand-in for `random.Random` that always returns a fixed draw,
    so illicit-detection tests don't depend on the real threshold."""

    def __init__(self, value: float) -> None:
        self._value = value

    def random(self) -> float:
        return self._value


def make_district(*, illicit_market: bool = False) -> District:
    locations = [
        Location(id="square", name="The Square", kind="public"),
        Location(id="station", name="Rail Station", kind="station"),
        Location(id="market", name="Market", kind="market", illicit=illicit_market),
    ]
    coords = {loc.id: (0, 0) for loc in locations}
    return District(
        id=1,
        name="District 1",
        industry="x",
        produces=["coal"],
        imports=["grain"],
        population_base=1000,
        culture=DistrictCulture(),
        locations=locations,
        map=DistrictMap(image="x.png", width=100, height=100, location_coords=coords),
    )


def make_goods() -> dict[str, Good]:
    return {
        "coal": Good(id="coal", name="Coal", base_price=4.0, category="fuel"),
        "grain": Good(id="grain", name="Grain", base_price=2.0, category="food"),
    }


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
        location_id="market",
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


class TestResolveMarketLocation:
    def test_at_the_market_location_succeeds(self):
        district = make_district()
        character = make_character(location_id="market")
        location = market_svc.resolve_market_location(character, district)
        assert location.id == "market"

    def test_elsewhere_in_the_district_refuses(self):
        district = make_district()
        character = make_character(location_id="square")
        with pytest.raises(NotAllowed) as exc_info:
            market_svc.resolve_market_location(character, district)
        assert exc_info.value.reason_key == "market_not_at_market"
        # Regression: the raised fmt kwargs must actually satisfy the
        # string template, or t() raises KeyError instead of showing the
        # player a real message (this is exactly how it broke before).
        assert "Test" in t(exc_info.value.reason_key, **exc_info.value.fmt)


class TestCheckCanTrade:
    def test_approved_character_allowed(self):
        market_svc.check_can_trade(make_character())  # no raise

    def test_non_approved_character_refused(self):
        character = make_character(status=CharacterStatus.PENDING.value)
        with pytest.raises(NotAllowed) as exc_info:
            market_svc.check_can_trade(character)
        assert exc_info.value.reason_key == "character_not_approved"


class TestResolveGood:
    def test_traded_good_resolves(self):
        district = make_district()
        good = market_svc.resolve_good(district, make_goods(), "coal")
        assert good.id == "coal"

    def test_untraded_good_raises_not_found(self):
        district = make_district()
        with pytest.raises(NotFound) as exc_info:
            market_svc.resolve_good(district, make_goods(), "weapons")
        assert exc_info.value.reason_key == "market_good_not_traded"


class TestGetPrice:
    async def test_falls_back_to_base_price_with_no_row(self, db_session):
        good = Good(id="coal", name="Coal", base_price=4.0, category="fuel")
        price = await market_svc.get_price(db_session, 1, good)
        assert price == 4.0

    async def test_uses_existing_price_row(self, db_session):
        db_session.add(MarketPrice(district_id=1, good_id="coal", price=7.5, tick=0))
        await db_session.flush()
        good = Good(id="coal", name="Coal", base_price=4.0, category="fuel")
        price = await market_svc.get_price(db_session, 1, good)
        assert price == 7.5


class TestBuy:
    async def test_happy_path_deducts_money_and_adds_inventory(self, db_session):
        district = make_district()
        character = make_character(money=100)
        result = await market_svc.buy(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            good_id="coal",
            qty=5,
            tick=10,
            rng=FixedRng(0.99),
        )
        assert result.total == 20  # 5 * base_price 4.0
        assert character.money == 80
        assert result.caught is False

        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "coal"))
        assert inv is not None
        assert inv.qty == 5

        orders = (await db_session.execute(MarketOrder.__table__.select())).all()
        assert len(orders) == 1

    async def test_insufficient_funds_raises_and_changes_nothing(self, db_session):
        district = make_district()
        character = make_character(money=1)
        with pytest.raises(NotAllowed) as exc_info:
            await market_svc.buy(
                db_session,
                character=character,
                district=district,
                goods=make_goods(),
                good_id="coal",
                qty=5,
                tick=10,
                rng=FixedRng(0.99),
            )
        assert exc_info.value.reason_key == "market_insufficient_funds"
        assert character.money == 1
        assert "Test" in t(exc_info.value.reason_key, **exc_info.value.fmt)

    async def test_illicit_market_detection_applies_consequence(self, db_session):
        district = make_district(illicit_market=True)
        character = make_character(money=100, jailed_until_tick=None)
        db_session.add(DistrictState(district_id=district.id, peacekeeper_pressure=0.3))
        await db_session.flush()

        result = await market_svc.buy(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            good_id="coal",
            qty=1,
            tick=10,
            rng=FixedRng(0.0),  # always below the detection threshold
        )
        assert result.caught is True
        assert character.money == max(0, 100 - 4 - constants.MARKET_ILLICIT_FINE)
        assert character.jailed_until_tick == constants.MARKET_ILLICIT_JAIL_TICKS
        assert character.reputation == -constants.REP_ILLICIT_CAUGHT_PENALTY

        district_row = await db_session.get(DistrictState, district.id)
        assert district_row.peacekeeper_pressure == 0.3 + market_svc.ILLICIT_PRESSURE_DELTA

    async def test_crackdown_makes_detection_more_likely(self, db_session):
        district = make_district(illicit_market=True)
        character = make_character(money=100, jailed_until_tick=None)
        db_session.add(
            DistrictState(
                district_id=district.id, peacekeeper_pressure=0.3, crackdown_until_tick=100
            )
        )
        await db_session.flush()
        # A roll that clears the base detection prob but not the crackdown-scaled one.
        roll = (
            constants.MARKET_ILLICIT_DETECTION_PROB
            + constants.MARKET_ILLICIT_DETECTION_PROB * constants.CRACKDOWN_DETECTION_MULTIPLIER
        ) / 2

        result = await market_svc.buy(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            good_id="coal",
            qty=1,
            tick=10,
            rng=FixedRng(roll),
        )
        assert result.caught is True

    async def test_illicit_catch_without_a_district_state_row_does_not_raise(self, db_session):
        district = make_district(illicit_market=True)
        character = make_character(money=100, jailed_until_tick=None)

        result = await market_svc.buy(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            good_id="coal",
            qty=1,
            tick=10,
            rng=FixedRng(0.0),
        )
        assert result.caught is True  # no DistrictState row seeded -- consequence still applies

    async def test_legal_market_never_triggers_detection(self, db_session):
        district = make_district(illicit_market=False)
        character = make_character(money=100)
        result = await market_svc.buy(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            good_id="coal",
            qty=1,
            tick=10,
            rng=FixedRng(0.0),
        )
        assert result.caught is False
        assert character.jailed_until_tick is None

    async def test_no_price_row_yet_means_no_stock_ceiling(self, db_session):
        """A good `panem_sim.systems.economy` hasn't priced today yet (a
        fresh world) has nothing allocated to check against -- buying
        shouldn't be refused just because nobody's run the daily
        redistribution yet."""
        district = make_district()
        character = make_character(money=100)
        result = await market_svc.buy(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            good_id="coal",
            qty=5,
            tick=10,
            rng=FixedRng(0.99),
        )
        assert result.qty == 5

    async def test_buying_more_than_todays_stock_is_refused(self, db_session):
        district = make_district()
        character = make_character(money=100)
        db_session.add(MarketPrice(district_id=1, good_id="coal", price=4.0, supply=3.0, tick=0))
        await db_session.flush()

        with pytest.raises(NotAllowed) as exc_info:
            await market_svc.buy(
                db_session,
                character=character,
                district=district,
                goods=make_goods(),
                good_id="coal",
                qty=5,
                tick=10,
                rng=FixedRng(0.99),
            )
        assert exc_info.value.reason_key == "market_insufficient_stock"
        assert character.money == 100
        assert "Coal" in t(exc_info.value.reason_key, **exc_info.value.fmt)

    async def test_buying_within_todays_stock_decrements_it(self, db_session):
        district = make_district()
        character = make_character(money=100)
        db_session.add(MarketPrice(district_id=1, good_id="coal", price=4.0, supply=10.0, tick=0))
        await db_session.flush()

        await market_svc.buy(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            good_id="coal",
            qty=4,
            tick=10,
            rng=FixedRng(0.99),
        )

        row = await db_session.get(MarketPrice, (1, "coal"))
        assert row.supply == 6.0


class TestSell:
    async def test_happy_path_adds_money_and_removes_inventory(self, db_session):
        db_session.add(
            Inventory(owner_kind=OwnerKind.CHARACTER.value, owner_id="1", good_id="coal", qty=10)
        )
        await db_session.flush()
        district = make_district()
        character = make_character(money=0)

        result = await market_svc.sell(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            good_id="coal",
            qty=4,
            tick=10,
            rng=FixedRng(0.99),
        )

        expected_total = round(4 * 4.0 * constants.SELL_DISCOUNT)
        assert result.total == expected_total
        assert character.money == expected_total
        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "coal"))
        assert inv.qty == 6

    async def test_selling_more_than_owned_raises_and_changes_nothing(self, db_session):
        db_session.add(
            Inventory(owner_kind=OwnerKind.CHARACTER.value, owner_id="1", good_id="coal", qty=2)
        )
        await db_session.flush()
        district = make_district()
        character = make_character(money=50)

        with pytest.raises(NotAllowed) as exc_info:
            await market_svc.sell(
                db_session,
                character=character,
                district=district,
                goods=make_goods(),
                good_id="coal",
                qty=5,
                tick=10,
                rng=FixedRng(0.99),
            )
        assert exc_info.value.reason_key == "market_insufficient_inventory"
        assert character.money == 50
        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "coal"))
        assert inv.qty == 2
        assert "Test" in t(exc_info.value.reason_key, **exc_info.value.fmt)

    async def test_selling_returns_units_to_todays_local_stock(self, db_session):
        db_session.add(
            Inventory(owner_kind=OwnerKind.CHARACTER.value, owner_id="1", good_id="coal", qty=10)
        )
        db_session.add(MarketPrice(district_id=1, good_id="coal", price=4.0, supply=3.0, tick=0))
        await db_session.flush()
        district = make_district()
        character = make_character(money=0)

        await market_svc.sell(
            db_session,
            character=character,
            district=district,
            goods=make_goods(),
            good_id="coal",
            qty=4,
            tick=10,
            rng=FixedRng(0.99),
        )

        row = await db_session.get(MarketPrice, (1, "coal"))
        assert row.supply == 7.0


class TestListInventory:
    async def test_returns_only_positive_quantities_for_that_character(self, db_session):
        db_session.add_all(
            [
                Inventory(
                    owner_kind=OwnerKind.CHARACTER.value, owner_id="1", good_id="coal", qty=3
                ),
                Inventory(
                    owner_kind=OwnerKind.CHARACTER.value, owner_id="1", good_id="grain", qty=0
                ),
                Inventory(
                    owner_kind=OwnerKind.CHARACTER.value, owner_id="2", good_id="coal", qty=9
                ),
            ]
        )
        await db_session.flush()

        rows = await market_svc.list_inventory(db_session, 1)

        assert {row.good_id for row in rows} == {"coal"}
