from __future__ import annotations

import json
from pathlib import Path

import pytest

from panem_shared import constants
from panem_shared.content.loader import load_content
from panem_shared.lemonade import omni

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
LEMONADE_DIR = REPO_ROOT / "lemonade"
PROMPT_PATH = LEMONADE_DIR / "system_prompt.md"
CATALOG_PATH = LEMONADE_DIR / "components.json"


@pytest.fixture(scope="module")
def bundle():
    return load_content(DATA_DIR)


@pytest.fixture(scope="module")
def template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def catalog():
    return omni.load_components_catalog(CATALOG_PATH)


@pytest.fixture(scope="module")
def prompt(template, bundle) -> str:
    return omni.render_system_prompt(template, bundle)


class TestPromptTemplate:
    def test_template_validates(self, template):
        omni.validate_prompt_template(template)

    def test_placeholders_and_markers_exactly_once(self, template):
        assert template.count(omni.TOOL_LIST_PLACEHOLDER) == 1
        assert template.count(omni.TOOL_GUIDANCE_PLACEHOLDER) == 1
        assert template.count(omni.WORLD_ATLAS_MARKER) == 1
        assert template.count(omni.WORLD_RULES_MARKER) == 1

    @pytest.mark.parametrize(
        "bad",
        [
            "no placeholders at all <<WORLD_ATLAS>> <<WORLD_RULES>>",
            "{tool_list} {tool_list} {tool_guidance} <<WORLD_ATLAS>> <<WORLD_RULES>>",
            "{tool_list} {tool_guidance} {stray} <<WORLD_ATLAS>> <<WORLD_RULES>>",
            "{tool_list} {tool_guidance} <<WORLD_RULES>>",
        ],
    )
    def test_rejects_malformed_templates(self, bad):
        with pytest.raises(ValueError):
            omni.validate_prompt_template(bad)


class TestRenderedPrompt:
    def test_runtime_placeholders_survive_and_markers_are_expanded(self, prompt):
        assert prompt.count(omni.TOOL_LIST_PLACEHOLDER) == 1
        assert prompt.count(omni.TOOL_GUIDANCE_PLACEHOLDER) == 1
        assert omni.WORLD_ATLAS_MARKER not in prompt
        assert omni.WORLD_RULES_MARKER not in prompt

    def test_atlas_covers_every_district_location_and_job(self, prompt, bundle):
        for district in bundle.districts.values():
            assert district.name in prompt
            for loc in district.locations:
                assert loc.name in prompt
        for job in bundle.jobs.values():
            assert job.title in prompt

    def test_rules_carry_the_shipped_tunables(self, prompt):
        assert f"at most {constants.MAX_WORDS_REPLY} words" in prompt
        assert f"start with {constants.STARTING_MONEY}" in prompt
        assert f"up to {constants.MEMORY_CAP_PER_NPC} memories" in prompt
        assert str(constants.TICKET_BASE) in prompt

    def test_every_request_mode_is_taught(self, prompt):
        for mode in omni.RequestMode:
            assert f"- {mode.value}:" in prompt

    def test_district_culture_is_rendered_in_plain_words(self, prompt):
        assert "mutually loyal" in prompt
        assert "praising the Capitol openly" in prompt
        assert "mutually_loyal" not in prompt


