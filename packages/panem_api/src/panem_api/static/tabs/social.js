// The "Social" tab: `/resident list|where|profile` (Residents), a
// character's current `/talk`/`/engage`/`/scene` thread if any
// (Engagement), and the player-to-player economy primitives (Pay, Trade).
//
// The Engagement panel is deliberately read-only + a "Continue in
// Discord" deep link, not a full chat UI -- per the user's own choice
// when this feature was scoped: making it fully interactive here would
// need a dashboard -> Redis -> panem_bot relay (only the bot process
// holds a token and can post/create Discord threads), which is out of
// scope for this pass.
//
// The Trade panel has no push channel of its own the way the bot's DM +
// Accept/Decline view does -- a recipient discovers a pending offer by
// reopening this tab (or switching characters, which remounts it), same
// "poll, don't push" posture the Engagement panel already has.
import { fetchJson, el } from "./_shared.js?v=3";

function engagementPanel(ctx) {
  const statusEl = el("p", { class: "tab-status" });
  const detailEl = el("div", {});
  const panel = el("div", { class: "panel" }, el("h2", { text: "Current Engagement" }), statusEl, detailEl);

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    detailEl.innerHTML = "";
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      return;
    }
    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/social/${characterId}?discord_id=${encodeURIComponent(discordId)}`
      );
      if (!status.in_scene) {
        statusEl.textContent = "Not currently in a scene or engagement.";
        return;
      }
      statusEl.textContent = "";
      const lines = [
        el("p", {}, el("strong", { text: status.scene_title })),
        el("p", { class: "tab-status" }, `Kind: ${status.scene_kind}`),
      ];
      if (status.location_name) {
        lines.push(el("p", { class: "tab-status" }, `Location: ${status.location_name}`));
      }
      if (status.participant_character_names.length > 0) {
        lines.push(
          el(
            "p",
            { class: "tab-status" },
            `Characters: ${status.participant_character_names.join(", ")}`
          )
        );
      }
      if (status.participant_npc_names.length > 0) {
        lines.push(
          el("p", { class: "tab-status" }, `Residents: ${status.participant_npc_names.join(", ")}`)
        );
      }
      if (status.discord_thread_url) {
        lines.push(
          el(
            "a",
            { class: "btn", href: status.discord_thread_url, target: "_blank", rel: "noopener" },
            "Continue in Discord"
          )
        );
      }
      detailEl.append(...lines);
    } catch (err) {
      statusEl.textContent = `Could not load engagement status: ${err.message}`;
    }
  }

  refresh();
  return panel;
}

function residentsPanel(ctx) {
  const statusEl = el("p", { class: "tab-status" });
  const listHost = el("div", {});
  const profileHost = el("div", {});
  const panel = el(
    "div",
    { class: "panel" },
    el("h2", { text: "Residents" }),
    statusEl,
    listHost,
    profileHost
  );

  async function showProfile(name) {
    profileHost.innerHTML = "";
    try {
      const profile = await ctx.apiFetch(
        `/activity/dashboard/residents/${ctx.characterId()}/${encodeURIComponent(name)}` +
          `?discord_id=${encodeURIComponent(ctx.discordId())}`
      );
      const lines = [
        el("h3", { text: profile.name }),
        el("p", { class: "tab-status" }, `${profile.job_title}`),
      ];
      if (profile.location_name) {
        lines.push(el("p", { class: "tab-status" }, `At: ${profile.location_name}`));
      }
      lines.push(
        el("p", { class: "tab-status" }, `Traits: ${profile.traits.join(", ") || "unknown"}`)
      );
      lines.push(el("p", { class: "tab-status" }, `Speech: ${profile.tone}`));
      lines.push(el("p", { class: "tab-status" }, `Opinion of you: ${profile.stance}`));
      if (profile.appearance) lines.push(el("p", {}, profile.appearance));
      if (profile.backstory) lines.push(el("p", {}, profile.backstory));
      profileHost.append(...lines);
    } catch (err) {
      profileHost.append(el("p", { class: "result-line lose" }, err.message));
    }
  }

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    listHost.innerHTML = "";
    profileHost.innerHTML = "";
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      return;
    }
    try {
      const data = await ctx.apiFetch(
        `/activity/dashboard/residents/${characterId}?discord_id=${encodeURIComponent(discordId)}`
      );
      statusEl.textContent = `Residents of ${data.district_name}:`;
      if (data.residents.length === 0) {
        listHost.append(el("p", { class: "tab-status" }, "Nobody lives here."));
        return;
      }
      const table = el(
        "table",
        { class: "data-table" },
        el(
          "thead",
          {},
          el("tr", {}, el("th", { text: "Name" }), el("th", { text: "Job" }), el("th", { text: "Where" }))
        )
      );
      const tbody = el("tbody", {});
      for (const r of data.residents) {
        const row = el(
          "tr",
          { class: "clickable-row", onClick: () => showProfile(r.name) },
          el("td", { text: r.name }),
          el("td", { text: r.job_title }),
          el("td", { text: r.location_name || "unknown" })
        );
        tbody.append(row);
      }
      table.append(tbody);
      listHost.append(table);
    } catch (err) {
      statusEl.textContent = `Could not load residents: ${err.message}`;
    }
  }

  refresh();
  return panel;
}

function payPanel(ctx) {
  const targetInput = el("input", { type: "text", placeholder: "Character name" });
  const amountInput = el("input", { type: "number", placeholder: "Amount", min: "1" });
  const resultLine = el("p", { class: "result-line" });
  const payBtn = el("button", { class: "btn", type: "button" }, "Pay");

  payBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    const target = targetInput.value.trim();
    const amount = Number(amountInput.value);
    if (!target || !Number.isFinite(amount) || amount <= 0) {
      resultLine.className = "result-line lose";
      resultLine.textContent = "Name who to pay and a positive amount.";
      return;
    }
    try {
      const body = await ctx.apiFetch(`/activity/dashboard/pay/${ctx.characterId()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), target, amount }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = `Paid ${amount} to ${body.recipient_name} (${body.sender_money} left).`;
      targetInput.value = "";
      amountInput.value = "";
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Pay" }),
    el("div", { class: "field-row" }, el("label", { text: "To" }), targetInput),
    el("div", { class: "field-row" }, el("label", { text: "Amount" }), amountInput),
    el("div", { class: "field-row" }, payBtn),
    resultLine
  );
}

