"""All user-facing strings (NFR-10). Handlers/services format via `reason_key`
+ `fmt` from `errors.ServiceError`; nothing else in `panem_bot` should contain
a literal player-visible sentence.
"""

from __future__ import annotations

STRINGS: dict[str, str] = {
    # Characters (FR-CHR)
    "banned": "You're not able to use this bot.",
    "too_many_characters": "You already have {limit} characters pending or approved.",
    "invalid_name": "Name must be 1-32 characters (letters, spaces, hyphens, apostrophes).",
    "name_taken": "A character named **{name}** already exists. Please choose a different name.",
    "invalid_age": "Age must be between {min} and {max}.",
    "invalid_appearance": "Appearance must be {max} characters or fewer.",
    "invalid_backstory": "Backstory must be {max} characters or fewer.",
    "invalid_job_title": "Job title can't be empty and must be 80 characters or fewer.",
    "invalid_district": "Not a valid district.",
    "no_district_role": "You don't have a district role yet -- pick one during onboarding, or "
    "ask staff to assign one, before creating a character.",
    "ambiguous_district_role": "You have more than one district role, so it's not clear which "
    "district to create a character in -- ask staff to fix your roles.",
    "character_not_found": "Character not found.",
    "character_not_yours": "That's not your character.",
    "character_not_approved": "That character isn't approved yet.",
    "character_dead": "That character is dead.",
    "character_frozen": "That character is frozen and can't act.",
    "not_pending": "That character isn't awaiting approval.",
    "invalid_avatar_url": "Avatar must be an https image URL ending in .png, .jpg, .jpeg, .webp, or .gif "
    "(512 chars max).",
    "invalid_proxy_tag": "Tag must be 1-12 characters and can't start with `/` or `((`.",
    "proxy_tag_taken": "You're already using that tag for another character.",
    "character_created": "Character **{name}** submitted for approval.",
    "character_approved_dm": "Your character **{name}** was approved! Welcome to {district}.",
    "character_rejected_dm": "Your character **{name}** was rejected: {note}",
    "character_changes_dm": "Staff requested changes to **{name}**: {note}\nUse `/character edit` to resubmit.",
    "character_retired": "**{name}** has been retired.",
    # Sessions / proxying (FR-PRX)
    "rp_needs_thread": "Use this inside a scene.",
    "rp_session_set": "You're now playing **{name}** in this scene.",
    "rp_character_required": "You need to set a character with `/rp` before speaking in this scene.",
    "ooc_cleared": "OOC — session cleared for this scene.",
    "proxy_no_access": "**{name}** can't be here: {reason}.",
    "proxy_character_dead": "dead",
    "proxy_character_jailed": "jailed",
    "proxy_location_restricted": "no access to this location",
    "proxy_wrong_district": "not assigned to or currently in this district",
    "proxy_not_traveled": "hasn't traveled to this location yet",
    # Scenes (FR-SCN)
    "scene_not_traveled": "**{name}** needs to `/travel` to **{location}** before starting a scene there.",
    "scene_at_cap": "District is at its scene limit.",
    "scene_already_open": "You already have an open scene — close it with `/scene close` before starting another.",
    "scene_needs_tag": "This post needs exactly one location tag. It will be archived in 60s if not fixed.",
    "scene_closed": "Scene closed.",
    "scene_moved": "Scene moved to {location}.",
    "scene_not_yours": "Only the creator or staff can do that.",
    "npc_not_here": "{name} isn't at this location right now.",
    # Travel/locations (FR-LOC)
    "location_restricted": "You don't have access to that location.",
    "location_not_found": "Not a valid location for that district.",
    "travel_ok": "**{name}** travels to **{location}**.",
    "no_location_set": "**{name}** hasn't traveled anywhere yet -- use `/travel`.",
    "where_ok": "**{name}** is at **{location}** ({district}).",
    "travel_pick_one": "Give either `location` or `district`, not both or neither.",
    "travel_jailed": "**{name}** is locked up and isn't going anywhere.",
    "travel_already_in_transit": "**{name}** is already on a train -- check `/character status`.",
    "travel_same_district": "**{name}** is already there.",
    "travel_not_at_station": "**{name}** needs to be at **{station}** to catch a train.",
    "travel_no_route": "There's no train route to that district.",
    "travel_insufficient_funds": "**{name}** can't afford the {price}-coin ticket.",
    "travel_district_ok": (
        "**{name}** boards a train for **{district}** -- arriving in {ticks} ticks."
    ),
    # Jobs and shifts (FR-JOB, reworked: free-typed job_title + shift_phase,
    # set at character creation and changed only by staff -- no more
    # catalog to apply for/quit/list, or a JobOption-driven ladder to check).
    "job_none_set": "**{name}** doesn't have a job set -- ask staff to set one with "
    "`/staff give job`.",
    "job_no_open_shift": "**{name}** doesn't have a shift open right now.",
    "shift_no_longer_open": "That shift is no longer open.",
    "work_ok": "**{name}** {outcome}: +{wage} money, {rep_delta:+d} reputation.",
    "work_game_ready": "**{name}** clocks in for **{title}**. Play the shift's minigame below "
    "(opens in your browser) -- you're paid based on how it goes, even if you finish after "
    "the shift ends.",
    "work_game_ready_activity": "**{name}** clocks in for **{title}**. Launch the shift's "
    "minigame in Discord below -- you're paid based on how it goes, even if you finish after "
    "the shift ends.",
    "level_up": " **{name}** is now a **{level}** -- wages just went up!",
    # Residents (FR-NPC)
    "no_residents_in_district": "No residents are seeded for that district yet.",
    "resident_not_found": "No resident by that name in this district.",
    "resident_where_ok": "**{name}** ({job}) is currently at **{location}**.",
    # Dialogue (Phase 6, LLM-driven NPC talk)
    "talk_not_here": "**{name}** isn't at your location right now -- use `/resident where` to "
    "find them, or `/travel` to meet them.",
    "npc_needs_a_moment": "**{name}** needs a moment before talking more this hour.",
    # Market (FR-ECO-3/4)
    "market_invalid_qty": "Quantity must be a positive number.",
    "market_not_at_market": "**{name}** needs to be at a market location to trade.",
    "market_good_not_traded": "That good isn't bought or sold in this district.",
    "market_insufficient_funds": "**{name}** can't afford that.",
    "market_insufficient_inventory": "**{name}** doesn't have that many to sell.",
    "market_no_goods_traded": "Nothing is bought or sold in this district yet.",
    "market_bought_ok": "**{name}** buys {qty}x {good} for {total} money.",
    "market_sold_ok": "**{name}** sells {qty}x {good} for {total} money.",
    "market_caught_illicit": (
        " A peacekeeper notices -- fined {fine} money and held for {jail_ticks} ticks."
    ),
    "inventory_empty": "**{name}** isn't carrying anything.",
    # Generic
    "world_paused": "The world is paused.",
    "unexpected_error": "Something went wrong (ref `{ref}`).",
    "staff_only": "Staff only.",
    "staff_good_not_found": "Not a valid good id.",
}


def t(key: str, **fmt: object) -> str:
    return STRINGS[key].format(**fmt)
