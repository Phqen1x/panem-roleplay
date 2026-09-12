#!/usr/bin/env python3
"""One-time cleanup for guilds that predate name uniqueness (migration
`7116c3213f6e`): find characters sharing a name (case-insensitively,
excluding rejected applications) and delete or rename one side of each
collision, using the app's own `DATABASE_URL` -- no `psql`/direct SQL
access needed.

    uv run python scripts/resolve_duplicate_names.py                # list only
    uv run python scripts/resolve_duplicate_names.py --delete 42    # pending only
    uv run python scripts/resolve_duplicate_names.py --rename 42 "New Name"

Run with no arguments first, resolve every group it prints, then re-run
until it reports none left. Only then run `uv run alembic upgrade head`.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import select

from panem_bot.errors import ServiceError
from panem_bot.services import characters as characters_svc
from panem_shared.db.models import Character
from panem_shared.db.session import make_engine, make_session_factory
from panem_shared.enums import CharacterStatus
from panem_shared.settings import get_settings


async def _find_duplicate_groups(session) -> dict[str, list[Character]]:
    rows = (
        (
            await session.execute(
                select(Character)
                .where(Character.status != CharacterStatus.REJECTED.value)
                .order_by(Character.id)
            )
        )
        .scalars()
        .all()
    )
    groups: dict[str, list[Character]] = defaultdict(list)
    for row in rows:
        groups[row.name.lower()].append(row)
    return {key: rows for key, rows in groups.items() if len(rows) > 1}


def _print_groups(groups: dict[str, list[Character]]) -> None:
    if not groups:
        print("No duplicate names found. Safe to run `uv run alembic upgrade head`.")
        return
    print(f"{len(groups)} name collision(s) found:\n")
    for name_ci, rows in groups.items():
        print(f'"{name_ci}":')
        for row in rows:
            print(
                f"  id={row.id:<6} name={row.name!r:<24} status={row.status:<10} "
                f"district={row.district_id} user_id={row.user_id}"
            )
        print(
            "  Keep one, then resolve the rest: --delete <id> (pending only) or "
            '--rename <id> "New Name"\n'
        )


async def _delete(session, character_id: int) -> None:
    character = await session.get(Character, character_id)
    if character is None:
        raise SystemExit(f"No character with id={character_id}.")
    if character.status != CharacterStatus.PENDING.value:
        raise SystemExit(
            f"id={character_id} is {character.status}, not pending -- use --rename instead "
            "(deleting an approved/retired/dead character loses its history)."
        )
    name = character.name
    await session.delete(character)
    print(f"Deleted pending id={character_id} ({name!r}).")


async def _rename(session, character_id: int, new_name: str) -> None:
    character = await session.get(Character, character_id)
    if character is None:
        raise SystemExit(f"No character with id={character_id}.")
    try:
        characters_svc.validate_character_fields(
            district_id=character.district_id,
            name=new_name,
            age=character.age,
            appearance=character.appearance,
            backstory=character.backstory,
        )
        await characters_svc.ensure_name_available(
            session, new_name, exclude_character_id=character_id
        )
    except ServiceError as exc:
        raise SystemExit(
            f"Can't rename id={character_id} to {new_name!r}: {exc.reason_key}"
        ) from exc
    old_name = character.name
    character.name = new_name
    print(f"Renamed id={character_id}: {old_name!r} -> {new_name!r}.")


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = make_engine(settings)
    session_factory = make_session_factory(engine)

    async with session_factory() as session, session.begin():
        if args.delete is not None:
            await _delete(session, args.delete)
        elif args.rename is not None:
            character_id, new_name = args.rename
            await _rename(session, int(character_id), new_name)
        else:
            groups = await _find_duplicate_groups(session)
            _print_groups(groups)

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete", type=int, metavar="ID", help="Delete a pending duplicate by character id."
    )
    parser.add_argument(
        "--rename",
        nargs=2,
        metavar=("ID", "NEW_NAME"),
        help="Rename a non-pending duplicate by character id.",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
