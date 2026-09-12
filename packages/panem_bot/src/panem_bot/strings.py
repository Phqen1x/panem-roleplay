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
    # Scenes (FR-SCN)
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
    # Staff job editing (/staff job ...)
    "job_bad_json": "Not valid JSON: {detail}",
    "job_schema_invalid": "Doesn't match the job schema: {detail}",
    "job_bad_district": "{detail}",
    "job_bad_workplace": "{detail}",
    "job_not_found": "No job with that id (checked jobs.yaml and staff overrides).",
    "job_missing_fields": "{detail}",
    "job_set_ok": "Job **{job_id}** saved for district {district_id}.",
    "job_option_ok": "Job **{job_id}** option {slot} saved.",
    "job_removed_ok": "Job **{job_id}** removed.",
    # Jobs and shifts (FR-JOB)
    "already_employed": "That character already has a job — quit it first with `/job quit`.",
    "not_employed": "That character doesn't have a job to quit.",
    "reputation_too_low": "Reputation too low for that job (needs {min_reputation}).",
    "job_applied": "**{name}** is now working as **{title}**.",
    "job_quit": "**{name}** has left their job.",
    "job_no_open_shift": "**{name}** doesn't have a shift open right now.",
    "shift_no_longer_open": "That shift is no longer open.",
    "work_ok": "**{name}** works: +{wage} money, {rep_delta:+d} reputation.",
    "promotion_available": " **{name}** is eligible for a promotion to **{next_title}** -- ask staff.",
    "no_jobs_in_district": "No jobs are defined for that district yet.",
    # Residents (FR-NPC)
    "no_residents_in_district": "No residents are seeded for that district yet.",
    "resident_not_found": "No resident by that name in this district.",
    "resident_where_ok": "**{name}** ({job}) is currently at **{location}**.",
    # Tesserae (FR-ECO-7)
    "tesserae_claimed": "**{name}** claims tesserae: +{amount} money, {count} claimed total.",
    "tesserae_already_claimed": "**{name}** already claimed tesserae today.",
    "tesserae_not_eligible": "Only District characters can claim tesserae.",
    # Generic
    "world_paused": "The world is paused.",
    "unexpected_error": "Something went wrong (ref `{ref}`).",
    "staff_only": "Staff only.",
}


def t(key: str, **fmt: object) -> str:
    return STRINGS[key].format(**fmt)
