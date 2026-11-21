// The "Events" tab -- your personal mystery chain (currently just the
// "lost item" chain a hobby can turn up) and the standings of any
// district/world-wide global event currently scheduled or running
// (mirrors `/event mystery|deliver|status|info|leaderboard`,
// `panem_shared.personal_events`/`panem_shared.global_events`). Starting
// a chain and joining a global event both stay side effects of playing a
// hobby (the Hobbies tab) -- there's no "start"/"join" button here, same
// as there's no such Discord command either.
import { el, setStatusText } from "./_shared.js?v=9";

function mysteryPanel(ctx, chain, resultLine, onChanged) {
  if (!chain) {
    return el(
      "div",
      { class: "panel" },
      el("h2", { text: "Mystery" }),
      el("p", { class: "tab-status" }, "No mystery right now -- playing a hobby can turn one up.")
    );
  }

  const children = [
    el("h2", { text: "Mystery" }),
    el(
      "p",
      { class: "tab-status" },
      `You found ${chain.item_name} (${chain.found_how}) in ${ctx.districtName(chain.found_district_id)}.`
    ),
  ];

  if (chain.step === 0) {
    const investigateBtn = el("button", { class: "btn", type: "button" }, "Investigate");
    investigateBtn.addEventListener("click", async () => {
      resultLine.textContent = "";
      try {
        const result = await ctx.apiFetch(`/activity/dashboard/events/${ctx.characterId()}/mystery`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId() }),
        });
        resultLine.className = "result-line win";
        resultLine.textContent = `Likely owner: ${result.owner_npc_name}. ${result.clue}`;
        onChanged();
      } catch (err) {
        resultLine.className = "result-line lose";
        resultLine.textContent = err.message;
      }
    });
    children.push(el("div", { class: "field-row" }, investigateBtn));
  } else {
    children.push(
      el("p", { class: "tab-status" }, `It likely belongs to ${chain.target_npc_name}.`)
    );
    const deliverBtn = el("button", { class: "btn", type: "button" }, "Deliver");
    deliverBtn.addEventListener("click", async () => {
      resultLine.textContent = "";
      try {
        const result = await ctx.apiFetch(`/activity/dashboard/events/${ctx.characterId()}/deliver`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId() }),
        });
        resultLine.className = "result-line win";
        resultLine.textContent =
          `Delivered to ${result.npc_name} -- received ${result.money} money and ` +
          `${result.reputation} reputation.`;
        onChanged();
      } catch (err) {
        resultLine.className = "result-line lose";
        resultLine.textContent = err.message;
      }
    });
    children.push(el("div", { class: "field-row" }, deliverBtn));
  }

  return el("div", { class: "panel" }, ...children);
}

function leaderboardPanel(ctx, event_, resultLine) {
  const host = el("div", {});
  const viewBtn = el("button", { class: "btn secondary", type: "button" }, "View leaderboard");
  viewBtn.addEventListener("click", async () => {
    host.innerHTML = "";
    try {
      const board = await ctx.apiFetch(
        `/activity/dashboard/events/${ctx.characterId()}/global/${event_.event_id}/leaderboard?discord_id=${encodeURIComponent(ctx.discordId())}`
      );
      host.append(
        board.entries.length
          ? el(
              "table",
              { class: "data-table" },
              el(
                "thead",
                {},
                el(
                  "tr",
                  {},
                  el("th", { text: "#" }),
                  el("th", { text: "Character" }),
                  el("th", { text: "Score" }),
                  el("th", { text: "Attempts" })
                )
              ),
              el(
                "tbody",
                {},
                ...board.entries.map((entry) =>
                  el(
                    "tr",
                    {},
                    el("td", { text: String(entry.rank) }),
                    el("td", { text: entry.character_name }),
                    el("td", { text: String(entry.score) }),
                    el("td", { text: String(entry.attempts) })
                  )
                )
              )
            )
          : el("p", { class: "tab-status" }, "Nobody's scored on this event yet.")
      );
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  const scope = event_.district_id == null ? "World-wide" : ctx.districtName(event_.district_id);
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: `${event_.name} (${event_.hobby_name})` }),
    el("p", { class: "tab-status" }, event_.description),
    el(
      "p",
      { class: "tab-status" },
      `${scope} -- ${event_.status}${event_.in_range ? "" : " -- out of range for your current district"}.` +
        ` Starts ${ctx.formatTick(event_.starts_tick)}, ends ${ctx.formatTick(event_.ends_tick)}.`
    ),
    el("div", { class: "field-row" }, viewBtn),
    host
  );
}

export function mount(root, ctx) {
  const statusLine = el("p", { class: "tab-status" });
  const resultLine = el("p", { class: "result-line" });
  const host = el("div", {});
  root.append(statusLine, host, resultLine);

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    host.innerHTML = "";
    if (!characterId || !discordId) {
      setStatusText(statusLine, "Pick a character above first.");
      return;
    }
    try {
      const [status, globalList] = await Promise.all([
        ctx.apiFetch(
          `/activity/dashboard/events/${characterId}/status?discord_id=${encodeURIComponent(discordId)}`
        ),
        ctx.apiFetch(
          `/activity/dashboard/events/${characterId}/global?discord_id=${encodeURIComponent(discordId)}`
        ),
      ]);
      setStatusText(statusLine, "");
      const globalPanels = globalList.events.length
        ? globalList.events.map((event_) => leaderboardPanel(ctx, event_, resultLine))
        : [
            el(
              "div",
              { class: "panel" },
              el("h2", { text: "Global Events" }),
              el("p", { class: "tab-status" }, "No global events scheduled or running right now.")
            ),
          ];
      host.append(mysteryPanel(ctx, status.chain, resultLine, refresh), ...globalPanels);
    } catch (err) {
      setStatusText(statusLine, `Could not load events: ${err.message}`, { error: true });
    }
  }

  refresh();

  return {};
}
