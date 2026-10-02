// The "Vitals" tab: eat/drink/sleep/entertain, mirroring `/activity/
// dashboard/vitals` (Milestone 5) and embedding vitals.html (Milestone 6)
// in an <iframe> for the cook/bake minigame and the Entertainment panel's
// six leisure games -- the same same-origin-iframe-embedding approach
// static/tabs/work.js already established for work.html.
//
// Simulation-mode gating reuses `/activity/dashboard/mode/{id}/status`
// (the Home tab's own endpoint, Milestone 11 of the RP Modes feature)
// rather than inventing a second mode check -- Life/Story characters get
// the same "doesn't apply in your mode" framing Home's own meters panel
// already uses, before this tab ever calls the Vitals endpoints those
// characters would just get refused from anyway.
//
// Sleep has no dedicated backend route of its own (per the plan) -- this
// panel POSTs straight to the existing `/activity/dashboard/housing/{id}/
// sleep` endpoint the Housing tab's own (simpler) sleep control already
// uses, just with a live client-side "you'll restore ~X fatigue" preview
// on top, computed from the Vitals status endpoint's `has_bed`/
// `max_sleep_ticks`/`fatigue_restore_per_tick` so no round trip is needed
// per keystroke.
import { el, watchIframeResize } from "./_shared.js?v=7";

const RESULT_DISPLAY_MS = 4000;

function meterBar(label, value) {
  const pct = Math.max(0, Math.min(100, value));
  return el(
    "div",
    { class: "meter-row" },
    el("span", { class: "meter-label", text: `${label}  ${Math.round(value)}` }),
    el("div", { class: "meter-track" }, el("div", { class: "meter-fill", style: `width: ${pct}%` }))
  );
}

