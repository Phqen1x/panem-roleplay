"""Pure(-ish) service functions: DB reads/writes with no `discord.py` imports.

Kept separate from `panem_bot.cogs` so the rules in Spec §3.1-§3.4 (FR-CHR,
FR-PRX, FR-SCN, FR-LOC) are unit-testable without a live gateway connection
or mocked Discord objects.
"""
