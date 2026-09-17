from __future__ import annotations

from pathlib import Path

import pytest

from panem_shared.content.errors import ContentValidationError
from panem_shared.content.loader import load_content

REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class TestRealContentFiles:
    """The shipped `data/` directory must always validate cleanly."""

    def test_loads_all_13_districts(self):
        bundle = load_content(REPO_DATA_DIR)
        assert sorted(bundle.districts.keys()) == list(range(13))

    def test_every_district_has_station_and_public(self):
        bundle = load_content(REPO_DATA_DIR)
        for district in bundle.districts.values():
            kinds = {loc.kind.value for loc in district.locations}
            assert "station" in kinds
            assert "public" in kinds

    def test_goods_and_jobs_and_routes_present(self):
        bundle = load_content(REPO_DATA_DIR)
        assert len(bundle.goods) > 0
        assert len(bundle.jobs) > 0
        assert len(bundle.routes) > 0

    def test_jobs_have_three_options(self):
        bundle = load_content(REPO_DATA_DIR)
        for job in bundle.jobs.values():
            assert len(job.options) == 3

    def test_d12_matches_plan_worked_example(self):
        bundle = load_content(REPO_DATA_DIR)
        d12 = bundle.district(12)
        assert d12.industry == "coal"
        assert d12.quota.good == "coal"
        location_ids = {loc.id for loc in d12.locations}
        assert {
            "square",
            "hob",
            "mine",
            "justice",
            "seam",
            "merchant_row",
            "meadow",
            "station",
        } <= location_ids

    def test_career_districts_produce_career_training_via_an_academy_job(self):
        bundle = load_content(REPO_DATA_DIR)
        for district_id in (1, 2, 4, 9):
            district = bundle.district(district_id)
            assert "career_training" in district.produces
            location_ids = {loc.id for loc in district.locations}
            assert "academy" in location_ids
            academy_jobs = [j for j in bundle.jobs.values() if j.workplace == "academy"]
            assert any(
                j.district == district_id and "career_training" in j.produces for j in academy_jobs
            )

    def test_every_non_capitol_district_has_exactly_one_illicit_good_and_market(self):
        bundle = load_content(REPO_DATA_DIR)
        contraband_goods = {g.id for g in bundle.goods.values() if g.category == "contraband"}
        for district_id in range(1, 13):
            district = bundle.district(district_id)
            assert len(district.illicit_produces) == 1, district_id
            assert district.illicit_produces[0] in contraband_goods
            assert any(loc.illicit for loc in district.locations), district_id
        assert bundle.district(0).illicit_produces == []

    def test_every_district_with_illicit_goods_has_exactly_one_fence_npc(self):
        bundle = load_content(REPO_DATA_DIR)
        for district_id in range(1, 13):
            fences = [
                npc for npc in bundle.npcs_for_district(district_id) if npc.black_market_contact
            ]
            assert len(fences) == 1, district_id
        assert not any(npc.black_market_contact for npc in bundle.npcs_for_district(0))

    def test_every_district_has_authored_npcs(self):
        """`scripts/npc_generate.py` has been run for every shipped
        district (Phase 3 content authoring) -- a district with none
        would silently fall back to the synthetic population instead,
        which would be a regression worth catching."""
        bundle = load_content(REPO_DATA_DIR)
        for district_id in bundle.districts:
            assert bundle.npcs_for_district(district_id), (
                f"district {district_id} has no authored data/npcs/*.yaml content"
            )

    def test_authored_npcs_have_unique_ids_and_valid_backstories(self):
        bundle = load_content(REPO_DATA_DIR)
        assert len(bundle.npcs) > 0
        for npc in bundle.npcs.values():
            assert npc.backstory
            assert npc.name in npc.backstory


