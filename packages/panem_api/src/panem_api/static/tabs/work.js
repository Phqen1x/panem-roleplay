// The "Work" tab: mirrors /work. Starting a shift mints/finds the open
// Shift (same logic /work's cog runs) and embeds work.html in an
// <iframe> to actually play it, reusing the existing minigame pool
// unmodified; Skip calls the existing /activity/work/{id}/result
// endpoint directly with {won:false, neutral:true}, exactly like the
// bot's own Skip button does.
//
// A work log panel sits to the right, listing recent shift outcomes for
// the currently-selected character -- purely a client-side convenience
// (there's no server-side shift-history endpoint to back it), so it's
// kept in localStorage per character id and just grows/reads locally.
// It survives tab switches and page reloads the same way the header's
// remembered character selection does (see app.js's readStorage/
// writeStorage), but not across browsers/devices, and two people sharing
// a browser profile would share it -- an acceptable tradeoff for a
// convenience log, not a source of truth (the DB's `money`/`shifts_completed`
// columns are that).
import { fetchJson, el } from "./_shared.js?v=3";

const WORK_LOG_LIMIT = 20;
// How long the minigame's own result screen (posted via postMessage, see
// static/work.js's `finish()`) stays visible before this tab clears the
// iframe and refreshes -- previously that happened the instant the
// message arrived, closing the game the same frame it announced "you
// earned N money", which nobody could actually read.
const RESULT_DISPLAY_MS = 5000;

function workLogKey(characterId) {
  return `panem_work_log_${characterId}`;
}

function readWorkLog(characterId) {
  try {
    const raw = window.localStorage.getItem(workLogKey(characterId));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function appendWorkLogEntry(characterId, entry) {
  const entries = readWorkLog(characterId);
  entries.unshift({ ...entry, ts: Date.now() });
  entries.length = Math.min(entries.length, WORK_LOG_LIMIT);
  try {
    window.localStorage.setItem(workLogKey(characterId), JSON.stringify(entries));
  } catch {
    // Private browsing / blocked storage -- the log just won't persist.
  }
  return entries;
}

function logEntryLabel(entry) {
  if (entry.neutral) return "Skip";
  if (entry.game) return entry.game;
  return entry.won ? "Win" : "Loss";
}

function logEntryLine(entry) {
  const bits = [`${logEntryLabel(entry)} -- +${entry.wage} money`];
  if (entry.leveledUp) bits.push(`now ${entry.level}`);
  if (entry.arrested) bits.push("arrested");
  const cssClass = entry.neutral ? "" : entry.won ? "win" : "lose";
  return el("li", { class: cssClass }, bits.join(", "));
}

function renderWorkLog(logListEl, characterId) {
  logListEl.innerHTML = "";
  if (!characterId) {
    logListEl.append(el("li", { class: "tab-status" }, "Pick a character to see their work log."));
    return;
  }
  const entries = readWorkLog(characterId);
  if (entries.length === 0) {
    logListEl.append(el("li", { class: "tab-status" }, "No shifts worked yet."));
    return;
  }
  for (const entry of entries) {
    logListEl.append(logEntryLine(entry));
  }
}

export function mount(root, ctx) {
  const statusEl = el("p", { class: "tab-status" });
  const detailEl = el("p", { class: "tab-status" });
  const playBtn = el("button", { class: "btn", type: "button" }, "Play for your shift");
  const skipBtn = el("button", { class: "btn secondary", type: "button" }, "Skip (neutral wage)");
  const resultLine = el("p", { class: "result-line" });
  const iframeHost = el("div", {});
  const actionsEl = el("div", { class: "field-row" }, playBtn, skipBtn);
  const logListEl = el("ul", { class: "work-log-list" });

  root.append(
    el(
      "div",
      { class: "work-layout" },
      el(
        "div",
        { class: "work-main" },
        el("div", { class: "panel" }, el("h2", { text: "Work" }), statusEl, detailEl, actionsEl, resultLine),
        iframeHost
      ),
      el("div", { class: "panel work-log" }, el("h2", { text: "Work Log" }), logListEl)
    )
  );

  let currentShiftId = null;
  let messageListener = null;
  let closeResultTimer = null;

  function stopListening() {
    if (messageListener) {
      window.removeEventListener("message", messageListener);
      messageListener = null;
    }
  }

  function clearCloseTimer() {
    if (closeResultTimer) {
      clearTimeout(closeResultTimer);
      closeResultTimer = null;
    }
  }

  async function refresh() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    actionsEl.hidden = true;
    renderWorkLog(logListEl, characterId);
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
      clearCloseTimer();
      iframeHost.innerHTML = "";
      const iframe = el("iframe", {
        class: "minigame-frame",
        src: `/work.html?shift_id=${encodeURIComponent(currentShiftId)}`,
      });
      iframeHost.append(iframe);
      stopListening();
      messageListener = (event) => {
        if (event.data && event.data.source === "panem-activity" && event.data.type === "work-result") {
          stopListening();
          const characterId = ctx.characterId();
          if (characterId) {
            appendWorkLogEntry(characterId, {
              game: event.data.game || null,
              won: Boolean(event.data.won),
              neutral: Boolean(event.data.neutral),
              wage: event.data.wage,
              leveledUp: Boolean(event.data.leveled_up),
              level: event.data.level,
              arrested: Boolean(event.data.arrested),
            });
            renderWorkLog(logListEl, characterId);
          }
          // Leave the minigame's own result screen ("X earns N money",
          // level-ups, arrests) on screen for a few seconds instead of
          // yanking the iframe away the instant it appears -- refresh()
          // (which re-hides/re-shows the Play/Skip buttons) waits until
          // after the iframe is actually gone.
          clearCloseTimer();
          closeResultTimer = setTimeout(() => {
            closeResultTimer = null;
            iframeHost.innerHTML = "";
            refresh();
          }, RESULT_DISPLAY_MS);
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
      const characterId = ctx.characterId();
      if (characterId) {
        appendWorkLogEntry(characterId, {
          game: null,
          won: body.won,
          neutral: true,
          wage: body.wage,
          leveledUp: Boolean(body.leveled_up),
          level: body.level,
          arrested: Boolean(body.arrested),
        });
      }
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
      clearCloseTimer();
    },
  };
}