export function mount(root, ctx) {
  const statusEl = el("p", { class: "tab-status" });
  const resultLine = el("p", { class: "result-line" });
  const metersHost = el("div", {});
  const iframeHost = el("div", {});
  const eatHost = el("div", {});
  const drinkHost = el("div", {});
  const healHost = el("div", {});
  const sleepHost = el("div", {});
  const entertainHost = el("div", {});

  root.append(
    el(
      "div",
      { class: "panel" },
      el("h2", { text: "Vitals" }),
      statusEl,
      resultLine,
      metersHost
    ),
    iframeHost,
    el("div", { class: "panel" }, el("h2", { text: "Eat" }), eatHost),
    el("div", { class: "panel" }, el("h2", { text: "Drink" }), drinkHost),
    el("div", { class: "panel" }, el("h2", { text: "Heal" }), healHost),
    el("div", { class: "panel" }, el("h2", { text: "Sleep" }), sleepHost),
    el("div", { class: "panel" }, el("h2", { text: "Entertainment" }), entertainHost)
  );

  let messageListener = null;
  let closeResultTimer = null;
  let stopResizeWatch = null;

  function stopListening() {
    if (messageListener) window.removeEventListener("message", messageListener);
    messageListener = null;
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

  function openMinigame(query) {
    clearCloseTimer();
    iframeHost.innerHTML = "";
    const iframe = el("iframe", { class: "minigame-frame", src: `/vitals.html?${query}` });
    iframeHost.append(iframe);
    // Brings the newly-mounted game into view instead of leaving the
    // player to scroll down and find it themselves -- vitals.html's own
    // `reportSize` then keeps this iframe grown to fit whichever minigame
    // it mounts (see watchIframeResize's own comment).
    iframe.scrollIntoView({ behavior: "smooth", block: "start" });
    stopListening();
    stopResizeWatch = watchIframeResize(iframe);
    messageListener = (event) => {
      if (event.data && event.data.source === "panem-activity" && event.data.type === "vitals-result") {
        stopListening();
        // Leave the minigame's own result screen on-screen for a few
        // seconds instead of yanking the iframe away the instant it
        // appears -- refresh() (which rebuilds the panels below) waits
        // until after the iframe is actually gone.
        clearCloseTimer();
        closeResultTimer = setTimeout(() => {
          closeResultTimer = null;
          iframeHost.innerHTML = "";
          refresh();
        }, RESULT_DISPLAY_MS);
      }
    };
    window.addEventListener("message", messageListener);
  }

  function renderEat(status, characterId, discordId) {
    eatHost.innerHTML = "";
    if (status.edible.length === 0) {
      eatHost.append(el("p", { class: "tab-status" }, "No edible goods in inventory."));
      return;
    }
    for (const good of status.edible) {
      const eatBtn = el("button", { class: "btn", type: "button" }, "Eat");
      eatBtn.addEventListener("click", async () => {
        resultLine.textContent = "";
        try {
          const body = await ctx.apiFetch(`/activity/dashboard/vitals/${characterId}/eat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ discord_id: discordId, good_id: good.good_id, bonus: false }),
          });
          resultLine.className = "result-line win";
          resultLine.textContent = `${body.good_name} eaten -- hunger now ${body.hunger}/100.`;
          refresh();
        } catch (err) {
          resultLine.className = "result-line lose";
          resultLine.textContent = err.message;
        }
      });
      const row = el(
        "div",
        { class: "field-row" },
        el("span", {}, `${good.name} x${good.qty} (+${good.hunger_value} hunger)`),
        eatBtn
      );
      if (good.cook_method) {
        const isOven = good.cook_method === "oven";
        const cookBtn = el(
          "button",
          { class: "btn secondary", type: "button" },
          isOven ? "Bake it (2x bonus)" : "Cook it (2x bonus)"
        );
        cookBtn.addEventListener("click", () => {
          openMinigame(
            `kind=${isOven ? "bake" : "cook"}&character_id=${characterId}` +
              `&discord_id=${encodeURIComponent(discordId)}&good_id=${encodeURIComponent(good.good_id)}`
          );
        });
        row.append(cookBtn);
      }
      eatHost.append(row);
    }
  }

  function renderDrink(status, characterId, discordId) {
    drinkHost.innerHTML = "";
    if (status.drinkable.length === 0) {
      drinkHost.append(el("p", { class: "tab-status" }, "No drinkable goods in inventory."));
      return;
    }
    for (const good of status.drinkable) {
      const drinkBtn = el("button", { class: "btn", type: "button" }, "Drink");
      drinkBtn.addEventListener("click", async () => {
        resultLine.textContent = "";
        try {
          const body = await ctx.apiFetch(`/activity/dashboard/vitals/${characterId}/drink`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ discord_id: discordId, good_id: good.good_id }),
          });
          resultLine.className = "result-line win";
          resultLine.textContent = `${body.good_name} drunk -- thirst now ${body.thirst}/100.`;
          refresh();
        } catch (err) {
          resultLine.className = "result-line lose";
          resultLine.textContent = err.message;
        }
      });
      drinkHost.append(
        el(
          "div",
          { class: "field-row" },
          el("span", {}, `${good.name} x${good.qty} (+${good.thirst_value} thirst)`),
          drinkBtn
        )
      );
    }
  }

  function renderHeal(status, characterId, discordId) {
    healHost.innerHTML = "";
    const items = status.healable || [];
    if (items.length === 0) {
      healHost.append(el("p", { class: "tab-status" }, "No medical goods in inventory."));
      return;
    }
    for (const good of items) {
      const healBtn = el("button", { class: "btn", type: "button" }, "Use");
      healBtn.addEventListener("click", async () => {
        resultLine.textContent = "";
        try {
          const body = await ctx.apiFetch(`/activity/dashboard/vitals/${characterId}/heal`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ discord_id: discordId, good_id: good.good_id }),
          });
          resultLine.className = "result-line win";
          resultLine.textContent = `${body.good_name} used -- health now ${body.health}/100.`;
          refresh();
        } catch (err) {
          resultLine.className = "result-line lose";
          resultLine.textContent = err.message;
        }
      });
      healHost.append(
        el(
          "div",
          { class: "field-row" },
          el("span", {}, `${good.name} x${good.qty} (+${good.heal_value} health)`),
          healBtn
        )
      );
    }
  }

  function renderSleep(status, characterId, discordId) {
    sleepHost.innerHTML = "";
    const ticksInput = el("input", {
      type: "number",
      min: "1",
      max: String(status.max_sleep_ticks),
      placeholder: `ticks (max ${status.max_sleep_ticks})`,
    });
    const previewEl = el("p", { class: "tab-status" });
    const sleepBtn = el("button", { class: "btn", type: "button" }, "Sleep");
    const restBtn = el("button", { class: "btn secondary", type: "button" }, "Sleep the rest of the phase");

    function updatePreview() {
      const raw = ticksInput.value ? Number(ticksInput.value) : status.max_sleep_ticks;
      const ticks = Math.max(1, Math.min(status.max_sleep_ticks, raw || 1));
      const restored = Math.round(ticks * status.fatigue_restore_per_tick * 10) / 10;
      previewEl.textContent = `You'll restore ~${restored} fatigue${status.has_bed ? "" : " (no bed -- half rate)"}.`;
    }
    updatePreview();
    ticksInput.addEventListener("input", updatePreview);

    async function doSleep(ticks) {
      resultLine.textContent = "";
      try {
        const body = await ctx.apiFetch(`/activity/dashboard/housing/${characterId}/sleep`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: discordId, ticks }),
        });
        resultLine.className = "result-line win";
        resultLine.textContent = `Slept ${body.ticks} ticks, restored ${body.restored} fatigue.`;
        refresh();
      } catch (err) {
        resultLine.className = "result-line lose";
        resultLine.textContent = err.message;
      }
    }

    sleepBtn.addEventListener("click", () => {
      doSleep(ticksInput.value ? Number(ticksInput.value) : null);
    });
    restBtn.addEventListener("click", () => doSleep(null));

    sleepHost.append(
      el("div", { class: "field-row" }, el("label", { text: "Ticks" }), ticksInput),
      previewEl,
      el("div", { class: "field-row" }, sleepBtn, restBtn)
    );
  }

  function renderEntertain(status, characterId, discordId) {
    entertainHost.innerHTML = "";
    for (const option of status.entertainment) {
      const playBtn = el("button", { class: "btn", type: "button" }, "Play");
      playBtn.addEventListener("click", () => {
        openMinigame(
          `kind=entertain&character_id=${characterId}` +
            `&discord_id=${encodeURIComponent(discordId)}&game=${encodeURIComponent(option.game_id)}`
        );
      });
      entertainHost.append(
        el(
          "div",
          { class: "field-row" },
          el("span", {}, `${option.label} (+${option.sanity_value} sanity)`),
          playBtn
        )
      );
    }
  }

  async function refresh() {
    metersHost.innerHTML = "";
    eatHost.innerHTML = "";
    drinkHost.innerHTML = "";
    healHost.innerHTML = "";
    sleepHost.innerHTML = "";
    entertainHost.innerHTML = "";
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      return;
    }

    let mode;
    try {
      mode = await ctx.apiFetch(
        `/activity/dashboard/mode/${characterId}/status?discord_id=${encodeURIComponent(discordId)}`
      );
    } catch (err) {
      statusEl.textContent = err.message;
      return;
    }
    if (mode.mode !== "simulation") {
      statusEl.textContent = "Vitals only apply in Simulation mode.";
      return;
    }

    let status;
    try {
      status = await ctx.apiFetch(
        `/activity/dashboard/vitals/${characterId}/status?discord_id=${encodeURIComponent(discordId)}`
      );
    } catch (err) {
      statusEl.textContent = err.message;
      return;
    }
    statusEl.textContent = "";

    metersHost.append(
      meterBar("Health", status.health),
      meterBar("Hunger", status.hunger),
      meterBar("Thirst", status.thirst),
      meterBar("Fatigue", status.fatigue),
      meterBar("Sanity", status.sanity)
    );
    renderEat(status, characterId, discordId);
    renderDrink(status, characterId, discordId);
    renderHeal(status, characterId, discordId);
    renderSleep(status, characterId, discordId);
    renderEntertain(status, characterId, discordId);
  }

  refresh();

  return {
    unmount() {
      stopListening();
      clearCloseTimer();
    },
  };
}
