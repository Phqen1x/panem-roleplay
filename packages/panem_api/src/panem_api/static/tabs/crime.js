// The "Crime" tab: mirrors /steal, /burgle, /poach. Steal/burgle mint a
// crime attempt (same shape /activity/crime/{id} already reads) and play
// via an embedded crime.html <iframe>, reusing the pickpocket/lockpick
// minigames unmodified -- same pattern as static/tabs/jail.js. /poach
// never launches an Activity on the bot side either, so it resolves
// instantly here too.
import { fetchJson, el, dropdown } from "./_shared.js?v=3";

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

  root.append(statusEl, stealPanel, burglePanel, poachPanel, resultLine, iframeHost);

  let messageListener = null;
  function stopListening() {
    if (messageListener) {
      window.removeEventListener("message", messageListener);
      messageListener = null;
    }
  }

  function mountMinigame(attemptId, kind) {
    iframeHost.innerHTML = "";
    const iframe = el("iframe", {
      class: "minigame-frame",
      src: `/crime.html?attempt_id=${encodeURIComponent(attemptId)}&kind=${kind}`,
    });
    iframeHost.append(iframe);
    stopListening();
    messageListener = (event) => {
      if (event.data && event.data.source === "panem-activity" && event.data.type === "crime-result") {
        iframeHost.innerHTML = "";
        stopListening();
      }
    };
    window.addEventListener("message", messageListener);
  }

  async function loadOptions() {
    const characterId = ctx.characterId();
    const discordId = ctx.discordId();
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      [stealPanel, burglePanel, poachPanel].forEach((p) => (p.hidden = true));
      return;
    }
    [stealPanel, burglePanel, poachPanel].forEach((p) => (p.hidden = false));
    statusEl.textContent = "";
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
      statusEl.textContent = `Could not load targets: ${err.message}`;
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
      const body = await ctx.apiFetch(`/activity/dashboard/crime/${ctx.characterId()}/poach`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: ctx.discordId() }),
      });
      if (body.caught) {
        resultLine.className = "result-line lose";
        resultLine.textContent = `Caught! Fined ${body.fine} and jailed.`;
      } else {
        resultLine.className = "result-line win";
        resultLine.textContent = `Poached ${body.qty}x ${body.good_name}.`;
      }
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  loadOptions();

  return {
    unmount() {
      stopListening();
    },
  };
}
