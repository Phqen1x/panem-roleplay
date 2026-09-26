"""Injuries, afflictions, and death (`AfflictionType`/`CharacterAffliction`).

Moved to `panem_shared.afflictions` (same reasoning as every other
`panem_shared` move this session) because the web dashboard needs this too
and `panem_api` cannot import `panem_bot`. Re-exported here for every bot
call site (`panem_bot.cogs.needs`'s eat/drink/entertain cure hook today;
`panem_bot.cogs.characters`'s self-inflict command in a later milestone).
"""

from __future__ import annotations

from panem_shared.afflictions import AUTO_DEATH_MESSAGE as AUTO_DEATH_MESSAGE
from panem_shared.afflictions import apply_auto_afflictions as apply_auto_afflictions
from panem_shared.afflictions import apply_auto_death as apply_auto_death
from panem_shared.afflictions import apply_manual_affliction as apply_manual_affliction
from panem_shared.afflictions import check_and_cure as check_and_cure
from panem_shared.afflictions import mark_dead as mark_dead
