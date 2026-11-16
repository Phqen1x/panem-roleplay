// The Rumors tab's minigame page -- investigating a rumor now needs an
// actual skill check ("Spot the Odd One Out", `games/odd_one_out.js`)
// instead of resolving instantly. No `attempt_id`/Redis indirection the
// way Crime's minigames need (nothing here is a one-shot, cashable-in
// resolution worth protecting against replay -- `panem_shared.rumors.
// investigate`'s own `RUMOR_MAX_INVESTIGATIONS` cap, a real persisted
// count, already does that job): `character_id`/`discord_id`/`rumor_id`
// go straight in the URL and straight into the result POST, the same
// simpler shape `hobby.js` already uses for the same reason.
//
// `?character_id=<id>&discord_id=<id>&rumor_id=<id>`.
//
// `?v=` cache-busting matches every other standalone Activity page here:
// bump this file's own version (and rumor_investigate.html's/.css's
// matching `?v=`) any time this file or games/odd_one_out.js changes.
const ASSET_VERSION = "1";

try {
  const bg = window.localStorage.getItem("panem_theme_bg");
  const accent = window.localStorage.getItem("panem_theme_accent");
  const panel = window.localStorage.getItem("panem_theme_panel");
  const text = window.localStorage.getItem("panem_theme_text");
  if (bg) document.documentElement.style.setProperty("--bg", bg);
  if (accent) document.documentElement.style.setProperty("--accent", accent);
  if (panel) document.documentElement.style.setProperty("--panel", panel);
  if (text) document.documentElement.style.setProperty("--text", text);
} catch {
  // Private browsing / blocked storage -- falls back to the default theme.
}

function reportSize() {
  if (window.parent === window) return;
  try {
    window.parent.postMessage(
      { source: "panem-activity", type: "resize", height: document.documentElement.scrollHeight },
      "*"
    );
  } catch {
    // Embedded in a cross-origin frame this can't reach -- nothing to do.
  }
}
if (window.parent !== window) {
  new ResizeObserver(reportSize).observe(document.documentElement);
  window.addEventListener("load", reportSize);
}

const statusEl = document.getElementById("status");
const instructionsEl = document.getElementById("instructions");
const boardEl = document.getElementById("board");
const resultEl = document.getElementById("result");

let characterId = null;
let discordId = null;
let rumorId = null;

function setStatus(text) {
  statusEl.textContent = text;
}

function mountInfoButton(text) {
  boardEl.style.position = "relative";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "info-btn";
  btn.textContent = "i";
  btn.setAttribute("aria-label", "How to play");
  btn.setAttribute("aria-expanded", "false");
  const popup = document.createElement("div");
  popup.className = "info-popup";
  popup.textContent = text;
  popup.hidden = true;
  btn.addEventListener("click", () => {
    popup.hidden = !popup.hidden;
    btn.setAttribute("aria-expanded", String(!popup.hidden));
  });
  boardEl.append(btn, popup);
}

async function fetchJson(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `${path} -> ${response.status}`);
  }
  return body;
}

function notifyParent(payload) {
  if (window.parent === window) return;
  try {
    window.parent.postMessage(
      { source: "panem-activity", type: "rumor-investigate-result", ...payload },
      "*"
    );
  } catch {
    // Embedded in a cross-origin frame this can't reach -- nothing to do.
  }
}

async function finishInvestigate(success) {
  setStatus(success ? "Found it." : "Didn't spot it in time.");
  try {
    const result = await fetchJson(`/activity/dashboard/rumors/${characterId}/investigate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ discord_id: discordId, rumor_id: rumorId, success }),
    });
    resultEl.hidden = false;
    resultEl.className = success ? "win" : "lose";
    resultEl.textContent = `${result.finding} Reliability now ${result.reliability_pct}%.`;
    notifyParent({ success, ...result });
    resultEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    resultEl.hidden = false;
    resultEl.className = "lose";
    resultEl.textContent = `Couldn't report the result: ${err.message}`;
    resultEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

async function main() {
  const params = new URLSearchParams(location.search);
  characterId = params.get("character_id");
  discordId = params.get("discord_id");
  rumorId = params.get("rumor_id");

  if (!characterId || !discordId || !rumorId) {
    setStatus("Missing details -- open this from the Rumors tab.");
    return;
  }

  setStatus("Asking around...");
  const game = await import(`./games/odd_one_out.js?v=${ASSET_VERSION}`);
  instructionsEl.textContent = game.instructions();
  instructionsEl.hidden = false;
  boardEl.hidden = false;
  game.mount(boardEl, { onFinish: finishInvestigate, setStatus, difficulty: 0.5 });
  mountInfoButton(game.instructions());
}

main();
