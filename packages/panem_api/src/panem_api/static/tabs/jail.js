// The "Jail" tab: mirrors /bail and /lockpick. The jail cell itself is
// original inline SVG (bars, floor, a dim light) with the character's
// portrait composited inside when jailed -- no external image asset, same
// build-original-art precedent as the lockpick/pickpocket minigames.
// Lockpicking is played by minting an attempt (same Redis-backed shape
// `/lockpick` mints) and embedding crime.html in an <iframe>, reusing that
// page's minigame unmodified; a postMessage from crime.js on completion
// (see that file's docstring) tells this tab to refresh.
import { fetchJson, el } from "./_shared.js?v=2";

function jailCellSvg({ occupied, avatarUrl }) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 240 180");
  svg.setAttribute("class", "jail-cell-svg");

  function rect(x, y, w, h, fill) {
    const r = document.createElementNS(ns, "rect");
    r.setAttribute("x", x);
    r.setAttribute("y", y);
    r.setAttribute("width", w);
    r.setAttribute("height", h);
    r.setAttribute("fill", fill);
    return r;
  }

  svg.append(rect(0, 0, 240, 180, "#1b1f27"));
  svg.append(rect(10, 130, 220, 12, "#3a4150")); // floor
  const light = rect(100, 20, 40, 40, occupied ? "#e0a72e" : "#3a4150");
  light.setAttribute("opacity", occupied ? "0.25" : "0.1");
  light.setAttribute("rx", "20");
  svg.append(light);

  if (occupied) {
    const clipId = "jail-avatar-clip";
    const defs = document.createElementNS(ns, "defs");
    const clip = document.createElementNS(ns, "clipPath");
    clip.setAttribute("id", clipId);
    const clipCircle = document.createElementNS(ns, "circle");
    clipCircle.setAttribute("cx", "120");
    clipCircle.setAttribute("cy", "95");
    clipCircle.setAttribute("r", "34");
    clip.append(clipCircle);
    defs.append(clip);
    svg.append(defs);

    if (avatarUrl) {
      const img = document.createElementNS(ns, "image");
      img.setAttribute("href", avatarUrl);
      img.setAttribute("x", "86");
      img.setAttribute("y", "61");
      img.setAttribute("width", "68");
      img.setAttribute("height", "68");
      img.setAttribute("preserveAspectRatio", "xMidYMid slice");
      img.setAttribute("clip-path", `url(#${clipId})`);
      svg.append(img);
    } else {
      const circle = document.createElementNS(ns, "circle");
      circle.setAttribute("cx", "120");
      circle.setAttribute("cy", "95");
      circle.setAttribute("r", "34");
      circle.setAttribute("fill", "#8b93a3");
      svg.append(circle);
    }
    const outline = document.createElementNS(ns, "circle");
    outline.setAttribute("cx", "120");
    outline.setAttribute("cy", "95");
    outline.setAttribute("r", "34");
    outline.setAttribute("fill", "none");
    outline.setAttribute("stroke", "#e0a72e");
    outline.setAttribute("stroke-width", "2");
    svg.append(outline);
  }

  // Bars, drawn last so they sit in front of the occupant.
  for (let i = 0; i < 9; i++) {
    const x = 20 + i * 25;
    svg.append(rect(x, 15, 5, 140, "#14161c"));
  }
  svg.append(rect(10, 15, 220, 8, "#14161c"));

  return svg;
}

export function mount(root, ctx) {
  const cellWrap = el("div", { class: "jail-cell-wrap" });
  const statusEl = el("p", { class: "tab-status" });
  const detailEl = el("p", { class: "tab-status" });
  const resultLine = el("p", { class: "result-line" });
  const actionsEl = el("div", { class: "field-row" });
  const iframeHost = el("div", {});

  const bailBtn = el("button", { class: "btn", type: "button" }, "Pay Bail");
  const lockpickBtn = el("button", { class: "btn secondary", type: "button" }, "Attempt Lockpick");
  actionsEl.append(bailBtn, lockpickBtn);

  root.append(
    el("div", { class: "panel" }, el("h2", { text: "Jail" }), cellWrap, statusEl, detailEl, actionsEl, resultLine),
    iframeHost
  );

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
    iframeHost.innerHTML = "";
    stopListening();
    if (!characterId || !discordId) {
      statusEl.textContent = "Pick a character above to see their jail status.";
      cellWrap.innerHTML = "";
      actionsEl.hidden = true;
      return;
    }
    actionsEl.hidden = false;
    try {
      const status = await ctx.apiFetch(
        `/activity/dashboard/jail/${characterId}?discord_id=${encodeURIComponent(discordId)}`
      );
      cellWrap.innerHTML = "";
      cellWrap.append(jailCellSvg({ occupied: status.jailed, avatarUrl: status.avatar_url }));
      if (status.jailed) {
        statusEl.textContent = `${status.character_name} is behind bars.`;
        detailEl.textContent = `Bail: ${status.bail_cost} money -- Lockpick tries left: ${status.tries_left}/${status.tries_used + status.tries_left}`;
        bailBtn.hidden = false;
        lockpickBtn.hidden = status.tries_left <= 0;
      } else {
        statusEl.textContent = `${status.character_name} is free.`;
        detailEl.textContent = "";
        bailBtn.hidden = true;
        lockpickBtn.hidden = true;
      }
    } catch (err) {
      statusEl.textContent = `Could not load jail status: ${err.message}`;
    }
  }

  bailBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const body = await ctx.apiFetch(`/activity/dashboard/jail/${ctx.characterId()}/bail`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: Number(ctx.discordId()) }),
      });
      resultLine.className = "result-line win";
      resultLine.textContent = `${body.character_name} pays ${body.cost} and walks free.`;
      refresh();
    } catch (err) {
      resultLine.className = "result-line lose";
      resultLine.textContent = err.message;
    }
  });

  lockpickBtn.addEventListener("click", async () => {
    resultLine.textContent = "";
    try {
      const body = await ctx.apiFetch(`/activity/dashboard/jail/${ctx.characterId()}/lockpick/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: Number(ctx.discordId()) }),
      });
      iframeHost.innerHTML = "";
      const iframe = el("iframe", {
        class: "minigame-frame",
        src: `/crime.html?attempt_id=${encodeURIComponent(body.attempt_id)}&kind=lockpick`,
      });
      iframeHost.append(iframe);
      messageListener = (event) => {
        if (event.data && event.data.source === "panem-activity" && event.data.type === "crime-result") {
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

  refresh();

  return {
    unmount() {
      stopListening();
    },
  };
}
