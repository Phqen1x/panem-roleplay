"""Redis key/channel names shared across processes (Plan §2, Spec §2.3).

`panem_bot/redis_keys.py` keeps the bot-only keys (sessions, proxy
mappings, scene presence, talk windows) -- this one lives here instead
because both the writer (`panem_sim`, which has no dependency on
`panem_bot`) and the reader (`panem_api`, Phase 5's Activity bridge, or
`panem_bot`'s own `narrator.run` for `SIM_ALERTS_CHANNEL`) need to agree
on the same key/channel shape without either depending on the sim
package just for a string.
"""

from __future__ import annotations


def positions_key(district_id: int) -> str:
    return f"pos:{district_id}"


SIM_ALERTS_CHANNEL = "sim:alerts"
"""Published to by `panem_sim.tick` (FR-TCK-3) when a tick fails twice in
a row and the loop pauses -- `panem_bot.narrator.run` forwards whatever's
published here straight to `Settings.log_channel_id` verbatim, since a
paused sim is the single most operationally important thing staff can
be told about."""
