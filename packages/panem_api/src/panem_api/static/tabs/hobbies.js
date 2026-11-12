// The "Hobbies" tab -- pick up to 3 hobbies, play one for a chance at a
// collectible (`/museum`) or a personal event chain (mirrors `/hobby
// select|forget|play` exactly, `panem_shared.hobbies`). `play` here rolls
// the same success probability instantly instead of waiting on the
// Discord-side timing minigame -- there's no canvas equivalent of that
// button-press window on this tab yet, the same "always playable without
// one" shape `panem_shared.hobbies`'s own module docstring describes for
// the bare-Discord path.
import { el, setStatusText } from "./_shared.js?v=7";

function levelLabel(level) {
  return level.charAt(0).toUpperCase() + level.slice(1);
}

function selectedRow(ctx, hobby, resultLine, onChanged) {
  const playBtn = el(
    "button",
    { class: "btn", type: "button", disabled: hobby.on_cooldown ? "disabled" : undefined },
    hobby.on_cooldown ? "Resting..." : `Play (${hobby.verb})`
  );
  playBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const outcome = await ctx.apiFetch(`/activity/dashboard/hobbies/${ctx.characterId()}/play`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), hobby_id: hobby.hobby_id }),
      });
      resultLine.className = `result-line ${outcome.success ? "win" : "lose"}`;
      if (!outcome.success) {
        resultLine.textContent = "Nothing this time.";
      } else if (outcome.chain_started) {
        resultLine.textContent =
          "Something odd turned up -- not a normal find. Ask around in Discord with /event mystery.";
      } else if (outcome.collectible_name) {
        resultLine.textContent = `Found: ${outcome.collectible_name} (${outcome.collectible_rarity}).`;
      } else {
        resultLine.textContent = "A successful session, but nothing to show for it.";
      }
      if (outcome.leveled_up_to) {
        resultLine.textContent += ` Leveled up to ${levelLabel(outcome.leveled_up_to)}!`;
      }
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  const forgetBtn = el("button", { class: "btn secondary", type: "button" }, "Forget");
  forgetBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch(`/activity/dashboard/hobbies/${ctx.characterId()}/forget`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), hobby_id: hobby.hobby_id }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = `Set ${hobby.name} aside.`;
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  return el(
    "div",
    { class: "panel" },
    el("h2", { text: hobby.name }),
    el(
      "p",
      { class: "tab-status" },
      `${levelLabel(hobby.level)} -- ${hobby.sessions_played} sessions played.` +
        (hobby.tool_name ? ` Tool on hand: ${hobby.tool_name}.` : " No tool on hand yet.")
    ),
    el("p", { class: "tab-status" }, hobby.description),
    el("div", { class: "field-row" }, playBtn, forgetBtn)
  );
}

function availableRow(ctx, hobby, maxed, resultLine, onChanged) {
  const selectBtn = el(
    "button",
    { class: "btn", type: "button", disabled: maxed ? "disabled" : undefined },
    "Pick this hobby"
  );
  selectBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const result = await ctx.apiFetch(`/activity/dashboard/hobbies/${ctx.characterId()}/select`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), hobby_id: hobby.hobby_id }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = result.tool_name
        ? `Picked up ${result.name}, starting with a free ${result.tool_name}.`
        : `Picked up ${result.name}.`;
      onChanged();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });
  return el(
    "div",
    { class: "field-row" },
    el("label", { text: hobby.name }),
    el("span", { class: "tab-status", text: hobby.description }),
    selectBtn
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
      const status = await ctx.apiFetch(
        `/activity/dashboard/hobbies/${characterId}/status?discord_id=${encodeURIComponent(discordId)}`
      );
      setStatusText(statusLine, "");
      const maxed = status.selected.length >= status.max_hobbies;

      const selectedPanels = status.selected.length
        ? status.selected.map((hobby) => selectedRow(ctx, hobby, resultLine, refresh))
        : [
            el(
              "div",
              { class: "panel" },
              el("h2", { text: "Your Hobbies" }),
              el("p", { class: "tab-status" }, "You haven't picked any hobbies yet.")
            ),
          ];

      const availablePanel = el(
        "div",
        { class: "panel" },
        el(
          "h2",
          { text: `Pick a Hobby (${status.selected.length}/${status.max_hobbies})` }
        ),
        ...(status.available.length
          ? status.available.map((hobby) => availableRow(ctx, hobby, maxed, resultLine, refresh))
          : [el("p", { class: "tab-status" }, "Every hobby is already picked.")])
      );

      host.append(...selectedPanels, availablePanel);
    } catch (err) {
      setStatusText(statusLine, `Could not load hobbies: ${err.message}`, { error: true });
    }
  }

  refresh();

  return {};
}
