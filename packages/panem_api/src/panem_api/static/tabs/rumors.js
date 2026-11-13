// The "Rumors" tab -- every rumor currently floating around your
// character's own current district, and investigating one to shift how
// reliable it looks (mirrors `/rumor list|investigate`, `panem_shared.
// rumors`). Spawning a rumor stays `panem_bot.cogs.rumors`'s hourly
// background loop -- there's no "start a rumor" action here, same as
// Discord has none.
import { el, setStatusText } from "./_shared.js?v=9";

function rumorRow(ctx, rumor, resultLine, onChanged) {
  const capped = rumor.investigations_remaining <= 0;
  const investigateBtn = el(
    "button",
    { class: "btn secondary", type: "button" },
    capped ? "Fully investigated" : "Investigate"
  );
  investigateBtn.disabled = capped;
  investigateBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const result = await ctx.apiFetch(
        `/activity/dashboard/rumors/${ctx.characterId()}/investigate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId(), rumor_id: rumor.rumor_id }),
        }
      );
      resultLine.className = "result-line win";
      const direction = result.reliability_delta >= 0 ? "up" : "down";
      resultLine.textContent =
        `${result.finding} Reliability moved ${direction} to ${result.reliability_pct}%.`;
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: `${rumor.reliability_pct}% reliable` }),
    el("p", { class: "tab-status" }, `${rumor.text} (${rumor.origin})`),
    el(
      "p",
      { class: "tab-status" },
      `Investigated ${rumor.investigation_count} time(s) -- ${rumor.investigations_remaining} left.`
    ),
    el("div", { class: "field-row" }, investigateBtn)
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
      const board = await ctx.apiFetch(
        `/activity/dashboard/rumors/${characterId}/board?discord_id=${encodeURIComponent(discordId)}`
      );
      setStatusText(statusLine, "");
      const panels = board.rumors.length
        ? board.rumors.map((rumor) => rumorRow(ctx, rumor, resultLine, refresh))
        : [
            el(
              "div",
              { class: "panel" },
              el("h2", { text: ctx.districtName(board.district_id) }),
              el("p", { class: "tab-status" }, "No rumors going around here right now.")
            ),
          ];
      host.append(...panels);
    } catch (err) {
      setStatusText(statusLine, `Could not load rumors: ${err.message}`, { error: true });
    }
  }

  refresh();

  return {};
}
