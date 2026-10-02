from __future__ import annotations

import pytest

from panem_bot.errors import NotAllowed
from panem_bot.services import sustenance as sustenance_svc
from panem_shared import constants
from panem_shared.content.schemas import Good
from panem_shared.db.models import Character, Inventory
from panem_shared.enums import CharacterStatus, OwnerKind, RpMode

TICK = constants.TICKS_PER_DAY * 3 + 5

FISH = Good(
    id="fish",
    name="Seafood",
    base_price=5.0,
    category="food",
    hunger_value=20.0,
    cook_method="stove",
)
GRAIN = Good(
    id="grain", name="Grain", base_price=2.0, category="food", hunger_value=15.0, cook_method="oven"
)
PRODUCE = Good(
    id="produce", name="Fruits/Drinks", base_price=3.0, category="food", thirst_value=25.0
)
COAL = Good(id="coal", name="Coal", base_price=4.0, category="fuel")
MEDICINE = Good(
    id="medicine", name="Medicine", base_price=15.0, category="medical", heal_value=30.0
)


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        rp_mode=RpMode.SIMULATION.value,
        money=1_000,
        hunger=50.0,
        thirst=50.0,
        sanity=50.0,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


async def give_inventory(session, character: Character, good_id: str, qty: int) -> None:
    session.add(
        Inventory(
            owner_kind=OwnerKind.CHARACTER.value,
            owner_id=str(character.id),
            good_id=good_id,
            qty=qty,
        )
    )
    await session.flush()


class TestCheckCanEat:
    def test_refuses_a_dead_character(self):
        character = make_character(status=CharacterStatus.DEAD.value)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_eat(character, FISH, 1)
        assert exc_info.value.reason_key == "character_dead"

    def test_refuses_a_non_simulation_character(self):
        character = make_character(rp_mode=RpMode.LIFE.value)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_eat(character, FISH, 1)
        assert exc_info.value.reason_key == "sustenance_mode_forbidden"

    def test_refuses_a_non_edible_good(self):
        character = make_character()
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_eat(character, COAL, 5)
        assert exc_info.value.reason_key == "good_not_edible"

    def test_refuses_with_no_inventory(self):
        character = make_character()
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_eat(character, FISH, 0)
        assert exc_info.value.reason_key == "sustenance_no_inventory"

    def test_allows_with_inventory_on_hand(self):
        character = make_character()
        sustenance_svc.check_can_eat(character, FISH, 1)


class TestEat:
    async def test_consumes_one_unit_relieves_hunger_and_stamps_the_tick(self, db_session):
        character = make_character(hunger=80.0)
        await give_inventory(db_session, character, "fish", 3)
        hunger = await sustenance_svc.eat(db_session, character, FISH, TICK)
        assert hunger == 80.0 - FISH.hunger_value
        assert character.hunger == hunger
        assert character.last_ate_tick == TICK
        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "fish"))
        assert inv.qty == 2

    async def test_bonus_doubles_the_relief_for_a_cookable_good(self, db_session):
        character = make_character(hunger=80.0)
        await give_inventory(db_session, character, "fish", 1)
        hunger = await sustenance_svc.eat(db_session, character, FISH, TICK, bonus=True)
        assert hunger == 80.0 - FISH.hunger_value * constants.COOK_BONUS_MULTIPLIER

    async def test_bonus_is_ignored_for_a_good_with_no_cook_method(self, db_session):
        character = make_character(hunger=80.0)
        plain_food = Good(
            id="plain", name="Plain Food", base_price=1.0, category="food", hunger_value=10.0
        )
        await give_inventory(db_session, character, "plain", 1)
        hunger = await sustenance_svc.eat(db_session, character, plain_food, TICK, bonus=True)
        assert hunger == 80.0 - plain_food.hunger_value

    async def test_clamps_hunger_at_the_minimum(self, db_session):
        character = make_character(hunger=10.0)
        await give_inventory(db_session, character, "fish", 1)
        hunger = await sustenance_svc.eat(db_session, character, FISH, TICK)
        assert hunger == constants.HUNGER_MIN

    async def test_raises_and_leaves_the_character_untouched_with_no_inventory(self, db_session):
        character = make_character(hunger=80.0)
        with pytest.raises(NotAllowed):
            await sustenance_svc.eat(db_session, character, FISH, TICK)
        assert character.hunger == 80.0

    async def test_eating_twice_in_a_row_is_allowed(self, db_session):
        character = make_character(hunger=80.0)
        await give_inventory(db_session, character, "fish", 2)
        await sustenance_svc.eat(db_session, character, FISH, TICK)
        hunger = await sustenance_svc.eat(db_session, character, FISH, TICK)
        assert hunger == 80.0 - FISH.hunger_value * 2


class TestCheckCanDrink:
    def test_refuses_a_non_simulation_character(self):
        character = make_character(rp_mode=RpMode.STORY.value)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_drink(character, PRODUCE, 1)
        assert exc_info.value.reason_key == "sustenance_mode_forbidden"

    def test_refuses_a_non_drinkable_good(self):
        character = make_character()
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_drink(character, FISH, 5)
        assert exc_info.value.reason_key == "good_not_drinkable"

    def test_refuses_with_no_inventory(self):
        character = make_character()
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_drink(character, PRODUCE, 0)
        assert exc_info.value.reason_key == "sustenance_no_inventory"


