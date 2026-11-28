"""Shared "is this meter now in trouble" math for the Vitals widget/
low-vitals-DM feature. Lives in `panem_shared`, not `panem_sim` or
`panem_bot`, because both ends of the two places a vital actually gets
worse need the exact same threshold/inversion handling:

- `panem_sim.systems.needs` -- hunger/thirst/health/sanity's primary
  decay (per-phase and nightly).
- `panem_shared.housing.dock_fatigue` -- fatigue's *only* decrease path
  (RP-interaction credit, `panem_bot.cogs.proxy._apply_rp_credit`);
  nothing in `panem_sim.systems.needs` ever lowers fatigue, only
  restores it, so a fatigue crossing can never be detected there.

Nothing here publishes an event or sends a DM itself -- each caller
decides what "a crossing happened" means for it (`panem_sim` appends a
`VitalsLow` world event; `dock_fatigue` just returns the crossing for
`panem_bot` to DM about directly, since it already has live Discord
access and `panem_shared` never does).
"""

from __future__ import annotations

from typing import Literal

from panem_shared import constants

INVERTED_VITALS = {"hunger", "thirst"}
"""`health`/`fatigue`/`sanity` already store 100=best/0=worst; `hunger`/
`thirst` store the opposite (100=worst). `percent_remaining` normalizes
away that difference so one pair of thresholds covers all five."""

Severity = Literal["warn", "danger"]


def percent_remaining(vital: str, value: float) -> float:
    return 100.0 - value if vital in INVERTED_VITALS else value


def crossing_severity(vital: str, old_value: float, new_value: float) -> Severity | None:
    """`None` unless `vital` just moved from at-or-above a threshold to
    strictly below it between `old_value` and `new_value` -- merely
    *being* below (no change, or already below both before and after)
    never reports a crossing, which is what keeps this firing once per
    crossing rather than once per tick/interaction while a meter stays
    low. A drop past both thresholds in one step reports only `"danger"`,
    the more urgent of the two."""
    old_pct = percent_remaining(vital, old_value)
    new_pct = percent_remaining(vital, new_value)
    if new_pct >= old_pct:
        return None
    if old_pct > constants.VITALS_DANGER_THRESHOLD_PERCENT >= new_pct:
        return "danger"
    if old_pct > constants.VITALS_WARN_THRESHOLD_PERCENT >= new_pct:
        return "warn"
    return None
