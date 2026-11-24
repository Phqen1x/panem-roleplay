// The "Reactions" tab -- your archetype standing (Good/Neutral/Evil)
// (mirrors `/reaction archetype`, `panem_shared.reactions`). A pending
// pop-up itself is no longer shown/answered here specifically -- `app.js`
// polls for one globally and floats it above whichever tab happens to be
// open when it lands, since there's no reason a pop-up should wait for
// the player to be looking at this one tab in particular. This tab is
// just the always-available place to check your current standing, same
// as `/reaction archetype` is on Discord.
import { el, setStatusText } from "./_shared.js?v=11";

function archetypePanel(status) {
  const children = [
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
    ),
  ];
  if (status.pending) {
    children.push(
      el("p", { class: "tab-status" }, "Something's come up -- answer it in the pop-up window.")
    );
  }
  return el("div", { class: "panel" }, ...children);
}

export function mount(root, ctx) {
  const statusLine = el("p", { class: "tab-status" });
  const host = el("div", {});
  root.append(statusLine, host);

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
      host.append(archetypePanel(status));
    } catch (err) {
      setStatusText(statusLine, `Could not load reactions: ${err.message}`, { error: true });
    }
  }

  refresh();

  return {};
}