class TestLoadNpcs:
    def _base_district_files(self, tmp_path):
        districts_dir = tmp_path / "districts"
        districts_dir.mkdir()
        (districts_dir / "d1.yaml").write_text(
            """
id: 1
name: Test District
industry: testing
population_base: 100
culture: {}
locations:
  - {id: square, name: Square, kind: public}
  - {id: station, name: Station, kind: station}
  - {id: home, name: Home, kind: residential}
map:
  image: x.png
  width: 10
  height: 10
  location_coords: {square: [0, 0], station: [1, 1], home: [2, 2]}
"""
        )
        (tmp_path / "goods.yaml").write_text("[]")
        (tmp_path / "jobs.yaml").write_text(
            """
- id: clerk
  district: 1
  title: Clerk
  workplace: square
  wage: 10
  shift_phase: morning
  slots: 5
  options:
    - {label: a}
    - {label: b}
    - {label: c}
"""
        )

    def test_missing_npcs_dir_loads_empty(self, tmp_path):
        self._base_district_files(tmp_path)
        bundle = load_content(tmp_path)
        assert bundle.npcs == {}

    def test_loads_valid_npc_content(self, tmp_path):
        self._base_district_files(tmp_path)
        npcs_dir = tmp_path / "npcs"
        npcs_dir.mkdir()
        (npcs_dir / "d1.yaml").write_text(
            """
- id: d1_npc_001
  district: 1
  name: Test Npc
  age: 30
  job_id: clerk
  home_location_id: home
  traits: [kind, brave]
  backstory: A short life story.
  appearance: Tall.
"""
        )
        bundle = load_content(tmp_path)
        assert list(bundle.npcs) == ["d1_npc_001"]
        assert bundle.npcs_for_district(1)[0].name == "Test Npc"

    def test_duplicate_npc_id_rejected(self, tmp_path):
        self._base_district_files(tmp_path)
        npcs_dir = tmp_path / "npcs"
        npcs_dir.mkdir()
        entry = """
- id: dupe
  district: 1
  name: A
  age: 30
  home_location_id: home
  backstory: x
"""
        (npcs_dir / "d1.yaml").write_text(entry)
        (npcs_dir / "d1b.yaml").write_text(entry)
        with pytest.raises(ContentValidationError, match="duplicate npc id"):
            load_content(tmp_path)

    def test_npc_referencing_unknown_district_rejected(self, tmp_path):
        self._base_district_files(tmp_path)
        npcs_dir = tmp_path / "npcs"
        npcs_dir.mkdir()
        (npcs_dir / "d1.yaml").write_text(
            """
- id: ghost
  district: 9
  name: Ghost
  age: 30
  home_location_id: home
  backstory: x
"""
        )
        with pytest.raises(ContentValidationError, match="no district file"):
            load_content(tmp_path)

    def test_npc_referencing_unknown_home_location_rejected(self, tmp_path):
        self._base_district_files(tmp_path)
        npcs_dir = tmp_path / "npcs"
        npcs_dir.mkdir()
        (npcs_dir / "d1.yaml").write_text(
            """
- id: lost
  district: 1
  name: Lost
  age: 30
  home_location_id: nowhere
  backstory: x
"""
        )
        with pytest.raises(ContentValidationError, match="home_location_id"):
            load_content(tmp_path)

    def test_npc_referencing_unknown_job_rejected(self, tmp_path):
        self._base_district_files(tmp_path)
        npcs_dir = tmp_path / "npcs"
        npcs_dir.mkdir()
        (npcs_dir / "d1.yaml").write_text(
            """
- id: jobless
  district: 1
  name: Jobless
  age: 30
  job_id: nonexistent_job
  home_location_id: home
  backstory: x
"""
        )
        with pytest.raises(ContentValidationError, match="job_id"):
            load_content(tmp_path)

    def test_npc_job_in_wrong_district_rejected(self, tmp_path):
        self._base_district_files(tmp_path)
        (tmp_path / "districts" / "d2.yaml").write_text(
            """
id: 2
name: Other District
industry: testing
population_base: 100
culture: {}
locations:
  - {id: square, name: Square, kind: public}
  - {id: station, name: Station, kind: station}
map:
  image: x.png
  width: 10
  height: 10
  location_coords: {square: [0, 0], station: [1, 1]}
"""
        )
        npcs_dir = tmp_path / "npcs"
        npcs_dir.mkdir()
        (npcs_dir / "d2.yaml").write_text(
            """
- id: mismatched
  district: 2
  name: Mismatched
  age: 30
  job_id: clerk
  home_location_id: square
  backstory: x
"""
        )
        with pytest.raises(ContentValidationError, match="belongs to district"):
            load_content(tmp_path)