function tradeOfferSummary(trade) {
  const describe = (goodId, qty, money) => {
    const parts = [];
    if (goodId && qty) parts.push(`${qty} ${goodId}`);
    if (money) parts.push(`${money} money`);
    return parts.length > 0 ? parts.join(" + ") : "nothing";
  };
  const give = describe(trade.give_good_id, trade.give_qty, trade.give_money);
  const want = describe(trade.want_good_id, trade.want_qty, trade.want_money);
  if (trade.direction === "incoming") {
    return `${trade.initiator_name} offers you ${give} for ${want}.`;
  }
  return `You offered ${trade.recipient_name} ${give} for ${want}.`;
}

function tradeOfferRow(ctx, trade, { onChanged }) {
  const resultLine = el("p", { class: "result-line" });

  async function act(action) {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch(
        `/activity/dashboard/trade/${ctx.characterId()}/${trade.id}/${action}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId() }),
        }
      );
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  }

  const buttons = [];
  if (trade.direction === "incoming") {
    const acceptBtn = el("button", { class: "btn", type: "button" }, "Accept");
    acceptBtn.addEventListener("click", () => act("accept"));
    const declineBtn = el("button", { class: "btn secondary", type: "button" }, "Decline");
    declineBtn.addEventListener("click", () => act("decline"));
    buttons.push(acceptBtn, declineBtn);
  } else {
    const cancelBtn = el("button", { class: "btn secondary", type: "button" }, "Cancel");
    cancelBtn.addEventListener("click", () => act("cancel"));
    buttons.push(cancelBtn);
  }

  return el(
    "div",
    { class: "panel" },
    el("p", {}, tradeOfferSummary(trade)),
    el("div", { class: "field-row" }, ...buttons),
    resultLine
  );
}

function tradePanel(ctx) {
  const statusEl = el("p", { class: "tab-status" });
  const listEl = el("div", {});

  const targetInput = el("input", { type: "text", placeholder: "Character name" });
  const giveGoodInput = el("input", { type: "text", placeholder: "Good id" });
  const giveQtyInput = el("input", { type: "number", placeholder: "Qty", min: "1" });
  const giveMoneyInput = el("input", { type: "number", placeholder: "0", value: "0" });
  const wantGoodInput = el("input", { type: "text", placeholder: "Good id" });
  const wantQtyInput = el("input", { type: "number", placeholder: "Qty", min: "1" });
  const wantMoneyInput = el("input", { type: "number", placeholder: "0", value: "0" });
  const offerResult = el("p", { class: "result-line" });
  const offerBtn = el("button", { class: "btn", type: "button" }, "Send offer");

  offerBtn.addEventListener("click", async () => {
    offerResult.textContent = "";
    const target = targetInput.value.trim();
    if (!target) {
      offerResult.className = "result-line lose";
      offerResult.textContent = "Name who you're offering to.";
      return;
    }
    const giveGood = giveGoodInput.value.trim() || null;
    const wantGood = wantGoodInput.value.trim() || null;
    try {
      await ctx.apiFetch(`/activity/dashboard/trade/${ctx.characterId()}/offer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: ctx.discordId(),
          target,
          give_good_id: giveGood,
          give_qty: giveGood ? Number(giveQtyInput.value) || null : null,
          give_money: Number(giveMoneyInput.value) || 0,
          want_good_id: wantGood,
          want_qty: wantGood ? Number(wantQtyInput.value) || null : null,
          want_money: Number(wantMoneyInput.value) || 0,
        }),
      });
      offerResult.className = "result-line win";
      offerResult.textContent = "Offer sent.";
      targetInput.value = "";
      giveGoodInput.value = "";
      giveQtyInput.value = "";
      giveMoneyInput.value = "0";
      wantGoodInput.value = "";
      wantQtyInput.value = "";
      wantMoneyInput.value = "0";
      await refresh();
    } catch (err) {
      offerResult.className = "result-line lose";
      offerResult.textContent = err.message;
    }
  });

  async function refresh() {
    listEl.innerHTML = "";
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      return;
    }
    try {
      const body = await ctx.apiFetch(
        `/activity/dashboard/trade/${characterId}/list?discord_id=${encodeURIComponent(discordId)}`
      );
      statusEl.textContent = "";
      if (body.trades.length === 0) {
        listEl.append(el("p", { class: "tab-status" }, "No pending trade offers."));
        return;
      }
      for (const trade of body.trades) {
        listEl.append(tradeOfferRow(ctx, trade, { onChanged: refresh }));
      }
    } catch (err) {
      statusEl.textContent = `Could not load trades: ${err.message}`;
    }
  }

  refresh();

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Trade" }),
    statusEl,
    listEl,
    el("h3", { text: "Offer a trade" }),
    el("div", { class: "field-row" }, el("label", { text: "To" }), targetInput),
    el("div", { class: "field-row" }, el("label", { text: "You give: good" }), giveGoodInput),
    el("div", { class: "field-row" }, el("label", { text: "Qty" }), giveQtyInput),
    el("div", { class: "field-row" }, el("label", { text: "+ money" }), giveMoneyInput),
    el("div", { class: "field-row" }, el("label", { text: "You want: good" }), wantGoodInput),
    el("div", { class: "field-row" }, el("label", { text: "Qty" }), wantQtyInput),
    el("div", { class: "field-row" }, el("label", { text: "+ money" }), wantMoneyInput),
    el("div", { class: "field-row" }, offerBtn),
    offerResult
  );
}

export function mount(root, ctx) {
  root.append(engagementPanel(ctx), residentsPanel(ctx), payPanel(ctx), tradePanel(ctx));
  return {};
}