class TestDrink:
    async def test_consumes_one_unit_relieves_thirst_and_stamps_the_tick(self, db_session):
        character = make_character(thirst=80.0)
        await give_inventory(db_session, character, "produce", 3)
        thirst = await sustenance_svc.drink(db_session, character, PRODUCE, TICK)
        assert thirst == 80.0 - PRODUCE.thirst_value
        assert character.thirst == thirst
        assert character.last_drank_tick == TICK
        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "produce"))
        assert inv.qty == 2

    async def test_clamps_thirst_at_the_minimum(self, db_session):
        character = make_character(thirst=10.0)
        await give_inventory(db_session, character, "produce", 1)
        thirst = await sustenance_svc.drink(db_session, character, PRODUCE, TICK)
        assert thirst == constants.THIRST_MIN


class TestOwnedConsumables:
    async def test_filters_to_edible_drinkable_and_healing_goods(self, db_session):
        character = make_character()
        await give_inventory(db_session, character, "fish", 2)
        await give_inventory(db_session, character, "coal", 5)
        await give_inventory(db_session, character, "produce", 1)
        await give_inventory(db_session, character, "medicine", 1)
        goods = {"fish": FISH, "coal": COAL, "produce": PRODUCE, "medicine": MEDICINE}
        pairs = await sustenance_svc.owned_consumables(db_session, character, goods)
        good_ids = {good.id for _, good in pairs}
        assert good_ids == {"fish", "produce", "medicine"}

    async def test_excludes_zero_quantity_rows(self, db_session):
        character = make_character()
        await give_inventory(db_session, character, "fish", 0)
        pairs = await sustenance_svc.owned_consumables(db_session, character, {"fish": FISH})
        assert pairs == []


class TestCheckCanHeal:
    def test_refuses_a_dead_character(self):
        character = make_character(status=CharacterStatus.DEAD.value)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_heal(character, MEDICINE, 1)
        assert exc_info.value.reason_key == "character_dead"

    def test_refuses_a_non_simulation_character(self):
        character = make_character(rp_mode=RpMode.LIFE.value)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_heal(character, MEDICINE, 1)
        assert exc_info.value.reason_key == "sustenance_mode_forbidden"

    def test_refuses_a_non_healing_good(self):
        character = make_character()
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_heal(character, COAL, 5)
        assert exc_info.value.reason_key == "good_not_healing"

    def test_refuses_with_no_inventory(self):
        character = make_character()
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_heal(character, MEDICINE, 0)
        assert exc_info.value.reason_key == "sustenance_no_inventory"

    def test_allows_with_inventory_on_hand(self):
        character = make_character()
        sustenance_svc.check_can_heal(character, MEDICINE, 1)


class TestHeal:
    async def test_consumes_one_unit_and_restores_health(self, db_session):
        character = make_character(health=50.0)
        await give_inventory(db_session, character, "medicine", 2)
        health = await sustenance_svc.heal(db_session, character, MEDICINE, TICK)
        assert health == 50.0 + MEDICINE.heal_value
        assert character.health == health
        inv = await db_session.get(Inventory, (OwnerKind.CHARACTER.value, "1", "medicine"))
        assert inv.qty == 1

    async def test_clamps_health_at_max(self, db_session):
        character = make_character(health=90.0)
        await give_inventory(db_session, character, "medicine", 1)
        health = await sustenance_svc.heal(db_session, character, MEDICINE, TICK)
        assert health == constants.HEALTH_MAX
        assert character.health == constants.HEALTH_MAX


class TestCheckCanEntertain:
    def test_refuses_a_non_simulation_character(self):
        character = make_character(rp_mode=RpMode.LIFE.value)
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.check_can_entertain(character)
        assert exc_info.value.reason_key == "sustenance_mode_forbidden"

    def test_allows_a_simulation_character(self):
        character = make_character()
        sustenance_svc.check_can_entertain(character)


class TestEntertain:
    def test_relieves_sanity_by_the_games_own_value_and_stamps_the_tick(self):
        character = make_character(sanity=20.0)
        sanity = sustenance_svc.entertain(character, "snake", TICK)
        assert sanity == 20.0 + constants.ENTERTAINMENT_SANITY_VALUES["snake"]
        assert character.sanity == sanity
        assert character.last_entertained_tick == TICK

    def test_clamps_sanity_at_the_maximum(self):
        character = make_character(sanity=95.0)
        sanity = sustenance_svc.entertain(character, "connect4", TICK)
        assert sanity == constants.SANITY_MAX

    def test_rejects_an_unknown_game(self):
        character = make_character()
        with pytest.raises(NotAllowed) as exc_info:
            sustenance_svc.entertain(character, "chess", TICK)
        assert exc_info.value.reason_key == "unknown_game"

    def test_playing_repeatedly_is_allowed(self):
        character = make_character(sanity=0.0)
        sustenance_svc.entertain(character, "poison", TICK)
        sanity = sustenance_svc.entertain(character, "poison", TICK)
        assert sanity == constants.ENTERTAINMENT_SANITY_VALUES["poison"] * 2
