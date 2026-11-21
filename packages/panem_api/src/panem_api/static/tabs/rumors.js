// The "Rumors" tab -- every rumor currently floating around your
// character's own current district, investigating one (a "Spot the Odd
// One Out" minigame, not an instant click), and deleting one outright
// (mirrors `/rumor list|investigate`, `panem_shared.rumors`). Spawning a
// rumor stays `panem_bot.cogs.weather`'s poll (for one tied to a real
// event) and `panem_bot.cogs.rumors`'s hourly background loop (for a
// free-floating one) -- there's no "start a rumor" action here, same as
// Discord has none.
import { el, setStatusText, watchIframeResize } from "./_shared.js?v=9";

// How long rumor_investigate.html's own result text stays visible before
// this tab clears the iframe -- same fix/reasoning as tabs/crime.js's
// identical constant.
const RESULT_DISPLAY_MS = 5000;

function resolutionPanel(ctx, rumor) {
  const resolution = rumor.resolution;
  let text;
  if (!resolution) {
    text =
      rumor.reliability_pct >= 60
        ? "This has been investigated as thoroughly as it's going to get -- it checks out, " +
          `${rumor.reliability_pct}% reliable, but nothing concrete enough to act on turned up.`
        : rumor.reliability_pct <= 40
          ? "Fully investigated and it doesn't hold up -- nothing concrete ever backed it, and " +
            `it's sitting at ${rumor.reliability_pct}% reliable.`
          : `Fully investigated -- the findings stayed mixed, settling at ${rumor.reliability_pct}% reliable.`;
  } else if (resolution.kind === "weather_event") {
    const when = ctx.formatTick(resolution.predicted_start_tick);
    text =
      rumor.reliability_pct >= 60
        ? `This checks out: expect a ${resolution.name.toLowerCase()} (category ${resolution.severity}) ` +
          `in ${ctx.districtName(resolution.district_id)} around ${when} -- worth preparing your home beforehand.`
        : rumor.reliability_pct <= 40
          ? `This doesn't hold up -- no ${resolution.name.toLowerCase()} actually lines up with what's been found.`
          : `Still unclear whether the ${resolution.name.toLowerCase()} talk is worth acting on.`;
  } else {
    const when = ctx.formatTick(resolution.starts_tick);
    text =
      rumor.reliability_pct >= 60
        ? `This checks out: "${resolution.name}" (${resolution.hobby_name}) starts around ${when} -- ` +
          "worth practicing beforehand."
        : rumor.reliability_pct <= 40
          ? `This doesn't hold up -- "${resolution.name}" doesn't line up with what's been found.`
          : `Still unclear whether "${resolution.name}" is worth acting on.`;
  }
  return el("p", { class: "tab-status" }, text);
}

function rumorRow(ctx, rumor, resultLine, onInvestigate, onDelete) {
  const capped = rumor.investigations_remaining <= 0;
  const investigateBtn = el(
    "button",
    { class: "btn secondary", type: "button" },
    capped ? "Fully investigated" : "Investigate"
  );
  investigateBtn.disabled = capped;
  investigateBtn.addEventListener("click", () => onInvestigate(rumor.rumor_id));

  const deleteBtn = el("button", { class: "btn secondary", type: "button" }, "Delete");
  deleteBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      await ctx.apiFetch(`/activity/dashboard/rumors/${ctx.characterId()}/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId(), rumor_id: rumor.rumor_id }),
      });
      onDelete();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  const children = [
    el("h2", { text: `${rumor.reliability_pct}% reliable` }),
    el("p", { class: "tab-status" }, `${rumor.text} (${rumor.origin})`),
    el(
      "p",
      { class: "tab-status" },
      `Investigated ${rumor.investigation_count} time(s) -- ${rumor.investigations_remaining} left.` +
        (rumor.expires_tick != null ? ` Expires around ${ctx.formatTick(rumor.expires_tick)}.` : "")
    ),
  ];
  if (capped) {
    children.push(resolutionPanel(ctx, rumor));
  }
  children.push(el("div", { class: "field-row" }, investigateBtn, deleteBtn));
  return el("div", { class: "panel" }, ...children);
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

  function investigate(rumorId) {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    resultLine.textContent = "";
    clearCloseTimer();
    iframeHost.innerHTML = "";
    const iframe = el("iframe", {
      class: "minigame-frame",
      src:
        `/rumor_investigate.html?rumor_id=${encodeURIComponent(rumorId)}` +
        `&character_id=${encodeURIComponent(characterId)}` +
        `&discord_id=${encodeURIComponent(discordId)}`,
    });
    iframeHost.append(iframe);
    iframe.scrollIntoView({ behavior: "smooth", block: "start" });
    stopListening();
    stopResizeWatch = watchIframeResize(iframe);
    messageListener = (event) => {
      if (
        event.data &&
        event.data.source === "panem-activity" &&
        event.data.type === "rumor-investigate-result"
      ) {
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
      const board = await ctx.apiFetch(
        `/activity/dashboard/rumors/${characterId}/board?discord_id=${encodeURIComponent(discordId)}`
      );
      setStatusText(statusLine, "");
      const panels = board.rumors.length
        ? board.rumors.map((rumor) => rumorRow(ctx, rumor, resultLine, investigate, refresh))
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

  return {
    unmount() {
      stopListening();
      clearCloseTimer();
    },
  };
}
