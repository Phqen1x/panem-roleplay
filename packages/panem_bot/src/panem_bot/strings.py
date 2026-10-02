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
    "character_death_dm": "Your character **{name}** has died.\n\nCause of death: {cause}",
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
    "outskirts_night_only": "**{name}** can't reach the outskirts except at night.",
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
    "travel_district_instant_ok": "**{name}** arrives in **{district}**.",
    # Jobs and shifts (FR-JOB, reworked: free-typed job_title + shift_phase,
    # set at character creation and changed only by staff -- no more
    # catalog to apply for/quit/list, or a JobOption-driven ladder to check).
    "job_none_set": "**{name}** doesn't have a job set -- ask staff to set one with "
    "`/staff give job`.",
    "work_jailed": "**{name}** is locked up and can't work a shift.",
    "job_no_open_shift": "**{name}** doesn't have a shift open right now.",
    "job_wrong_district": "**{name}** isn't in their home district right now and can't work "
    "this shift -- come back once you've returned home.",
    "shift_no_longer_open": "That shift is no longer open.",
    "shift_already_worked_this_tick": (
        "**{name}** already worked this shift this tick -- try again next tick."
    ),
    "shift_worked_banner": "This shift has already been worked!",
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
    "poach_night_only": "**{name}** can only poach at night -- the outskirts are watched too "
    "closely by day.",
    "poach_nothing_to_poach": "There's nothing worth poaching here.",
    "poach_on_cooldown": "**{name}** already tried poaching this phase of the day.",
    "poach_jailed": "**{name}** is locked up and can't go poaching.",
    "poach_ok": "**{name}** slips back with {qty}x {good}, unseen.",
    "poach_miss": "**{name}** can't land the shot and comes back empty-handed.",
    "poach_game_ready": "**{name}** nocks an arrow at the treeline -- go make the shot!",
    "poach_already_tried": "This hunt has already been tried!",
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
    "lockpick_game_ready": "**{name}** kneels at the cell door, pick in hand -- go pick the lock!",
    "lockpick_already_tried": "This lock has already been tried!",
    # Black market (contraband system: /blackmarket)
    "blackmarket_no_fence": "This district has no black market contact.",
    "blackmarket_not_at_market": "**{name}** needs to be at the district's outskirts to trade "
    "on the black market.",
    "blackmarket_night_only": "**{name}** can only reach the black market at night.",
    "blackmarket_not_trusted": (
        "**{name}** isn't on good enough terms with {fence} to be shown the black market."
    ),
    "blackmarket_good_not_traded": "That good isn't traded on this district's black market.",
    "blackmarket_insufficient_stock": "The black market doesn't have that much {good} left today.",
    # RP-mode crime gating (shared by /steal, /burgle, /poach)
    "crime_mode_forbidden": "**{name}** is in Story mode and has no access to crime.",
    "crime_disabled_by_actor": "**{name}** has crime disabled for themselves right now.",
    "victim_is_story_mode": "{name} is a Story-mode character and can't be targeted this way.",
    "victim_crime_disabled": "{name} has crime disabled and can't be targeted this way.",
    "burgle_mode_forbidden": (
        "**{name}** is in Life mode, which has no housing access -- burglary isn't available."
    ),
    "market_mode_forbidden": "**{name}** is in Story mode and has no access to the markets.",
    "housing_mode_forbidden": (
        "**{name}** doesn't have access to the housing system in their current RP mode."
    ),
    "npc_interaction_mode_forbidden": "**{name}** is in Story mode and can't interact with NPCs.",
    # RP-mode switching & crime toggle (/character mode, /character crime)
    "mode_already_active": "This character is already in {mode} mode.",
    "mode_switch_on_cooldown": (
        "This character switched modes too recently -- {hours} hour(s) left before they can "
        "switch again."
    ),
    "mode_switch_already_pending": (
        "This character already has a mode switch awaiting staff approval."
    ),
    "mode_switch_approved_dm": "Your **{name}**'s switch to **{mode}** mode has been approved!",
    "mode_switch_declined_dm": (
        "Staff declined **{name}**'s switch to **{mode}** mode: {note}\n"
        "Use `/character mode` to try again."
    ),
    "crime_toggle_wrong_mode": "Only Life-mode characters can toggle crime on/off for themselves.",
    "crime_toggle_already_set": "This character's crime setting is already {enabled}.",
    "crime_toggle_on_cooldown": (
        "This character already changed their crime setting today -- "
        "{hours} hour(s) left before they can change it again."
    ),
    # Self-inflicted afflictions/death (/character afflict, /character die)
    "affliction_wrong_mode": (
        "Only Life-mode characters can self-inflict an affliction -- Simulation mode handles "
        "this automatically, and Story mode is excluded from the system entirely."
    ),
    "affliction_cause_required": "Say how it happened.",
    "affliction_type_not_found": "No affliction type by that name -- pick one from the list.",
    "affliction_ok": "**{name}** is now afflicted with **{affliction}**: {cause}",
    "death_wrong_mode": (
        "Only Life-mode characters can end their own character this way -- Simulation mode "
        "handles death automatically, and Story mode is excluded from the system entirely."
    ),
    "character_already_dead": "**{name}** is already dead.",
    "death_ok": "**{name}** has died.",
    # Player-to-player money/goods (/pay, /trade)
    "pay_mode_forbidden": "**{name}** is in Story mode and can't send or receive money this way.",
    "pay_cannot_self": "Can't pay yourself.",
    "pay_invalid_amount": "Enter a positive amount to pay.",
    "pay_insufficient_money": "**{name}** doesn't have that much money.",
    "pay_ok": "**{sender}** pays **{recipient}** {amount} money.",
    "trade_mode_forbidden": "**{name}** is in Story mode and can't trade this way.",
    "trade_cannot_self": "Can't trade with yourself.",
    "trade_negative_money": "Money offered can't be negative.",
    "trade_good_qty_mismatch": "A good and a quantity must be given together, or not at all.",
    "trade_invalid_qty": "Quantity must be a positive number.",
    "trade_empty_offer": "Offer at least some money or a good on each side.",
    "trade_insufficient_money": "**{name}** doesn't have enough money to cover this trade.",
    "trade_insufficient_inventory": "**{name}** doesn't have enough of that good to cover this trade.",
    "trade_not_pending": "This trade offer isn't pending anymore.",
    "trade_offer_sent": "Trade offer sent to **{name}** -- waiting on their response.",
    "trade_accepted": "Trade accepted -- goods and money have changed hands.",
    "trade_declined": "Trade declined.",
    "trade_cancelled": "Trade offer cancelled.",
    "trade_not_found": "No trade offer by that id.",
    "trade_not_yours_to_cancel": "That isn't your trade offer to cancel.",
    # Stealing (contraband system: /steal)
    "steal_target_not_found": "No one by that name is here to steal from.",
    "steal_not_here": "**{name}** needs to be at the same location as the mark to try this.",
    "steal_on_cooldown": "**{name}** already tried to steal something this phase of the day.",
    "steal_jailed": "**{name}** is locked up and can't try that.",
    "steal_ok": "**{name}** lifts {amount}x {good} off {target}, unnoticed.",
    "steal_miss": "**{name}** comes up empty-handed -- and, as far as they can tell, unnoticed.",
    "steal_alerted_escape": "**{name}** is spotted going for {target}'s pocket -- and bolts clear.",
    "steal_caught": (
        "**{name}** is caught stealing from {target} -- fined {fine} money and held for "
        "{jail_ticks} ticks."
    ),
    "steal_game_ready": "**{name}** eyes {target}'s pocket -- go make the lift!",
    "steal_already_tried": "This lift has already been tried!",
    "burgle_jailed": "**{name}** is locked up and can't try that.",
    "burgle_owner_not_found": "No character by that name owns a house here.",
    "burgle_not_a_house": "That property isn't a house.",
    "burgle_wrong_district": "**{name}** needs to be in the house's district to break in.",
    "burgle_own_house": "**{name}** can't burgle their own house.",
    "burgle_owner_home": "{owner} is home right now -- **{name}** can't break in undetected.",
    "burgle_ok": "**{name}** slips out of {owner}'s house with {amount}x {good}.",
    "burgle_miss": "**{name}** finds nothing worth taking -- and slips out unnoticed.",
    "burgle_alerted_escape": "**{name}** is spotted breaking into {owner}'s house -- and bolts.",
    "burgle_caught": (
        "**{name}** is caught breaking into {owner}'s house -- fined {fine} money and held for "
        "{jail_ticks} ticks."
    ),
    "burgle_game_ready": "**{name}** kneels at {owner}'s door, pick in hand -- go pick the lock!",
    "burgle_already_tried": "This break-in has already been tried!",
    # Shipment heists (contraband system: /shipment)
    "shipment_none_here": "There's no shipment sitting here right now.",
    "shipment_jailed": "**{name}** is locked up and can't try that.",
    "shipment_not_here": "**{name}** needs to be at the shipment's location to try this.",
    "shipment_gone": "The peacekeepers already cleared that shipment out.",
    "shipment_ok": "**{name}** slips {amount}x {good} off the shipment before anyone notices.",
    "shipment_miss": "**{name}** can't get near the shipment -- and slips away unnoticed.",
    "shipment_alerted_escape": "**{name}** is spotted going for the shipment -- and bolts clear.",
    "shipment_caught": (
        "**{name}** is caught robbing the shipment -- roughed up, fined {fine} money and held "
        "for {jail_ticks} ticks."
    ),
    "shipment_game_ready": "**{name}** eyes the shipment's guards -- go make the grab!",
    "shipment_already_tried": "This shipment has already been tried!",
    "crimelog_header": "**{name}**'s recent crime log:",
    "crimelog_empty": "**{name}** hasn't attempted any crimes yet.",
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
    # Sustenance (Simulation mode: /eat, /drink, /entertain -- Vitals tab feature)
    "sustenance_mode_forbidden": "**{name}** doesn't need to eat, drink, or entertain "
    "themselves in their current RP mode.",
    "good_not_edible": "**{good}** isn't something {name} can eat.",
    "good_not_drinkable": "**{good}** isn't something {name} can drink.",
    "good_not_healing": "**{good}** isn't something {name} can use to heal.",
    "sustenance_no_inventory": "**{name}** doesn't have any **{good}** to consume.",
    "unknown_game": "**{game}** isn't one of the Vitals tab's entertainment games.",
    "eat_ok": "**{name}** eats some **{good}** and feels better (hunger now {hunger}/100).",
    "drink_ok": "**{name}** drinks some **{good}** (thirst now {thirst}/100).",
    "heal_ok": "**{name}** uses some **{good}** to treat their injuries (health now {health}/100).",
    "entertain_ok": "**{name}** takes some time to unwind (sanity now {sanity}/100).",
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
    # Panem-wide history/lore (`/staff lore ...`, `panem_shared.lore`)
    "panem_history_needs_a_keyword": "Give at least one keyword, comma-separated.",
    "panem_history_text_required": "The history fact can't be empty.",
    "panem_history_text_too_long": "That history fact is too long (4000 characters max).",
    "panem_history_au_notes_too_long": "Those notes are too long (4000 characters max).",
}


def t(key: str, **fmt: object) -> str:
    return STRINGS[key].format(**fmt)
