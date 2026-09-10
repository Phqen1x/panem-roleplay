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
