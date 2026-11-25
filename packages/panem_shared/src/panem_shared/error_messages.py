"""Plain-English versions of every `panem_shared.errors.ServiceError`
`reason_key` (and the handful of ad-hoc literal codes `panem_api.
dashboard_routes` raises directly), for the Activity dashboard.

`panem_bot/src/panem_bot/strings.py`'s `STRINGS` dict already has a
hand-written message for most of these `reason_key`s -- but it's written
for a Discord reply (`**{name}**` bold-mentions a specific character,
since a Discord command can act on any of the player's characters in one
message). The dashboard is always already looking at one character's own
panel, so repeating that character's name in every error reads oddly,
and Discord's `**bold**` markdown would show up as literal asterisks in
a plain HTML result line. This is the same set of situations, reworded
in second person with no markdown, for `panem_api.dashboard_routes.
_http_from_service_error` (and its `map3d_routes` twin) to use as the
HTTP `detail` instead of the bare `reason_key` -- which is what used to
reach players completely unformatted (e.g. "poach_no_outskirts"), merely
cosmetically underscore-stripped by the dashboard's own `humanize()`
(`static/tabs/_shared.js`) into "Poach no outskirts." -- a sentence
shape, but not a sentence.

Each value may reference `{fmt}` keys the same way `strings.py`'s `t()`
does; `error_message()` formats it the same way but never raises if a
key the service layer didn't actually pass is missing -- a staff-only,
rarely-hit validation path moving no `fmt` at all should still be safe
to include a `{field}`-shaped hint in client code; it just degrades to
the literal unformatted sentence instead of 500ing the request.
"""

from __future__ import annotations

