from __future__ import annotations

import datetime as dt

import pytest

from panem_shared import afflictions
from panem_shared.db.models import AfflictionType, Character, CharacterAffliction
from panem_shared.enums import AfflictionSource, CharacterStatus, RpMode
from panem_shared.errors import NotAllowed, ValidationFailed

NOW = dt.datetime(2026, 1, 10, tzinfo=dt.UTC)


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
        rp_mode=RpMode.LIFE.value,
        health=100.0,
        hunger=0.0,
        thirst=0.0,
        fatigue=100.0,
        sanity=100.0,
    )
    defaults.update(overrides)
    character = Character(**defaults)  # type: ignore[arg-type]
    character.id = 1
    return character


async def make_affliction_type(db_session, **overrides: object) -> AfflictionType:
    defaults: dict[str, object] = dict(name="Broken Leg", description="Ouch", is_permanent=False)
    defaults.update(overrides)
    row = AfflictionType(**defaults)  # type: ignore[arg-type]
    db_session.add(row)
    await db_session.flush()
    return row


def make_active_row(affliction_type: AfflictionType, **overrides: object) -> CharacterAffliction:
    """A DB-free `CharacterAffliction`/`AfflictionType` pair for the
    `*_sync` helpers `panem_sim.systems.needs` calls directly -- no
    `db_session` needed since these never touch a session."""
    defaults: dict[str, object] = dict(
        character_id=1, affliction_type_id=1, source=AfflictionSource.MANUAL.value
    )
    defaults.update(overrides)
    row = CharacterAffliction(**defaults)  # type: ignore[arg-type]
    row.affliction_type = affliction_type
    return row


class TestApplyManualAffliction:
    async def test_refuses_a_non_life_mode_character(self, db_session):
        character = make_character(rp_mode=RpMode.SIMULATION.value)
        affliction_type = await make_affliction_type(db_session)
        with pytest.raises(NotAllowed) as exc_info:
            await afflictions.apply_manual_affliction(
                db_session, character=character, affliction_type=affliction_type, cause="Fell"
            )
        assert exc_info.value.reason_key == "affliction_wrong_mode"

    async def test_refuses_a_blank_cause(self, db_session):
        character = make_character()
        affliction_type = await make_affliction_type(db_session)
        with pytest.raises(ValidationFailed) as exc_info:
            await afflictions.apply_manual_affliction(
                db_session, character=character, affliction_type=affliction_type, cause="   "
            )
        assert exc_info.value.reason_key == "affliction_cause_required"

    async def test_inserts_a_manual_active_row(self, db_session):
        character = make_character()
        affliction_type = await make_affliction_type(db_session)
        row = await afflictions.apply_manual_affliction(
            db_session,
            character=character,
            affliction_type=affliction_type,
            cause="Fell off a roof",
        )
        assert row.source == AfflictionSource.MANUAL.value
        assert row.cause == "Fell off a roof"
        assert row.cured_at is None


class TestMarkDead:
    def test_refuses_a_non_life_mode_character(self):
        character = make_character(rp_mode=RpMode.STORY.value)
        with pytest.raises(NotAllowed) as exc_info:
            afflictions.mark_dead(character, "Fell")
        assert exc_info.value.reason_key == "death_wrong_mode"

    def test_refuses_an_already_dead_character(self):
        character = make_character(status=CharacterStatus.DEAD.value)
        with pytest.raises(NotAllowed) as exc_info:
            afflictions.mark_dead(character, "Fell")
        assert exc_info.value.reason_key == "character_already_dead"

    def test_sets_status_and_cause(self):
        character = make_character()
        afflictions.mark_dead(character, "Fell off a cliff")
        assert character.status == CharacterStatus.DEAD.value
        assert character.death_cause == "Fell off a cliff"

    def test_blank_cause_becomes_none(self):
        character = make_character()
        afflictions.mark_dead(character, "   ")
        assert character.death_cause is None


class TestCheckAndCure:
    async def test_cures_once_the_stat_crosses_the_threshold(self, db_session):
        character = make_character(sanity=80.0)
        affliction_type = await make_affliction_type(
            db_session, cure_stat="sanity", cure_threshold=60.0
        )
        row = CharacterAffliction(
            character_id=character.id,
            affliction_type_id=affliction_type.id,
            cause="Nightmares",
            source=AfflictionSource.MANUAL.value,
        )
        db_session.add(row)
        await db_session.flush()

        cured = await afflictions.check_and_cure(db_session, character)

        assert len(cured) == 1
        assert cured[0].id == row.id
        assert row.cured_at is not None

    async def test_leaves_it_active_below_threshold(self, db_session):
        character = make_character(sanity=10.0)
        affliction_type = await make_affliction_type(
            db_session, cure_stat="sanity", cure_threshold=60.0
        )
        row = CharacterAffliction(
            character_id=character.id,
            affliction_type_id=affliction_type.id,
            source=AfflictionSource.MANUAL.value,
        )
        db_session.add(row)
        await db_session.flush()

        cured = await afflictions.check_and_cure(db_session, character)

        assert cured == []
        assert row.cured_at is None

    async def test_never_cures_a_permanent_affliction(self, db_session):
        character = make_character(sanity=100.0)
        affliction_type = await make_affliction_type(db_session, is_permanent=True)
        row = CharacterAffliction(
            character_id=character.id,
            affliction_type_id=affliction_type.id,
            source=AfflictionSource.MANUAL.value,
        )
        db_session.add(row)
        await db_session.flush()

        cured = await afflictions.check_and_cure(db_session, character)

        assert cured == []


