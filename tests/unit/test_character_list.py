"""`_render_character_list` (`panem_bot.cogs.characters`) -- `/character
list` never deletes dead/retired characters, just sorts them to the
bottom and lets the caller filter them out (`CharacterListView`'s toggle).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from panem_bot.cogs.characters import _render_character_list
from panem_shared.db.models import Character
from panem_shared.enums import CharacterStatus


class FakeDistrict:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeBot:
    def __init__(self) -> None:
        self.content = MagicMock()
        self.content.district = MagicMock(return_value=FakeDistrict("District 1"))


def make_character(**overrides: object) -> Character:
    defaults: dict[str, object] = dict(
        user_id=1,
        district_id=1,
        current_district_id=1,
        name="Test",
        age=20,
        status=CharacterStatus.APPROVED.value,
    )
    defaults.update(overrides)
    return Character(**defaults)  # type: ignore[arg-type]


class TestRenderCharacterList:
    def test_sorts_dead_and_retired_to_the_bottom(self):
        alive = make_character(name="Alive", status=CharacterStatus.APPROVED.value)
        dead = make_character(name="Dead", status=CharacterStatus.DEAD.value)
        retired = make_character(name="Retired", status=CharacterStatus.RETIRED.value)
        pending = make_character(name="Pending", status=CharacterStatus.PENDING.value)

        text = _render_character_list(FakeBot(), [dead, alive, retired, pending], hide_dead=False)

        lines = text.splitlines()
        names_in_order = [line.split("**")[1] for line in lines]
        assert names_in_order == ["Alive", "Pending", "Dead", "Retired"]
        # dead/retired still show up, just last -- never deleted
        assert "Dead" in text
        assert "Retired" in text

    def test_hide_dead_filters_out_dead_and_retired(self):
        alive = make_character(name="Alive", status=CharacterStatus.APPROVED.value)
        dead = make_character(name="Dead", status=CharacterStatus.DEAD.value)
        retired = make_character(name="Retired", status=CharacterStatus.RETIRED.value)

        text = _render_character_list(FakeBot(), [dead, alive, retired], hide_dead=True)

        assert "Alive" in text
        assert "Dead" not in text
        assert "Retired" not in text

    def test_hide_dead_with_only_dead_characters_shows_empty_message(self):
        dead = make_character(name="Dead", status=CharacterStatus.DEAD.value)
        text = _render_character_list(FakeBot(), [dead], hide_dead=True)
        assert text == "No characters to show."
