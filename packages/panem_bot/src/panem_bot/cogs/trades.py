"""`/pay` (instant one-way money transfer) and `/trade` (a two-party
accept/decline exchange of one item-or-money offer per side) -- the
player-to-player economy primitives Life/Simulation characters need that
didn't exist before this feature (`/steal`/`/burgle` are the only
existing character-to-character money movement, and they're
non-consensual). Story-mode characters are refused by both --
`panem_shared.pay`/`panem_shared.trades` already carry that gate.
"""

from __future__ import annotations

import contextlib
import datetime as dt
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panem_bot import autocomplete, utils
from panem_bot.errors import ServiceError
from panem_bot.services import characters as characters_svc
from panem_bot.services import pay as pay_svc
from panem_bot.services import trades as trades_svc
from panem_bot.strings import t
from panem_shared import constants
from panem_shared.db.models import Character, Trade, User
from panem_shared.enums import TradeStatus


class _TradeResponseButton(discord.ui.Button["discord.ui.View"]):
    """Same restricted-callback shape as `engagements.py`'s
    `_InviteResponseButton` -- kept as its own small copy here rather than
    imported across cogs, the same "small per-module duplication" choice
    `blackmarket.py`/`trades.py` (the service module) already made for
    `_adjust_inventory`."""

    def __init__(
        self,
        *,
        label: str,
        style: discord.ButtonStyle,
        target_discord_id: int,
        on_click: Callable[[discord.Interaction], Awaitable[None]],
    ) -> None:
        super().__init__(label=label, style=style)
        self._target_discord_id = target_discord_id
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._target_discord_id:
            await interaction.response.send_message("That offer isn't yours.", ephemeral=True)
            return
        await self._on_click(interaction)


class TradeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.expire_stale_trades.start()

    async def cog_unload(self) -> None:
        self.expire_stale_trades.cancel()

    def _describe_side(self, good_id: str | None, qty: int | None, money: int) -> str:
        content = self.bot.content  # type: ignore[attr-defined]
        parts = []
        if good_id is not None and qty is not None:
            good = content.goods.get(good_id)
            parts.append(f"{qty} {good.name if good else good_id}")
        if money:
            parts.append(f"{money} money")
        return " + ".join(parts) if parts else "nothing"

    # ----------------------------------------------------------------- /pay

    @app_commands.command(name="pay", description="Pay another player's character money")
    @app_commands.describe(character="Your character", target="Who to pay", amount="How much money")
    @app_commands.autocomplete(
        character=autocomplete.own_approved, target=autocomplete.any_approved
    )
    async def pay(
        self, interaction: discord.Interaction, character: str, target: str, amount: int
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            sender = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if sender is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            recipient = (
                await session.execute(select(Character).where(Character.name == target))
            ).scalar_one_or_none()
            if recipient is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            try:
                pay_svc.pay(sender, recipient, amount)
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            sender_name, recipient_name = sender.name, recipient.name
        await interaction.response.send_message(
            t("pay_ok", sender=sender_name, recipient=recipient_name, amount=amount),
            ephemeral=True,
        )

    # --------------------------------------------------------------- /trade

    group = app_commands.Group(name="trade", description="Offer, accept, or cancel player trades")

    def _response_view(self, *, trade_id: int, target_discord_id: int) -> discord.ui.View:
        async def _respond(interaction: discord.Interaction, *, accepted: bool) -> None:
            async with self.bot.db() as session:  # type: ignore[attr-defined]
                trade = await session.get(Trade, trade_id)
                if trade is None:
                    await interaction.response.edit_message(content=t("trade_not_found"), view=None)
                    return
                initiator = await session.get(Character, trade.initiator_character_id)
                recipient = await session.get(Character, trade.recipient_character_id)
                if initiator is None or recipient is None:
                    await interaction.response.edit_message(
                        content=t("character_not_found"), view=None
                    )
                    return
                try:
                    if accepted:
                        await trades_svc.accept_trade(
                            session,
                            trade=trade,
                            initiator=initiator,
                            recipient=recipient,
                            now=dt.datetime.now(dt.UTC),
                        )
                    else:
                        trades_svc.decline_trade(trade, dt.datetime.now(dt.UTC))
                except ServiceError as exc:
                    await interaction.response.edit_message(
                        content=t(exc.reason_key, **exc.fmt), view=None
                    )
                    return
            key = "trade_accepted" if accepted else "trade_declined"
            await interaction.response.edit_message(content=t(key), view=None)

        view = discord.ui.View(timeout=None)
        view.add_item(
            _TradeResponseButton(
                label="Accept",
                style=discord.ButtonStyle.success,
                target_discord_id=target_discord_id,
                on_click=lambda i: _respond(i, accepted=True),
            )
        )
        view.add_item(
            _TradeResponseButton(
                label="Decline",
                style=discord.ButtonStyle.danger,
                target_discord_id=target_discord_id,
                on_click=lambda i: _respond(i, accepted=False),
            )
        )
        return view

    @group.command(name="offer", description="Offer a trade to another player's character")
    @app_commands.describe(
        character="Your character",
        target="The character you're offering to",
        give_good="A good you're offering (optional)",
        give_qty="How much of that good (required together with give_good)",
        give_money="Money you're offering (optional)",
        want_good="A good you want in return (optional)",
        want_qty="How much of that good (required together with want_good)",
        want_money="Money you want in return (optional)",
    )
    @app_commands.autocomplete(
        character=autocomplete.own_approved,
        target=autocomplete.any_approved,
        give_good=autocomplete.any_good,
        want_good=autocomplete.any_good,
    )
    async def offer(
        self,
        interaction: discord.Interaction,
        character: str,
        target: str,
        give_good: str | None = None,
        give_qty: int | None = None,
        give_money: int = 0,
        want_good: str | None = None,
        want_qty: int | None = None,
        want_money: int = 0,
    ) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            initiator = await utils.get_character_case_insensitive(session, interaction.user.id, character)
            if initiator is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            recipient = (
                await session.execute(select(Character).where(Character.name == target))
            ).scalar_one_or_none()
            if recipient is None:
                await interaction.response.send_message(t("character_not_found"), ephemeral=True)
                return
            try:
                trades_svc.check_can_trade(initiator, recipient)
                trades_svc.validate_offer(
                    initiator=initiator,
                    give_good_id=give_good,
                    give_qty=give_qty,
                    give_money=give_money,
                    want_good_id=want_good,
                    want_qty=want_qty,
                    want_money=want_money,
                )
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
            trade = Trade(
                initiator_character_id=initiator.id,
                recipient_character_id=recipient.id,
                give_good_id=give_good,
                give_qty=give_qty,
                give_money=give_money,
                want_good_id=want_good,
                want_qty=want_qty,
                want_money=want_money,
                status=TradeStatus.PENDING.value,
            )
            session.add(trade)
            await session.flush()
            trade_id, initiator_name, recipient_name = trade.id, initiator.name, recipient.name
            give_desc = self._describe_side(give_good, give_qty, give_money)
            want_desc = self._describe_side(want_good, want_qty, want_money)
            recipient_user = await session.get(User, recipient.user_id)
            recipient_discord_id = recipient_user.discord_id if recipient_user is not None else None

        await interaction.response.send_message(
            t("trade_offer_sent", name=recipient_name), ephemeral=True
        )

        guild = interaction.guild
        if guild is None or recipient_discord_id is None:
            return
        member = guild.get_member(recipient_discord_id)
        if member is None:
            return
        with contextlib.suppress(discord.Forbidden):
            await member.send(
                f"**{initiator_name}** offers **{recipient_name}** a trade "
                f"(`#{trade_id}`):\nThey give: {give_desc}\nThey want: {want_desc}",
                view=self._response_view(trade_id=trade_id, target_discord_id=recipient_discord_id),
            )

    @group.command(name="cancel", description="Cancel a pending trade offer you sent")
    @app_commands.describe(trade_id="The trade's id (shown when you sent the offer)")
    async def cancel(self, interaction: discord.Interaction, trade_id: int) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            trade = await session.get(Trade, trade_id)
            if trade is None:
                await interaction.response.send_message(t("trade_not_found"), ephemeral=True)
                return
            initiator = await session.get(Character, trade.initiator_character_id)
            user = await characters_svc.get_or_create_user(session, interaction.user.id)
            if initiator is None or initiator.user_id != user.id:
                await interaction.response.send_message(
                    t("trade_not_yours_to_cancel"), ephemeral=True
                )
                return
            try:
                trades_svc.cancel_trade(trade, dt.datetime.now(dt.UTC))
            except ServiceError as exc:
                await interaction.response.send_message(
                    t(exc.reason_key, **exc.fmt), ephemeral=True
                )
                return
        await interaction.response.send_message(t("trade_cancelled"), ephemeral=True)

    # --------------------------------------------------------------- upkeep

    @tasks.loop(minutes=constants.TRADE_EXPIRY_CHECK_INTERVAL_MINUTES)
    async def expire_stale_trades(self) -> None:
        async with self.bot.db() as session:  # type: ignore[attr-defined]
            cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(
                minutes=constants.TRADE_OFFER_EXPIRY_MINUTES
            )
            stale = (
                await session.execute(
                    select(Trade).where(
                        Trade.status == TradeStatus.PENDING.value, Trade.created_at < cutoff
                    )
                )
            ).scalars()
            for trade in stale:
                trades_svc.expire_trade(trade, dt.datetime.now(dt.UTC))

    @expire_stale_trades.before_loop
    async def _before_expire_stale_trades(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TradeCog(bot))
