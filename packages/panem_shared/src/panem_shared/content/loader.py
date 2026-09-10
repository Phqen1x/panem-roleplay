"""Load and validate `data/*.yaml` content files (Spec §5.4).

Every loader function raises `ContentValidationError` naming the offending
file and field on any problem; callers (process boot, `/staff reload
content`) decide whether that means "refuse to start" or "keep the
previous content" (NFR-12).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from panem_shared.content.errors import ContentValidationError
from panem_shared.content.schemas import District, Good, Job, Route


def _read_yaml(path: Path) -> object:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ContentValidationError(str(path), f"invalid YAML: {exc}") from exc
    except OSError as exc:
        raise ContentValidationError(str(path), f"cannot read file: {exc}") from exc


def _validate_one[T: BaseModel](path: Path, model: type[T], raw: object) -> T:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ContentValidationError(str(path), exc.errors().__repr__()) from exc


def _validate_list[T: BaseModel](path: Path, model: type[T], raw: object) -> list[T]:
    try:
        return TypeAdapter(list[model]).validate_python(raw)  # type: ignore[valid-type]
    except ValidationError as exc:
        raise ContentValidationError(str(path), exc.errors().__repr__()) from exc


def load_districts(data_dir: Path) -> tuple[dict[int, District], dict[int, Path]]:
    districts: dict[int, District] = {}
    paths: dict[int, Path] = {}
    districts_dir = data_dir / "districts"
    for path in sorted(districts_dir.glob("*.yaml")):
        raw = _read_yaml(path)
        district = _validate_one(path, District, raw)
        if district.id in districts:
            raise ContentValidationError(
                str(path), f"duplicate district id {district.id} (already loaded from another file)"
            )
        districts[district.id] = district
        paths[district.id] = path
    return districts, paths


def load_goods(data_dir: Path) -> dict[str, Good]:
    path = data_dir / "goods.yaml"
    raw = _read_yaml(path)
    goods = _validate_list(path, Good, raw)
    by_id: dict[str, Good] = {}
    for good in goods:
        if good.id in by_id:
            raise ContentValidationError(path.as_posix(), f"duplicate good id {good.id!r}")
        by_id[good.id] = good
    return by_id


def load_jobs(data_dir: Path) -> dict[str, Job]:
    path = data_dir / "jobs.yaml"
    raw = _read_yaml(path)
    jobs = _validate_list(path, Job, raw)
    by_id: dict[str, Job] = {}
    for job in jobs:
        if job.id in by_id:
            raise ContentValidationError(path.as_posix(), f"duplicate job id {job.id!r}")
        by_id[job.id] = job
    return by_id


def load_routes(data_dir: Path) -> list[Route]:
    path = data_dir / "routes.yaml"
    if not path.exists():
        return []
    raw = _read_yaml(path)
    return _validate_list(path, Route, raw)


@dataclass(frozen=True, slots=True)
class ContentBundle:
    districts: dict[int, District]
    goods: dict[str, Good]
    jobs: dict[str, Job]
    routes: list[Route]

    def district(self, district_id: int) -> District:
        try:
            return self.districts[district_id]
        except KeyError:
            raise ContentValidationError(
                "data/districts/", f"no district file defines id {district_id}"
            ) from None

    def jobs_for_district(self, district_id: int) -> list[Job]:
        return [job for job in self.jobs.values() if job.district == district_id]


def _cross_validate(bundle: ContentBundle, data_dir: Path, district_paths: dict[int, Path]) -> None:
    """Checks that span more than one file (job workplaces, route endpoints, ...)."""
    for job in bundle.jobs.values():
        district = bundle.districts.get(job.district)
        if district is None:
            raise ContentValidationError(
                (data_dir / "jobs.yaml").as_posix(),
                f"job {job.id!r} references district {job.district}, which has no district file",
            )
        location_ids = {loc.id for loc in district.locations}
        if job.workplace not in location_ids:
            raise ContentValidationError(
                (data_dir / "jobs.yaml").as_posix(),
                f"job {job.id!r} workplace {job.workplace!r} is not a location in district "
                f"{job.district}",
            )
        if job.ladder_next is not None and job.ladder_next not in bundle.jobs:
            raise ContentValidationError(
                (data_dir / "jobs.yaml").as_posix(),
                f"job {job.id!r} ladder_next {job.ladder_next!r} is not a defined job id",
            )

    for district in bundle.districts.values():
        district_path = district_paths[district.id].as_posix()
        for loc in district.locations:
            for job_id in loc.job_ids:
                if job_id not in bundle.jobs:
                    raise ContentValidationError(
                        district_path,
                        f"location {loc.id!r} references job_id {job_id!r}, which is not "
                        "defined in jobs.yaml",
                    )
            for job_id in loc.access_jobs:
                if job_id not in bundle.jobs:
                    raise ContentValidationError(
                        district_path,
                        f"location {loc.id!r} access_jobs references undefined job_id {job_id!r}",
                    )

    for route in bundle.routes:
        for end, label in ((route.from_, "from"), (route.to, "to")):
            if end not in bundle.districts:
                raise ContentValidationError(
                    (data_dir / "routes.yaml").as_posix(),
                    f"route {label}={end} is not a defined district",
                )
        if route.good not in bundle.goods:
            raise ContentValidationError(
                (data_dir / "routes.yaml").as_posix(),
                f"route {route.from_}->{route.to} references undefined good {route.good!r}",
            )


def load_content(data_dir: Path) -> ContentBundle:
    """Load and validate every content file under `data_dir`.

    Raises `ContentValidationError` on the first problem found; callers
    that need "keep the previous content on failure" semantics (NFR-12)
    should catch this around the whole call.
    """
    districts, district_paths = load_districts(data_dir)
    bundle = ContentBundle(
        districts=districts,
        goods=load_goods(data_dir),
        jobs=load_jobs(data_dir),
        routes=load_routes(data_dir),
    )
    _cross_validate(bundle, data_dir, district_paths)
    return bundle
