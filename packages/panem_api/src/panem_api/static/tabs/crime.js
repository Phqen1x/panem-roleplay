// The "Crime" tab: mirrors /steal, /burgle, /poach. All three mint a
// crime attempt (same shape /activity/crime/{id} already reads) and play
// via an embedded crime.html <iframe>, reusing the pickpocket/lockpick/
// archery minigames unmodified -- same pattern as static/tabs/jail.js.
import { fetchJson, el, dropdown, setStatusText, watchIframeResize } from "./_shared.js?v=7";

// How long crime.html's own result screen (posted via postMessage, see
// static/crime.js's `finish()`) stays visible before this tab clears the
// iframe -- matches static/tabs/work.js's identical fix/reasoning: the
// listener used to clear the iframe the instant the message arrived,
// closing the minigame the same frame it announced its own outcome text.
const RESULT_DISPLAY_MS = 5000;

export function mount(root, ctx) {
  const stealSelect = dropdown();
  const stealBtn = el("button", { class: "btn", type: "button" }, "Steal");
  const stealPanel = el(
    "div",
    { class: "panel" },
    el("h2", { text: "Steal" }),
    el("div", { class: "field-row" }, el("label", { text: "Target" }), stealSelect),
    stealBtn
  );

  const burgleSelect = dropdown();
  const burgleBtn = el("button", { class: "btn", type: "button" }, "Burgle");
  const burglePanel = el(
    "div",
    { class: "panel" },
    el("h2", { text: "Burgle" }),
    el("div", { class: "field-row" }, el("label", { text: "House owner" }), burgleSelect),
    burgleBtn
  );

  const poachBtn = el("button", { class: "btn secondary", type: "button" }, "Poach at the outskirts");
  const poachPanel = el("div", { class: "panel" }, el("h2", { text: "Poach" }), poachBtn);

  const resultLine = el("p", { class: "result-line" });
  const iframeHost = el("div", {});
  const statusEl = el("p", { class: "tab-status" });
  const logBody = el("tbody", {});
  const logStatus = el("p", { class: "tab-status" });
  const logPanel = el(
    "div",
    { class: "panel" },
    el("h2", { text: "Recent Activity" }),
    logStatus,
    el(
      "table",
      { class: "data-table" },
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          el("th", { text: "Crime" }),
          el("th", { text: "Result" }),
          el("th", { text: "Detail" }),
          el("th", { text: "Tick" })
        )
      ),
      logBody
    )
  );

  root.append(statusEl, stealPanel, burglePanel, poachPanel, resultLine, iframeHost, logPanel);

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

  function mountMinigame(attemptId, kind) {
    clearCloseTimer();
    iframeHost.innerHTML = "";
    const iframe = el("iframe", {
      class: "minigame-frame",
      src: `/crime.html?attempt_id=${encodeURIComponent(attemptId)}&kind=${kind}`,
    });
    iframeHost.append(iframe);
    // Brings the newly-mounted game into view instead of leaving the
    // player to scroll down and find it themselves -- crime.html's own
    // `reportSize` then keeps this iframe grown to fit whichever minigame
    // it mounts (see watchIframeResize's own comment).
    iframe.scrollIntoView({ behavior: "smooth", block: "start" });
    stopListening();
    stopResizeWatch = watchIframeResize(iframe);
    messageListener = (event) => {
      if (event.data && event.data.source === "panem-activity" && event.data.type === "crime-result") {
        stopListening();
        // Leave crime.html's own result text ("gets away with N money",
        // "caught -- fined and jailed", ...) on screen for a few seconds
        // instead of yanking the iframe away the instant it appears.
        clearCloseTimer();
        closeResultTimer = setTimeout(() => {
          closeResultTimer = null;
          iframeHost.innerHTML = "";
        }, RESULT_DISPLAY_MS);
        loadLog();
      }
    };
    window.addEventListener("message", messageListener);
  }

  function describeLogEntry(entry) {
    const verb = { steal: "Steal", burgle: "Burgle", poach: "Poach" }[entry.kind] || entry.kind;
    if (entry.caught) {
      return { verb, result: "Caught", cls: "lose", detail: "Fined and jailed" };
    }
    if (entry.kind === "poach") {
      if (entry.good_name) {
        return { verb, result: "Success", cls: "win", detail: `${entry.amount}x ${entry.good_name}` };
      }
      return { verb, result: "Missed", cls: "", detail: "Came back empty-handed" };
    }
    if (entry.success) {
      const from = entry.target_name ? ` from ${entry.target_name}` : "";
      return { verb, result: "Success", cls: "win", detail: `${entry.amount} money${from}` };
    }
    return { verb, result: "Failed", cls: "", detail: entry.target_name || "No one to blame" };
  }

  async function loadLog() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    if (!characterId || !discordId) {
      logBody.innerHTML = "";
      setStatusText(logStatus, "");
      return;
    }
    try {
      const body = await ctx.apiFetch(
        `/activity/dashboard/crime/${characterId}/log?discord_id=${encodeURIComponent(discordId)}`
      );
      logBody.innerHTML = "";
      if (body.entries.length === 0) {
        setStatusText(logStatus, "No crimes attempted yet.");
        return;
      }
      setStatusText(logStatus, "");
      for (const entry of body.entries) {
        const { verb, result, cls, detail } = describeLogEntry(entry);
        logBody.append(
          el(
            "tr",
            {},
            el("td", { text: verb }),
            el("td", { class: cls, text: result }),
            el("td", { text: detail }),
            el("td", { text: String(entry.tick) })
          )
        );
      }
    } catch (err) {
      setStatusText(logStatus, `Could not load activity: ${err.message}`, { error: true });
    }
  }

  async function loadOptions() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    if (!characterId || !discordId) {
      setStatusText(statusEl, "Pick a character above first.");
      [stealPanel, burglePanel, poachPanel].forEach((p) => (p.hidden = true));
      return;
    }
    [stealPanel, burglePanel, poachPanel].forEach((p) => (p.hidden = false));
    setStatusText(statusEl, "");
    try {
      const [stealBody, burgleBody] = await Promise.all([
        ctx.apiFetch(
          `/activity/dashboard/crime/${characterId}/steal-targets?discord_id=${encodeURIComponent(discordId)}`
        ),
        ctx.apiFetch(
          `/activity/dashboard/crime/${characterId}/burgle-targets?discord_id=${encodeURIComponent(discordId)}`
        ),
      ]);
      if (stealBody.targets.length === 0) {
        stealSelect.setOptions([{ value: "", label: "Nobody here to steal from" }]);
        stealBtn.disabled = true;
      } else {
        stealBtn.disabled = false;
        stealSelect.setOptions(
          stealBody.targets.map((t) => ({ value: t.name, label: `${t.name} (${t.kind})` }))
        );
      }
      if (burgleBody.owners.length === 0) {
        burgleSelect.setOptions([{ value: "", label: "No houses to burgle here" }]);
        burgleBtn.disabled = true;
      } else {
        burgleBtn.disabled = false;
        burgleSelect.setOptions(burgleBody.owners.map((owner) => ({ value: owner, label: owner })));
      }
    } catch (err) {
      setStatusText(statusEl, `Could not load targets: ${err.message}`, { error: true });
    }
  }

  stealBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const body = await ctx.apiFetch(
        `/activity/dashboard/crime/${ctx.characterId()}/steal/start`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId(), target: stealSelect.value }),
        }
      );
      mountMinigame(body.attempt_id, "steal");
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  burgleBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const body = await ctx.apiFetch(
        `/activity/dashboard/crime/${ctx.characterId()}/burgle/start`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId(), owner: burgleSelect.value }),
        }
      );
      mountMinigame(body.attempt_id, "burgle");
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  poachBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const body = await ctx.apiFetch(
        `/activity/dashboard/crime/${ctx.characterId()}/poach/start`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ discord_id: ctx.discordId() }),
        }
      );
      mountMinigame(body.attempt_id, "poach");
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  loadOptions();
  loadLog();

  return {
    unmount() {
      stopListening();
      clearCloseTimer();
    },
  };
}
