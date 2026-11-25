// The "Hobbies" tab -- pick up to 3 hobbies, play one for a chance at a
// collectible (`/museum`) or a personal event chain (mirrors `/hobby
// select|forget|play`, `panem_shared.hobbies`). Playing now opens an
// embedded `hobby.html` minigame (fishing/gardening/baking, each hobby's
// own `games/*.js` module) the same way the Crime/Work/Vitals tabs embed
// theirs -- success requires landing that minigame AND the usual
// probability roll, mirroring the Discord side's `HobbyTimingView`
// button-press requirement exactly instead of skipping straight to the
// roll.
import { el, setStatusText, watchIframeResize } from "./_shared.js?v=13";

// How long hobby.html's own result text stays visible before this tab
// clears the iframe -- same fix/reasoning as tabs/crime.js's identical
// constant (closing the minigame the instant it announces its result
// reads as the dashboard eating the message, not showing it).
const RESULT_DISPLAY_MS = 5000;

function levelLabel(level) {
  return level.charAt(0).toUpperCase() + level.slice(1);
}

function selectedRow(ctx, hobby, resultLine, onMinigame, onChanged) {
  const playBtn = el(
    "button",
    { class: "btn", type: "button", disabled: hobby.on_cooldown ? "disabled" : undefined },
    hobby.on_cooldown ? "Resting..." : `Play (${hobby.verb})`
  );
  playBtn.addEventListener("click", () => {
    resultLine.textContent = "";
    onMinigame(hobby.hobby_id);
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
  const iframeHost = el("div", {});
  root.append(statusLine, host, resultLine, iframeHost);

  let messageListener = null;
  let closeResultTimer = null;
  let stopResizeWatch = null;

  function stopListening() {
    if (messageListener) {
      window.removeEventListener("message", messageListener);
      messageListener = null;
    }
    if (stopResizeWatch) {
      stopResizeWatch();
      stopResizeWatch = null;
    }
  }

  function clearCloseTimer() {
    if (closeResultTimer) {
      clearTimeout(closeResultTimer);
      closeResultTimer = null;
    }
  }

  function playMinigame(hobbyId) {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    clearCloseTimer();
    iframeHost.innerHTML = "";
    const iframe = el("iframe", {
      class: "minigame-frame",
      src:
        `/hobby.html?kind=${encodeURIComponent(hobbyId)}` +
        `&character_id=${encodeURIComponent(characterId)}` +
        `&discord_id=${encodeURIComponent(discordId)}` +
        `&hobby_id=${encodeURIComponent(hobbyId)}`,
    });
    iframeHost.append(iframe);
    iframe.scrollIntoView({ behavior: "smooth", block: "start" });
    stopListening();
    stopResizeWatch = watchIframeResize(iframe);
    messageListener = (event) => {
      if (event.data && event.data.source === "panem-activity" && event.data.type === "hobby-result") {
        stopListening();
        clearCloseTimer();
        closeResultTimer = setTimeout(() => {
          closeResultTimer = null;
          iframeHost.innerHTML = "";
        }, RESULT_DISPLAY_MS);
        refresh();
      }
    };
    window.addEventListener("message", messageListener);
  }

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
        ? status.selected.map((hobby) => selectedRow(ctx, hobby, resultLine, playMinigame, refresh))
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

  return {
    unmount() {
      stopListening();
      clearCloseTimer();
    },
  };
}