ERROR_MESSAGES: dict[str, str] = {
    # Characters (FR-CHR)
    "banned": "You're not able to use this bot.",
    "too_many_characters": "You already have {limit} characters pending or approved.",
    "invalid_name": "Name must be 1-32 characters (letters, spaces, hyphens, apostrophes).",
    "name_taken": "A character with that name already exists -- please choose a different name.",
    "invalid_age": "Age must be between {min} and {max}.",
    "invalid_appearance": "Appearance must be {max} characters or fewer.",
    "invalid_backstory": "Backstory must be {max} characters or fewer.",
    "invalid_job_title": "Job title can't be empty and must be 80 characters or fewer.",
    "mastery_needs_value": "Provide either a shift count or a level.",
    "invalid_shifts_completed": "Completed shifts must be zero or greater.",
    "invalid_district": "Not a valid district.",
    "capitol_requires_authorization": (
        "Creating a Capitol character needs staff sign-off first -- ask a staff member to "
        "authorize you, then try again."
    ),
    "character_not_found": "Character not found.",
    "character_not_yours": "That's not your character.",
    "character_not_approved": "This character isn't approved yet.",
    "character_dead": "This character is dead.",
    "character_frozen": "This character is frozen and can't act.",
    "not_pending": "That character isn't awaiting approval.",
    "invalid_avatar_url": (
        "Avatar must be an https image URL ending in .png, .jpg, .jpeg, .webp, or .gif "
        "(512 characters max)."
    ),
    "invalid_proxy_tag": "Tag must be 1-12 characters and can't start with / or ((.",
    "proxy_tag_taken": "You're already using that tag for another character.",
    "invalid_rp_mode": "Not a valid RP mode.",
    "invalid_gender": "Not a valid gender option.",
    "job_required": "Set a job title first.",
    # Feature flags / beta gates (`/staff feature set`)
    "feature_tab_disabled": "{tab} is currently disabled on this server.",
    "feature_tab_staff_only": "{tab} is currently restricted to staff while this is in beta.",
    "npc_interaction_disabled": "NPC roleplay interaction is currently disabled on this server.",
    "npc_interaction_staff_only": (
        "NPC roleplay interaction is currently restricted to staff while this is in beta."
    ),
    # Dashboard theme (color-wheel)
    "theme_invalid_hex_color": "That's not a valid hex color (use a format like #1a2b3c).",
    "theme_invalid_profile_name": "Give this theme a name, {max} characters or fewer.",
    "theme_profile_limit_reached": "You've saved as many custom themes as you're allowed.",
    # Market
    "market_not_at_market": "You need to be at a market location to trade.",
    "market_mode_forbidden": "You're in Story mode and have no access to the markets.",
    "market_passed_out": "You've passed out from exhaustion and can't buy or sell right now.",
    "market_good_not_traded": "That good isn't bought or sold in this district.",
    "market_stock_qty_must_be_positive": "Quantity must be a positive number.",
    "market_invalid_qty": "Quantity must be a positive number.",
    "market_insufficient_stock": "The market doesn't have that much {good} left today.",
    "market_insufficient_inventory": "You don't have that many to sell.",
    "market_insufficient_funds": "You can't afford that.",
    "market_no_goods_traded": "Nothing is bought or sold in this district yet.",
    # Poaching (outskirts hunting)
    "poach_no_outskirts": "There's nowhere to poach in this district.",
    "poach_not_at_outskirts": "You need to be at the outskirts to try poaching.",
    "poach_night_only": "You can only poach at night -- the outskirts are watched too closely by day.",
    "poach_on_cooldown": "You already tried poaching this phase of the day.",
    "poach_nothing_to_poach": "There's nothing worth poaching here.",
    "poach_jailed": "You're locked up and can't go poaching.",
    "poach_passed_out": "You've passed out from exhaustion and can't go poaching.",
    "crime_mode_forbidden": "You're in Story mode and have no access to crime.",
    "crime_disabled_by_actor": "You've disabled crime for yourself right now -- turn it back on first.",
    "victim_is_story_mode": "That character is in Story mode and can't be targeted this way.",
    "victim_crime_disabled": "That character has crime disabled and can't be targeted this way.",
    # Jail / bail / lockpicking
    "jail_not_jailed": "You're not locked up.",
    "bail_insufficient_funds": "You can't cover the {cost} money bail needs.",
    "lockpick_no_tries_left": "You're out of lockpicking attempts for this stay.",
    "work_jailed": "You're locked up and can't work a shift.",
    "work_passed_out": "You've passed out from exhaustion and can't work a shift.",
    "travel_jailed": "You're locked up and aren't going anywhere.",
    "travel_passed_out": "You've passed out from exhaustion and aren't going anywhere.",
    "steal_jailed": "You're locked up and can't try that.",
    "steal_passed_out": "You've passed out from exhaustion and can't try that.",
    "burgle_jailed": "You're locked up and can't try that.",
    "burgle_passed_out": "You've passed out from exhaustion and can't try that.",
    "shipment_jailed": "You're locked up and can't try that.",
    "shipment_passed_out": "You've passed out from exhaustion and can't try that.",
    "blackmarket_passed_out": "You've passed out from exhaustion and can't trade right now.",
    # District lore (staff-authored history/opinions panel)
    "district_lore_adjective_too_long": "That adjective is too long.",
    "district_lore_invalid_opinion_district": "Not a valid district to hold an opinion about.",
    "district_lore_opinion_too_long": "That opinion text is too long.",
    "district_lore_invalid_classification": "Not a valid classification (Inner/Outlier).",
    "district_lore_academy_name_too_long": "That academy name is too long.",
    "invalid_district_id": "Not a valid district.",
    "district_lore_invalid_person_role": "Not a valid role for a notable person.",
    "district_lore_invalid_person_name": "Give this person a name.",
    "district_lore_person_not_found": "No notable person by that name in this district.",
    # Bulletin board
    "bulletin_title_required": "A bulletin post needs a title.",
    "bulletin_image_url_invalid": "That doesn't look like a valid image link (must start with http:// or https://).",
    "bulletin_entry_not_found": "No bulletin entry with that id.",
    "bulletin_not_yours": "You can only retract your own posts.",
    "bulletin_reminder_needs_time": "That entry has no scheduled time to remind you ahead of.",
    # Stealing / burglary
    "steal_target_not_found": "No one by that name is here to steal from.",
    "steal_not_here": "You need to be at the same location as your target to try this.",
    "steal_on_cooldown": "You already tried to steal something this phase of the day.",
    "burgle_mode_forbidden": (
        "You're in Life mode, which has no housing access -- burglary isn't available."
    ),
    "burgle_owner_not_found": "No character by that name owns a house here.",
    "burgle_not_a_house": "That property isn't a house.",
    "burgle_wrong_district": "You need to be in the house's district to break in.",
    "burgle_own_house": "You can't burgle your own house.",
    "burgle_owner_home": "The owner is home right now -- you can't break in undetected.",
    # Avatar layers / character appearance
    "layer_category_not_found": "No such appearance category.",
    "invalid_layer_image": "That image isn't valid for this layer.",
    "layer_image_too_large": "That image is too large.",
    "layer_option_not_found": "No such appearance option.",
    "invalid_appearance_layers": "That appearance combination isn't valid.",
    "invalid_avatar_image": "That doesn't look like a valid image.",
    "avatar_image_too_large": "That image is too large.",
    # Ambient music (Staff tab)
    "ambient_track_not_found": "No ambient track with that id.",
    "ambient_title_invalid": "Give this track a title, 1-80 characters.",
    "ambient_scope_invalid": "Not a valid scope (global/location/channel/district).",
    "ambient_file_too_large": "That audio file is too large.",
    # Rumors
    "rumor_investigate_capped": (
        "This rumor's already been looked into enough -- the findings so far stand."
    ),
    "rumor_not_found": "No rumor with that id.",
    # Sustenance (eat/drink/heal/energize)
    "sustenance_mode_forbidden": "You don't need to eat, drink, or entertain yourself in your current RP mode.",
    "good_not_edible": "That isn't something you can eat.",
    "good_not_drinkable": "That isn't something you can drink.",
    "good_not_healing": "That isn't something you can use to heal.",
    "good_not_energizing": "That isn't something you can use to perk up.",
    "sustenance_no_inventory": "You don't have any of that to consume.",
    "unknown_game": "That's not one of the Vitals tab's entertainment games.",
    # Personal event chains ("mystery" investigations)
    "chain_already_investigated": "You've already looked into this one.",
    "chain_no_candidates": "No one around seems like a likely owner right now -- try again later.",
    "chain_not_investigated": "Investigate it first.",
    "chain_target_gone": "Whoever it belonged to isn't around to ask anymore.",
    "chain_wrong_district": "The owner lives in a different district -- travel there first.",
    "chain_none_active": "You're not in the middle of anything like that right now.",
    # Travel/locations
    "location_restricted": "You don't have access to that location.",
    "outskirts_night_only": "You can't reach the outskirts except at night.",
    "location_not_found": "Not a valid location for that district.",
    "travel_already_in_transit": "You're already on a train -- check your status first.",
    "travel_same_district": "You're already there.",
    "travel_not_at_station": "You need to be at the station to catch a train.",
    "travel_insufficient_transport": (
        "You don't have enough transport banked for the trip -- buy some at a district market first."
    ),
    # Weather prep/repair
    "weather_prep_invalid_kind": "Not a weather kind that can be prepared for.",
    "weather_prep_maxed": "Already as prepared as possible for that.",
    "weather_prep_insufficient_materials": "You don't have the goods or money on hand for that.",
    "weather_repair_nothing": "No damage to repair.",
    "weather_repair_insufficient_money": "You can't afford to repair that damage.",
    "weather_no_property": "You don't own or lease a property to prepare or repair.",
    # Shipment heists
    "shipment_none_here": "There's no shipment sitting here right now.",
    "shipment_not_here": "You need to be at the shipment's location to try this.",
    "shipment_gone": "The peacekeepers already cleared that shipment out.",
    # Black market
    "blackmarket_no_fence": "This district has no black market contact.",
    "blackmarket_not_at_market": "You need to be at the district's outskirts to trade on the black market.",
    "blackmarket_night_only": "You can only reach the black market at night.",
    "blackmarket_not_trusted": "You aren't on good enough terms with the contact to be shown the black market.",
    "blackmarket_good_not_traded": "That good isn't traded on this district's black market.",
    "blackmarket_insufficient_stock": "The black market doesn't have that much {good} left today.",
    # Reaction events
    "reaction_already_answered": "You've already decided on that.",
    "reaction_none_pending": "Nothing like that is happening for you right now.",
    # Housing
    "housing_mode_forbidden": "You don't have access to the housing system in your current RP mode.",
    "housing_not_found": "No property by that id.",
    "housing_not_for_sale": "That property isn't for sale.",
    "housing_already_owned": "Someone already owns that property.",
    "housing_wrong_district": "You can only buy a house in your own district.",
    "housing_tier_too_low": "You aren't a high enough job level yet for that house.",
    "housing_insufficient_funds": "You can't afford that.",
    "housing_not_an_apartment": "That property isn't an apartment unit.",
    "housing_unit_already_leased": "That unit is already leased.",
    "housing_complex_not_found": "No apartment complex by that id.",
    "housing_complex_partially_owned": "Someone already owns part of that complex.",
    "housing_no_home": "You don't have a fixed home right now.",
    "housing_not_a_tenant": "You own your home outright -- there's no lease to end.",
    "housing_not_an_inn": "That property isn't an inn.",
    "housing_not_your_property": "You don't own that property.",
    "housing_down_payment_too_much": "You can't afford the down payment.",
    "housing_no_mortgage": "That property doesn't have a mortgage or maintenance balance.",
    "housing_refinance_too_much": "You can't borrow that much against that property.",
    "housing_auction_already_open": "That property already has an open auction.",
    "housing_auction_not_found": "No open auction for that property.",
    "housing_bid_too_low": "That bid isn't higher than the current one.",
    # Trades (player-to-player)
    "trade_mode_forbidden": "You're in Story mode and can't trade this way.",
    "trade_cannot_self": "You can't trade with yourself.",
    "trade_negative_money": "Money offered can't be negative.",
    "trade_good_qty_mismatch": "A good and a quantity must be given together, or not at all.",
    "trade_invalid_qty": "Quantity must be a positive number.",
    "trade_empty_offer": "Offer at least some money or a good on each side.",
    "trade_insufficient_money": "You don't have enough money to cover this trade.",
    "trade_insufficient_inventory": "You don't have enough of that good to cover this trade.",
    "trade_not_pending": "This trade offer isn't pending anymore.",
    "trade_not_found": "No trade offer by that id.",
    "trade_not_yours_to_cancel": "That isn't your trade offer to cancel.",
    # RP mode switching / crime toggle
    "mode_switch_already_pending": "You already have a mode switch awaiting staff approval.",
    "mode_already_active": "This character is already in that mode.",
    "mode_switch_on_cooldown": "You switched modes too recently -- try again later.",
    "crime_toggle_wrong_mode": "Only Life-mode characters can toggle crime on or off for themselves.",
    "crime_toggle_already_set": "Your crime setting is already set to that.",
    "crime_toggle_on_cooldown": "You already changed your crime setting today -- try again later.",
    # Pay (player-to-player money)
    "pay_mode_forbidden": "You're in Story mode and can't send or receive money this way.",
    "pay_cannot_self": "You can't pay yourself.",
    "pay_invalid_amount": "Enter a positive amount to pay.",
    "pay_insufficient_money": "You don't have that much money.",
    # Pets
    "pets_name_required": "Give your pet a name.",
    "pets_name_too_long": "That pet name is too long ({max_length} characters max).",
    "pets_insufficient_funds": "You can't afford that.",
    # Affliction types (Staff tab catalog)
    "invalid_affliction_type_name": "Give this affliction a name.",
    "invalid_affliction_type_description": "Give this affliction a description.",
    "affliction_type_name_taken": "An affliction type with that name already exists.",
    "permanent_affliction_cannot_have_cure": "A permanent affliction can't have a cure.",
    "affliction_cure_fields_incomplete": "Fill in every field needed to define a cure.",
    "affliction_auto_apply_fields_incomplete": "Fill in every field needed for auto-apply.",
    "affliction_type_not_found": "No affliction type by that name.",
    # Panem-wide history/lore
    "panem_history_needs_a_keyword": "Give at least one keyword, comma-separated.",
    "panem_history_text_required": "The history fact can't be empty.",
    "panem_history_text_too_long": "That history fact is too long.",
    "panem_history_au_notes_too_long": "Those notes are too long.",
    "panem_history_entry_not_found": "No history entry with that id.",
    # Hobbies
    "hobby_not_found": "That's not a hobby.",
    "hobby_already_selected": "You already do that hobby.",
    "hobby_limit_reached": "You can only have {limit} hobbies at once -- drop one first.",
    "hobby_not_selected": "You haven't picked that hobby.",
    "hobby_on_cooldown": "Still catching your breath -- try again in a bit.",
    # Global events
    "global_event_not_found": "No event with that name or id.",
    "global_event_bad_window": "The event must end after it starts.",
    "global_event_not_active": "That event isn't running right now.",
    "global_event_already_over": "That event has already finished or been cancelled.",
    # Self-inflicted afflictions/death
    "affliction_wrong_mode": (
        "Only Life-mode characters can self-inflict an affliction -- Simulation mode handles "
        "this automatically, and Story mode is excluded from the system entirely."
    ),
    "affliction_cause_required": "Say how it happened.",
    "death_wrong_mode": (
        "Only Life-mode characters can end their own character this way -- Simulation mode "
        "handles death automatically, and Story mode is excluded from the system entirely."
    ),
    "character_already_dead": "That character is already dead.",
    # Museum
    "museum_item_not_found": "That's not one of your collectibles.",
    "museum_slot_not_found": "That museum piece couldn't be found.",
    "museum_unknown_preset": "Not a valid layout preset.",
    # Generic dashboard/staff plumbing
    "tab_disabled": "This tab is currently disabled.",
    "donor_only": "This feature is for donors only.",
    "staff_only": "Staff only.",
    "invalid_ticks": "Ticks must be a positive number.",
    "staff_good_not_found": "Not a valid good id.",
    "roleplay_role_not_configured": "No roleplay role is configured for this server yet.",
    "channel_not_found": "No such channel.",
    "discord_permission_edit_failed": "Discord refused that permission change -- check the bot's role position/permissions.",
    "invalid_motto_length": "That motto is too long.",
    "job_fields_required": "A job title and shift are both required.",
    "invalid_limit": "That limit isn't valid.",
    "invalid_minutes": "Minutes must be a positive number.",
    "district_state_not_seeded": "This district's world state hasn't been seeded yet.",
    "staff_npc_not_found": (
        "No resident found -- if two residents share a name, pick one from the suggestions to "
        "tell them apart."
    ),
    "npc_needs_trait": "Name at least one trait.",
    "invalid_location": "Not a valid location.",
    "invalid_job": "Not a valid job for that district.",
    "invalid_key": "Not a valid key.",
    "invalid_state": "Not a valid state.",
    "resident_not_found": "No resident by that name in this district.",
    "job_none_set": "You don't have a job set -- ask staff to set one for you.",
    "job_no_open_shift": "It isn't your shift right now.",
    "job_wrong_district": (
        "You're not in your home district right now and can't work this shift -- come back once "
        "you've returned home."
    ),
}


def error_message(reason_key: str, **fmt: object) -> str:
    """The dashboard's plain-English counterpart to `panem_bot.strings.t`
    -- looks `reason_key` up in `ERROR_MESSAGES` and formats it against
    whatever `fmt` the raising `ServiceError` carried. A key this dict
    doesn't cover yet (should be rare -- every situation a player can
    actually hit is meant to be listed above) falls back to a cheap
    underscore-stripped version of the key itself, same readability pass
    `static/tabs/_shared.js`'s `humanize()` already does client-side, so
    nothing ever regresses to a worse-than-before raw code. A template
    referencing an `fmt` key the caller didn't actually pass degrades to
    the unformatted template rather than raising -- still a real
    sentence, just missing one number/name."""
    template = ERROR_MESSAGES.get(reason_key)
    if template is None:
        return _humanize(reason_key)
    try:
        return template.format(**fmt)
    except (KeyError, IndexError):
        return template


def _humanize(reason_key: str) -> str:
    text = reason_key.replace("_", " ").strip()
    if not text:
        return text
    capitalized = text[0].upper() + text[1:]
    return capitalized if capitalized.endswith((".", "!", "?")) else f"{capitalized}."