class TestApplyAutoAfflictions:
    async def test_skips_non_simulation_characters(self, db_session):
        character = make_character(rp_mode=RpMode.LIFE.value, hunger=100.0)
        affliction_type = await make_affliction_type(
            db_session, auto_apply_stat="hunger", auto_apply_threshold=1000.0
        )
        applied = await afflictions.apply_auto_afflictions(
            db_session, character=character, catalog=[affliction_type]
        )
        assert applied == []

    async def test_applies_once_the_stat_falls_beneath_the_threshold(self, db_session):
        character = make_character(rp_mode=RpMode.SIMULATION.value, thirst=90.0)
        affliction_type = await make_affliction_type(
            db_session,
            name="Dehydrated",
            auto_apply_stat="thirst",
            auto_apply_threshold=1000.0,  # thirst (90) always "falls beneath" this
        )
        applied = await afflictions.apply_auto_afflictions(
            db_session, character=character, catalog=[affliction_type]
        )
        assert len(applied) == 1
        assert applied[0].source == AfflictionSource.AUTO.value
        assert applied[0].cause is None

    async def test_does_not_duplicate_an_already_active_instance(self, db_session):
        character = make_character(rp_mode=RpMode.SIMULATION.value, thirst=90.0)
        affliction_type = await make_affliction_type(
            db_session, auto_apply_stat="thirst", auto_apply_threshold=1000.0
        )
        first = await afflictions.apply_auto_afflictions(
            db_session, character=character, catalog=[affliction_type]
        )
        assert len(first) == 1
        second = await afflictions.apply_auto_afflictions(
            db_session, character=character, catalog=[affliction_type]
        )
        assert second == []


class TestApplyAutoDeath:
    def test_skips_non_simulation_characters(self):
        character = make_character(rp_mode=RpMode.LIFE.value, health=0.0)
        assert afflictions.apply_auto_death(character) is False
        assert character.status != CharacterStatus.DEAD.value

    def test_does_nothing_above_zero_health(self):
        character = make_character(rp_mode=RpMode.SIMULATION.value, health=1.0)
        assert afflictions.apply_auto_death(character) is False

    def test_marks_dead_at_zero_health(self):
        character = make_character(rp_mode=RpMode.SIMULATION.value, health=0.0)
        assert afflictions.apply_auto_death(character) is True
        assert character.status == CharacterStatus.DEAD.value
        assert character.death_cause == afflictions.AUTO_DEATH_MESSAGE

    def test_is_a_no_op_on_an_already_dead_character(self):
        character = make_character(
            rp_mode=RpMode.SIMULATION.value, health=0.0, status=CharacterStatus.DEAD.value
        )
        assert afflictions.apply_auto_death(character) is False


class TestCheckAndCureSync:
    """The DB-free core `panem_sim.systems.needs` calls directly against
    `WorldState`'s preloaded data -- same behavior as `check_and_cure`,
    just fed an already-loaded `active` list instead of querying for one."""

    def test_cures_once_the_stat_crosses_the_threshold(self):
        character = make_character(sanity=80.0)
        affliction_type = AfflictionType(
            name="Nightmares",
            description="",
            is_permanent=False,
            cure_stat="sanity",
            cure_threshold=60.0,
        )
        row = make_active_row(affliction_type)

        cured = afflictions.check_and_cure_sync(character, [row], NOW)

        assert cured == [row]
        assert row.cured_at == NOW

    def test_leaves_it_active_below_threshold(self):
        character = make_character(sanity=10.0)
        affliction_type = AfflictionType(
            name="Nightmares",
            description="",
            is_permanent=False,
            cure_stat="sanity",
            cure_threshold=60.0,
        )
        row = make_active_row(affliction_type)

        cured = afflictions.check_and_cure_sync(character, [row], NOW)

        assert cured == []
        assert row.cured_at is None

    def test_never_cures_a_permanent_affliction(self):
        character = make_character(sanity=100.0)
        affliction_type = AfflictionType(name="Scar", description="", is_permanent=True)
        row = make_active_row(affliction_type)

        cured = afflictions.check_and_cure_sync(character, [row], NOW)

        assert cured == []


class TestApplyAutoAfflictionsSync:
    def test_skips_non_simulation_characters(self):
        character = make_character(rp_mode=RpMode.LIFE.value, hunger=100.0)
        affliction_type = AfflictionType(
            name="Starving",
            description="",
            is_permanent=False,
            auto_apply_stat="hunger",
            auto_apply_threshold=1000.0,
        )

        applied = afflictions.apply_auto_afflictions_sync(character, [affliction_type], [])

        assert applied == []

    def test_applies_once_the_stat_falls_beneath_the_threshold(self):
        character = make_character(rp_mode=RpMode.SIMULATION.value, thirst=90.0)
        affliction_type = AfflictionType(
            name="Dehydrated",
            description="",
            is_permanent=False,
            auto_apply_stat="thirst",
            auto_apply_threshold=1000.0,
        )

        applied = afflictions.apply_auto_afflictions_sync(character, [affliction_type], [])

        assert len(applied) == 1
        assert applied[0].source == AfflictionSource.AUTO.value
        assert applied[0].cause is None

    def test_does_not_duplicate_an_already_active_instance(self):
        character = make_character(rp_mode=RpMode.SIMULATION.value, thirst=90.0)
        affliction_type = AfflictionType(
            name="Dehydrated",
            description="",
            is_permanent=False,
            auto_apply_stat="thirst",
            auto_apply_threshold=1000.0,
        )
        affliction_type.id = 7
        active_row = make_active_row(affliction_type, affliction_type_id=7)

        applied = afflictions.apply_auto_afflictions_sync(
            character, [affliction_type], [active_row]
        )

        assert applied == []
