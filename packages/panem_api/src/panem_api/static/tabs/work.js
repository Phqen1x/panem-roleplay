// The "Work" tab: mirrors /work. Starting a shift mints/finds the open
// Shift (same logic /work's cog runs) and embeds work.html in an
// <iframe> to actually play it, reusing the existing minigame pool
// unmodified; Skip calls the existing /activity/work/{id}/result
// endpoint directly with {won:false, neutral:true}, exactly like the
// bot's own Skip button does.
import { fetchJson, el } from "./_shared.js?v=3";

export function mount(root, ctx) {
  const statusEl = el("p", { class: "tab-status" });
  const detailEl = el("p", { class: "tab-status" });
  const playBtn = el("button", { class: "btn", type: "button" }, "Play for your shift");
  const skipBtn = el("button", { class: "btn secondary", type: "button" }, "Skip (neutral wage)");
  const resultLine = el("p", { class: "result-line" });
  const iframeHost = el("div", {});
  const actionsEl = el("div", { class: "field-row" }, playBtn, skipBtn);

  root.append(
    el("div", { class: "panel" }, el("h2", { text: "Work" }), statusEl, detailEl, actionsEl, resultLine),
    iframeHost
  );

  let currentShiftId = null;
  let messageListener = null;

  function stopListening() {
    if (messageListener) {
      window.removeEventListener("message", messageListener);
      messageListener = null;
    }
  }

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    actionsEl.hidden = true;
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      return;
    }
    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/work/${characterId}?discord_id=${encodeURIComponent(discordId)}`
      );
      if (!status.has_job) {
        statusEl.textContent = "This character has no job set.";
        return;
      }
      statusEl.textContent = `${status.job_title} -- ${status.shift_phase} shift (${status.level})`;
      detailEl.textContent = "";
      actionsEl.hidden = false;
    } catch (err) {
      statusEl.textContent = `Could not load work status: ${err.message}`;
    }
  }

  async function ensureShift() {
    // `refresh()` already hides these buttons whenever no character is
    // selected, but this guard is the one place that actually stops a
    // click from firing anyway (e.g. a click landing between a character
    // becoming unselected and this tab re-rendering) -- without it the
    // request goes out as ".../work/null/start", which FastAPI rejects as
    // a path-validation error before this shows up here as a clean,
    // readable message instead of a raw HTTP failure.
    if (!ctx.characterId()) {
      throw new Error("Pick a character above first.");
    }
    const body = await ctx.apiFetch(`/activity/dashboard/work/${ctx.characterId()}/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ discord_id: ctx.discordId() }),
    });
    currentShiftId = body.shift_id;
    if (body.already_worked_this_tick) {
      detailEl.textContent = "Already worked this shift this tick -- come back next tick.";
      return null;
    }
    return body;
  }

  playBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const started = await ensureShift();
      if (!started) return;
      iframeHost.innerHTML = "";
      const iframe = el("iframe", {
        class: "minigame-frame",
        src: `/work.html?shift_id=${encodeURIComponent(currentShiftId)}`,
      });
      iframeHost.append(iframe);
      stopListening();
      messageListener = (event) => {
        if (event.data && event.data.source === "panem-activity" && event.data.type === "work-result") {
          iframeHost.innerHTML = "";
          stopListening();
          refresh();
        }
      };
      window.addEventListener("message", messageListener);
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  skipBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const started = await ensureShift();
      if (!started) return;
      const body = await ctx.apiFetch(`/activity/work/${currentShiftId}/result`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ won: false, neutral: true }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = `${body.character_name} earns ${body.wage} money this shift.`;
      if (body.leveled_up) resultLine.textContent += ` Now a ${body.level}!`;
      refresh();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  refresh();

  return {
    unmount() {
      stopListening();
    },
  };
}