class TestProfiles:
    @pytest.mark.parametrize("key", sorted(omni.PROFILES))
    def test_planner_first_and_tool_calling(self, key, catalog):
        profile = omni.PROFILES[key]
        labels = catalog[profile.planner]["labels"]
        assert "chat" in labels
        assert "tool-calling" in labels
        assert not any("chat" in catalog[c]["labels"] for c in profile.components[1:]), (
            "lemond picks the first `chat` component as planner; keep the LLM first and unique"
        )

    @pytest.mark.parametrize("key", sorted(omni.PROFILES))
    def test_every_panem_role_is_covered(self, key, catalog):
        covered = omni.roles_covered(omni.PROFILES[key], catalog)
        assert {"planner", "vision", "transcription", "embeddings"} <= covered

    @pytest.mark.parametrize("key", sorted(omni.PROFILES))
    def test_no_speech_component(self, key, catalog):
        """Kokoro is dropped from both profiles: a corrupted/incomplete
        Kokoro archive download makes Lemonade fail to load the *whole*
        collection (it's one omni model), which blocked dialogue entirely
        for no benefit -- nothing in panem_bot consumes generated speech
        yet (see the README's Phase 6 notes)."""
        assert "speech" not in omni.roles_covered(omni.PROFILES[key], catalog)

    @pytest.mark.parametrize("key", sorted(omni.PROFILES))
    def test_no_image_generation_component(self, key, catalog):
        for name in omni.PROFILES[key].components:
            assert not {"image", "edit"} & set(catalog[name]["labels"]), name

    @pytest.mark.parametrize("key", sorted(omni.PROFILES))
    def test_components_exist_in_vendored_catalog_with_sizes(self, key, catalog):
        for name in omni.PROFILES[key].components:
            assert name in catalog
            assert float(catalog[name]["size"]) > 0
            assert catalog[name]["recipe"]

    def test_profiles_have_distinct_model_names(self):
        names = [p.model_name for p in omni.PROFILES.values()]
        assert len(names) == len(set(names))
        assert all(n.startswith("user.") for n in names)


class TestCollectionFiles:
    @pytest.mark.parametrize("key", sorted(omni.PROFILES))
    def test_committed_collection_matches_fresh_build(self, key, prompt, catalog):
        profile = omni.PROFILES[key]
        expected = omni.dump_collection(
            omni.build_collection(profile, system_prompt=prompt, catalog=catalog)
        )
        path = LEMONADE_DIR / omni.collection_filename(profile)
        assert path.read_text(encoding="utf-8") == expected, (
            f"{path.name} is stale -- run `uv run python scripts/lemonade_omni.py build`"
        )

    @pytest.mark.parametrize("key", sorted(omni.PROFILES))
    def test_collection_is_an_import_ready_pull_body(self, key):
        profile = omni.PROFILES[key]
        data = json.loads((LEMONADE_DIR / omni.collection_filename(profile)).read_text())
        assert data["model_name"] == profile.model_name
        assert data["recipe"] == omni.COLLECTION_RECIPE
        assert data["components"] == list(profile.components)
        assert [m["model_name"] for m in data["models"]] == list(profile.components)
        assert data["size"] == pytest.approx(sum(m["size"] for m in data["models"]), abs=0.01)
        assert data["system_prompt"].count("{tool_list}") == 1

    def test_build_collection_rejects_unknown_component(self, prompt):
        profile = omni.OmniProfile(
            key="x", model_name="user.X", summary="", components=("Nope",), planner_options={}
        )
        with pytest.raises(KeyError):
            omni.build_collection(profile, system_prompt=prompt, catalog={})


class TestRequestContract:
    def test_header_layout(self):
        ctx = omni.RequestContext(
            mode=omni.RequestMode.DIALOGUE,
            npc={"name": "Old Ferro", "stance": "likes"},
            scene={"location": "The Hob"},
            memories=("Paid her debt early.",),
            constraints={"voice": "am_adam"},
        )
        header = omni.render_request_header(ctx)
        assert header.splitlines()[0] == "[MODE: dialogue]"
        assert "[NPC] name: Old Ferro; stance: likes" in header
        assert "[SCENE] location: The Hob" in header
        assert "[MEMORIES]\n- Paid her debt early." in header
        assert header.splitlines()[-1] == (
            f"[CONSTRAINTS] max_words={constants.MAX_WORDS_REPLY}; voice=am_adam"
        )

    def test_messages_put_header_in_first_system_slot(self):
        ctx = omni.RequestContext(mode=omni.RequestMode.NARRATE)
        messages = omni.build_messages(ctx, [{"role": "user", "content": "hi"}])
        assert messages[0]["role"] == "system"
        assert messages[0]["content"].startswith("[MODE: narrate]")
        assert messages[1] == {"role": "user", "content": "hi"}
