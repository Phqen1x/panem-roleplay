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
    "job_title_prompt": "Next, tell us what job your character wants.",
    "mastery_needs_value": "Provide either shifts_completed or level.",
    "invalid_shifts_completed": "shifts_completed must be zero or greater.",
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
    "travel_insufficient_transport": (
        "**{name}** doesn't have {qty} units of transport banked for the trip -- "
        "buy some at a district market first."
    ),
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
    "shift_already_worked_this_tick": (
        "**{name}** already worked this shift this tick -- try again next tick."
    ),
    "work_ok": "**{name}** {outcome}: +{wage} money, {rep_delta:+d} reputation.",
    "work_game_ready": "**{name}** clocks in for **{title}**. Play the shift's minigame below "
    "(opens in your browser) -- you're paid based on how it goes, even if you finish after "
    "the shift ends. Not in the mood? Skip it for a flat, neutral wage instead.",
    "work_game_ready_activity": "**{name}** clocks in for **{title}**. Launch the shift's "
    "minigame in Discord below -- you're paid based on how it goes, even if you finish after "
    "the shift ends. Not in the mood? Skip it for a flat, neutral wage instead.",
    "level_up": " **{name}** is now a **{level}** -- wages just went up!",
    "illicit_work_arrested": (
        " Peacekeepers finally catch up with **{name}** -- fined, jailed, and their name is "
        "known now."
    ),
    # Residents (FR-NPC)
    "no_residents_in_district": "No residents are seeded for that district yet.",
    "resident_not_found": "No resident by that name in this district.",
    "resident_where_ok": "**{name}** ({job}) is currently at **{location}**.",
    # Dialogue (Phase 6, LLM-driven NPC talk)
    "talk_not_here": "**{name}** isn't at your location right now -- use `/resident where` to "
    "find them, or `/travel` to meet them.",
    "npc_needs_a_moment": "**{name}** needs a moment before talking more this hour.",
    "talk_npc_wrong_district": "**{name}** is only assigned to their own district and can't "
    "be pulled into a thread outside it.",
    "talk_npc_no_access": "**{name}**'s job doesn't give them access to this location.",
    # Market (FR-ECO-3/4)
    "market_invalid_qty": "Quantity must be a positive number.",
    "market_not_at_market": "**{name}** needs to be at a market location to trade.",
    "market_good_not_traded": "That good isn't bought or sold in this district.",
    "market_insufficient_funds": "**{name}** can't afford that.",
    "market_insufficient_inventory": "**{name}** doesn't have that many to sell.",
    "market_insufficient_stock": "The market doesn't have that much {good} left today.",
    "market_no_goods_traded": "Nothing is bought or sold in this district yet.",
    "market_bought_ok": "**{name}** buys {qty}x {good} for {total} money.",
    "market_sold_ok": "**{name}** sells {qty}x {good} for {total} money.",
    "market_caught_illicit": (
        " A peacekeeper notices -- fined {fine} money and held for {jail_ticks} ticks."
    ),
    "inventory_empty": "**{name}** isn't carrying anything.",
    "poach_no_outskirts": "There's nowhere to poach in this district.",
    "poach_not_at_outskirts": "**{name}** needs to be at **{location}** to try poaching.",
    "poach_nothing_to_poach": "There's nothing worth poaching here.",
    "poach_ok": "**{name}** slips back with {qty}x {good}, unseen.",
    "poach_caught": (
        "**{name}** is caught poaching -- fined {fine} money and held for {jail_ticks} ticks."
    ),
    # Jail (contraband system: /bail, /lockpick)
    "jail_not_jailed": "**{name}** isn't locked up.",
    "bail_insufficient_funds": "**{name}** can't cover the {cost} money bail needs.",
    "bail_ok": "**{name}** pays {cost} money in bail and walks free.",
    "lockpick_no_tries_left": "**{name}** is out of lockpicking attempts for this stay.",
    "lockpick_success": "**{name}** works the lock loose and slips out.",
    "lockpick_fail": "The lock holds. **{name}** has {tries_left} attempt(s) left.",
    # Housing (buying/renting houses/apartments/inns, fatigue, sleep)
    "housing_not_found": "No property by that id.",
    "housing_nothing_available": "Nothing is available in this district right now.",
    "housing_not_for_sale": "That property isn't for sale.",
    "housing_already_owned": "Someone already owns that property.",
    "housing_wrong_district": "**{name}** can only buy a house in their own district.",
    "housing_tier_too_low": "**{name}** isn't a high enough job level yet -- that house needs at least {tier}.",
    "housing_insufficient_funds": "**{name}** can't afford that.",
    "housing_bought_ok": "**{name}** buys the {kind} (`#{property_id}`) for {price} money.",
    "housing_not_an_apartment": "That property isn't an apartment unit.",
    "housing_unit_already_leased": "That unit is already leased.",
    "housing_rented_ok": "**{name}** signs a lease (`#{property_id}`) for {price} money/day rent.",
    "housing_complex_not_found": "No apartment complex by that id.",
    "housing_complex_partially_owned": "Someone already owns part of that complex.",
    "housing_complex_bought_ok": (
        "**{name}** buys out the whole complex (`{complex_id}`, {units} units) for {price} money."
    ),
    "housing_no_home": "**{name}** doesn't have a fixed home right now.",
    "housing_not_a_tenant": "**{name}** owns their home outright -- there's no lease to end.",
    "housing_moved_out_ok": "**{name}** moves out.",
    "housing_status": "**{name}** -- home: {home}; fatigue: {fatigue}/100.",
    "housing_not_an_inn": "That property isn't an inn.",
    "housing_inn_stay_ok": "**{name}** pays {price} money for a night (and a meal) at the inn.",
    "housing_not_your_property": "**{name}** doesn't own that property.",
    "housing_down_payment_too_much": "**{name}** can't afford the down payment.",
    "housing_financed_ok": (
        "**{name}** buys the {kind} (`#{property_id}`) with {down_payment} down -- "
        "{payment} money/day for the rest."
    ),
    "housing_no_mortgage": "That property doesn't have a mortgage or maintenance balance.",
    "housing_refinance_too_much": "**{name}** can't borrow that much against that property.",
    "housing_refinanced_ok": (
        "**{name}** refinances (`#{property_id}`) for {amount} money -- now {payment} money/day."
    ),
    "housing_sold_ok": "**{name}** lists the property (`#{property_id}`) for {price} money.",
    "housing_delisted_ok": "**{name}** takes the property (`#{property_id}`) off the market.",
    "housing_rent_out_ok": "**{name}** sets the unit's rent (`#{property_id}`) to {price} money/day.",
    "housing_auction_already_open": "That property already has an open auction.",
    "housing_auction_started_ok": (
        "**{name}** puts the property (`#{property_id}`) up for auction, minimum bid {minimum}."
    ),
    "housing_auction_not_found": "No open auction for that property.",
    "housing_bid_too_low": "That bid isn't higher than the current one.",
    "housing_bid_ok": "**{name}** bids {amount} on `#{property_id}`.",
    "sleep_wrong_phase": "**{name}** can only sleep between the end of the evening shift and "
    "the start of the morning one.",
    "sleep_ok": "**{name}** sleeps {ticks} tick(s) and restores {restored} fatigue "
    "(now {fatigue}/100).",
    # NPC engagements (group RP threads with one or more NPCs, plus other players'
    # characters by invitation)
    "engagement_no_location": "**{name}** hasn't traveled anywhere yet -- use `/travel`, "
    "or pass `location`.",
    "engagement_not_traveled": "**{name}** needs to `/travel` to **{location}** before "
    "starting an engagement there.",
    "engagement_needs_participants": "Name at least one NPC or character to include.",
    "engagement_nobody_available": "Nobody named could join right now.",
    "engagement_npc_busy_shift": "**{name}** is working right now -- try **{location}** instead.",
    "engagement_npc_busy_sleep": "**{name}** is asleep for the night.",
    "engagement_npc_no_access": "**{name}**'s job doesn't give them access to **{location}**.",
    "engagement_started_ok": "Engagement started: {thread}",
    "engagement_not_yours": "Only the creator or staff can do that.",
    "engagement_ended_ok": "Engagement ended.",
    "engagement_invite_prompt": "{mention}, **{character}** has been invited to join this "
    "engagement.",
    "engagement_invite_not_yours": "That invitation isn't for you.",
    "engagement_invite_accepted": "**{character}** joined the engagement.",
    "engagement_invite_declined": "**{character}** declined to join.",
    "engagement_invite_gone": "This engagement no longer exists.",
    "engagement_invite_already_handled": "This invitation has already been answered.",
    "engagement_join_not_here": "**{name}** needs to `/travel` to **{location}** first.",
    "engagement_join_already_in": "**{name}** is already part of this engagement.",
    "engagement_join_ok": "**{name}** joins the engagement.",
    "engagement_closing_line": "*The gathering breaks up.*",
    # Generic
    "world_paused": "The world is paused.",
    "unexpected_error": "Something went wrong (ref `{ref}`).",
    "staff_only": "Staff only.",
    "staff_npc_not_found": "No resident found -- if two residents share a name (the "
    "generator samples names per district, so that happens), pick one from the "
    "autocomplete suggestions to tell them apart.",
    "staff_good_not_found": "Not a valid good id.",
}


def t(key: str, **fmt: object) -> str:
    return STRINGS[key].format(**fmt)
