"""Jailing (contraband system): re-exports `panem_shared.jail`'s
priors-scaled sentencing so every existing `jail_svc.commit_to_jail(...)`
call site in `panem_bot` keeps working unchanged -- the logic itself
lives in `panem_shared` because `panem_api`'s `/work` minigame result
endpoint needs the matching `resolve_illicit_heat` too and can't depend
on `panem_bot` to get it (same reasoning as `panem_shared.shifts`)."""

from __future__ import annotations

from panem_shared.jail import (
    commit_to_jail as commit_to_jail,
)
