"""Redis key builders shared across processes (Plan §2, Spec §2.3).

`panem_bot/redis_keys.py` keeps the bot-only keys (sessions, proxy
mappings, scene presence, talk windows) -- this one lives here instead
because both the writer (`panem_sim`, which has no dependency on
`panem_bot`) and the reader (`panem_api`, Phase 5's Activity bridge)
need to agree on the same key shape without either depending on the
bot package just for a string format.
"""

from __future__ import annotations


def positions_key(district_id: int) -> str:
    return f"pos:{district_id}"
