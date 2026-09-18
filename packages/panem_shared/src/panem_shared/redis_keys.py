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


def work_pending_key(voice_channel_id: int) -> str:
    """A Discord Activity launched via an `embedded_application` invite
    can't carry a custom URL query param the way a plain link can --
    Discord always loads the app's one configured root URL, only ever
    appending its own params (`channel_id`, `guild_id`, `instance_id`,
    ...). `panem_bot`'s `/work` writes the shift it just opened/synthesized
    here, keyed by the voice channel the invite was made for (short TTL);
    `panem_api`'s `GET /activity/work/for-channel/{channel_id}` reads it
    back using the `channel_id` Discord itself hands `work.html` on
    launch, so the two sides never need a shared OAuth-derived identity
    just to find "which shift is this."""
    return f"work:pending:{voice_channel_id}"


WORK_PENDING_TTL_S = 10 * 60
"""Long enough to join the voice channel and click through the invite
Discord shows, short enough that a stale entry doesn't linger and get
mistaken for a fresher shift once the channel is reused."""


def work_interaction_key(shift_id: int) -> str:
    """`panem_bot`'s `/work` stashes the interaction it used to send the
    minigame-launch message here (its `application_id`/`token`, the pair
    an interaction's webhook-edit endpoint needs -- no bot token
    required), keyed by shift id. `panem_api`'s work-result endpoint reads
    it back once the Activity reports a result, so it can edit that
    original message to remove its now-stale Play/Skip buttons even
    though the result arrived in a different process with no Discord
    gateway connection of its own."""
    return f"work:interaction:{shift_id}"


WORK_INTERACTION_TTL_S = 14 * 60
"""Just under Discord's 15-minute interaction-token validity window -- a
lookup that survives past that point couldn't be used to edit the
original message anyway, so there's no reason to keep it longer."""


SIM_ALERTS_CHANNEL = "sim:alerts"
"""Published to by `panem_sim.tick` (FR-TCK-3) when a tick fails twice in
a row and the loop pauses -- `panem_bot.narrator.run` forwards whatever's
published here straight to `Settings.log_channel_id` verbatim, since a
paused sim is the single most operationally important thing staff can
be told about."""
