from __future__ import annotations

from panem_shared.hostility import is_hostile_action


class TestIsHostileAction:
    def test_true_for_spitting(self):
        assert is_hostile_action("*spits on Mark*") is True

    def test_true_for_hitting(self):
        assert is_hostile_action("Wren hits him across the face.") is True

    def test_true_for_attacking(self):
        assert is_hostile_action("I attack the guard with my fists!") is True

    def test_true_for_annoying(self):
        assert is_hostile_action("She keeps annoying him about it.") is True

    def test_false_for_ordinary_conversation(self):
        assert is_hostile_action("Hello, how are you doing today?") is False

    def test_case_insensitive(self):
        assert is_hostile_action("SHE SPITS ON HIM") is True
