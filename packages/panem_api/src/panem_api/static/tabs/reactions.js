// The "Reactions" tab -- your archetype standing (Good/Neutral/Evil),
// plus answering a still-pending reaction pop-up from the dashboard
// instead of only through the DM'd Discord view (mirrors `/reaction
// archetype|respond`, `panem_shared.reactions`). The pop-up itself is
// only ever *created* by `panem_bot.cogs.reactions`'s background trigger
// loop -- this tab only ever reads/answers whatever that loop already
// logged, polling while mounted so one that fires while the tab is open
// still shows up without a manual refresh.
import { el, setStatusText } from "./_shared.js?v=9";

const POLL_INTERVAL_MS = 4000;

function archetypePanel(status) {
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Your Archetype" }),
    el(
      "p",
      { class: "tab-status" },
      `Currently reading as ${status.archetype_label} (score ${status.archetype_score.toFixed(1)}).`
    ),
    el(
      "p",
      { class: "tab-status" },
      "A consistent run of choices one way builds toward being seen as that archetype -- picking the " +
        "opposite once doesn't erase the streak, but it does cost real ground. Picking neutral softens " +
        "an extreme stance without ever being free."
    )
  );
}

function pendingPanel(ctx, pending, resultLine, onChanged) {
  const seconds = Math.max(0, Math.round(pending.expires_in_seconds));
  const countdown = el(
    "p",
    { class: "tab-status" },
    seconds > 0 ? `You have about ${seconds}s left to answer.` : "This pop-up is about to expire."
  );
  const buttons = pending.options.map((opt) => {
    const btn = el("button", { class: "btn", type: "button" }, opt.label);
    btn.addEventListener("click", async () => {
      resultLine.textContent = "";
      for (const b of buttons) b.disabled = true;
      try {
        const result = await ctx.apiFetch(
          `/activity/dashboard/reactions/${ctx.characterId()}/respond`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ discord_id: ctx.discordId(), choice: opt.choice }),
          }
        );
        resultLine.className = "result-line win";
        resultLine.textContent = `Reading now: ${result.archetype_label} (score ${result.archetype_score.toFixed(1)}).`;
        onChanged();
      } catch (err) {
        resultLine.className = "result-line lose";
        resultLine.textContent = err.message;
        for (const b of buttons) b.disabled = false;
      }
    });
    return btn;
  });
  return el(
    "div",
    { class: "panel hero-panel" },
    el("h2", { text: "Something's Happening" }),
    el("p", { class: "tab-status" }, pending.prompt),
    countdown,
    el("div", { class: "field-row" }, ...buttons)
  );
}

export function mount(root, ctx) {
  const statusLine = el("p", { class: "tab-status" });
  const resultLine = el("p", { class: "result-line" });
  const host = el("div", {});
  root.append(statusLine, host, resultLine);

  let pollTimer = null;

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    host.innerHTML = "";
    if (!characterId || !discordId) {
      setStatusText(statusLine, "Pick a character above first.");
      return;
    }
    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/reactions/${characterId}/status?discord_id=${encodeURIComponent(discordId)}`
      );
      setStatusText(statusLine, "");
      const panels = [
        status.pending ? pendingPanel(ctx, status.pending, resultLine, refresh) : null,
        archetypePanel(status),
      ].filter(Boolean);
      host.append(...panels);
    } catch (err) {
      setStatusText(statusLine, `Could not load reactions: ${err.message}`, { error: true });
    }
  }

  refresh();
  pollTimer = setInterval(refresh, POLL_INTERVAL_MS);

  return {
    unmount() {
      if (pollTimer) clearInterval(pollTimer);
    },
  };
}