class TestValidationFailures:
    def test_missing_goods_file_raises(self, tmp_path):
        (tmp_path / "jobs.yaml").write_text("[]")
        (tmp_path / "districts").mkdir()
        with pytest.raises(ContentValidationError):
            load_content(tmp_path)

    def test_empty_districts_dir_loads_empty_bundle(self, tmp_path):
        """The generic loader doesn't enforce "all 13 districts present" --
        that's a caller-level check, since a partial pilot rollout (Plan
        §11: "Pilot with districts 12, 3, 11, and the Capitol") is valid."""
        (tmp_path / "goods.yaml").write_text("[]")
        (tmp_path / "jobs.yaml").write_text("[]")
        (tmp_path / "districts").mkdir()
        bundle = load_content(tmp_path)
        assert bundle.districts == {}

    def test_bad_yaml_raises_with_path(self, tmp_path):
        districts_dir = tmp_path / "districts"
        districts_dir.mkdir()
        (districts_dir / "d1.yaml").write_text("id: [unterminated")
        (tmp_path / "goods.yaml").write_text("[]")
        (tmp_path / "jobs.yaml").write_text("[]")
        with pytest.raises(ContentValidationError) as exc_info:
            load_content(tmp_path)
        assert "d1.yaml" in exc_info.value.file_path

    def test_district_without_station_rejected(self, tmp_path):
        districts_dir = tmp_path / "districts"
        districts_dir.mkdir()
        (districts_dir / "d1.yaml").write_text(
            """
id: 1
name: Test District
industry: testing
population_base: 100
culture: {}
locations:
  - {id: square, name: Square, kind: public}
map:
  image: x.png
  width: 10
  height: 10
  location_coords:
    square: [0, 0]
"""
        )
        (tmp_path / "goods.yaml").write_text("[]")
        (tmp_path / "jobs.yaml").write_text("[]")
        with pytest.raises(ContentValidationError, match="station"):
            load_content(tmp_path)

    def test_duplicate_location_names_rejected(self, tmp_path):
        """Discord forum tags are keyed by name, not id, and reject
        duplicates outright -- two locations with different ids but the
        same name must fail content validation before they ever reach
        Discord (this is what data/districts/d8.yaml actually shipped
        with once: `market` and `loading_dock` both named "The Loading
        Dock")."""
        districts_dir = tmp_path / "districts"
        districts_dir.mkdir()
        (districts_dir / "d1.yaml").write_text(
            """
id: 1
name: Test District
industry: testing
population_base: 100
culture: {}
locations:
  - {id: square, name: Square, kind: public}
  - {id: station, name: Station, kind: station}
  - {id: dock, name: The Dock, kind: market}
  - {id: pier, name: The Dock, kind: market}
map:
  image: x.png
  width: 10
  height: 10
  location_coords: {square: [0, 0], station: [1, 1], dock: [2, 2], pier: [3, 3]}
"""
        )
        (tmp_path / "goods.yaml").write_text("[]")
        (tmp_path / "jobs.yaml").write_text("[]")
        with pytest.raises(ContentValidationError, match="duplicate location names"):
            load_content(tmp_path)

    def test_job_with_wrong_option_count_rejected(self, tmp_path):
        districts_dir = tmp_path / "districts"
        districts_dir.mkdir()
        (districts_dir / "d1.yaml").write_text(
            """
id: 1
name: Test District
industry: testing
population_base: 100
culture: {}
locations:
  - {id: square, name: Square, kind: public}
  - {id: station, name: Station, kind: station}
  - {id: work, name: Work, kind: workplace}
map:
  image: x.png
  width: 10
  height: 10
  location_coords: {square: [0, 0], station: [1, 1], work: [2, 2]}
"""
        )
        (tmp_path / "goods.yaml").write_text("[]")
        (tmp_path / "jobs.yaml").write_text(
            """
- id: bad_job
  district: 1
  title: Bad Job
  workplace: work
  wage: 10
  shift_phase: morning
  slots: 5
  options:
    - {label: only one}
"""
        )
        with pytest.raises(ContentValidationError):
            load_content(tmp_path)

    def test_job_referencing_unknown_workplace_rejected(self, tmp_path):
        districts_dir = tmp_path / "districts"
        districts_dir.mkdir()
        (districts_dir / "d1.yaml").write_text(
            """
id: 1
name: Test District
industry: testing
population_base: 100
culture: {}
locations:
  - {id: square, name: Square, kind: public}
  - {id: station, name: Station, kind: station}
map:
  image: x.png
  width: 10
  height: 10
  location_coords: {square: [0, 0], station: [1, 1]}
"""
        )
        (tmp_path / "goods.yaml").write_text("[]")
        (tmp_path / "jobs.yaml").write_text(
            """
- id: ghost_job
  district: 1
  title: Ghost Job
  workplace: nonexistent
  wage: 10
  shift_phase: morning
  slots: 5
  options:
    - {label: a}
    - {label: b}
    - {label: c}
"""
        )
        with pytest.raises(ContentValidationError, match="workplace"):
            load_content(tmp_path)
