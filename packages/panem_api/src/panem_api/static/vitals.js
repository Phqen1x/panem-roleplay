// The Vitals tab's minigame page (Milestone 6): cook/bake's timing stage
// and the Entertainment panel's six leisure games, all dispatched from one
// `?kind=` query param -- mirrors crime.js's `?kind=` dispatch shape
// closely, adapted to the dashboard's own identity model (`character_id`+
// `discord_id` in the URL, per dashboard_routes.py's module docstring --
// there's no server-minted "attempt" here the way crime/work have).
//
// `?kind=cook|bake&character_id=<id>&discord_id=<id>&good_id=<id>` plays
// games/cook.js or games/bake.js; on finish, `bonus` (only true if the
// gauge's "Take it off"/"Pull it out" landed in the Perfect zone) is
// posted straight through to `POST /activity/dashboard/vitals/{id}/eat`
// -- the same trust model every other Activity minigame here already
// uses for its own client-reported outcome (see panem_shared.sustenance's
// module docstring).
//
// `?kind=entertain&character_id=<id>&discord_id=<id>&game=<game_id>`
// reuses one of the six existing `games/*.js` leisure modules unmodified
// (the same pool `/work` already plays) and credits sanity on completion
// regardless of win/lose via `POST .../entertain` -- "the point is
// playing, not winning" (see the plan's Milestone 2 section).
//
// `?v=` cache-busting matches work.js/crime.js's own reasoning: bump
// ASSET_VERSION (and vitals.html's/vitals.css's matching `?v=`) any time
// this file or games/cook.js|bake.js changes.
const ASSET_VERSION = "3";

// Donor dashboard theme, same best-effort localStorage mirror every other
// standalone Activity page here already does (see crime.js's own comment
// for the full reasoning) -- this page has no Discord identity of its own
// to resolve a theme from directly.
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

// Reports this page's actual rendered height to whatever parent embedded
// it (the dashboard's Vitals tab, in an iframe -- see static/tabs/
// _shared.js's `watchIframeResize`) so that iframe can grow to fit instead
// of clipping or scrolling internally. Matches work.js's own `reportSize`
// -- see its comment for the full reasoning (Cook/Bake's gauge plus its
// win/lose text needs more room than a quick Entertainment game does). A
// no-op outside an iframe, same posture as `notifyParent` below.
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

const TITLES = {
  cook: "Cook it just right",
  bake: "Bake it just right",
  entertain: "Entertain yourself",
};

const statusEl = document.getElementById("status");
const instructionsEl = document.getElementById("instructions");
const boardEl = document.getElementById("board");
const resultEl = document.getElementById("result");
const titleEl = document.getElementById("page-title");

let kind = null;
let characterId = null;
let discordId = null;
let goodId = null;
let gameId = null;

function setStatus(text) {
  statusEl.textContent = text;
}

// Same "i" info-button pattern as crime.js/work.js -- see either for the
// full reasoning (an always-on-demand how-to-play popup that survives
// `game.mount(...)` overwriting `boardEl`'s contents).
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
    window.parent.postMessage({ source: "panem-activity", type: "vitals-result", ...payload }, "*");
  } catch {
    // Embedded in a cross-origin frame this can't reach -- nothing to do.
  }
}

async function finishCookBake(bonus) {
  setStatus(bonus ? "Landed it perfectly." : "Done, if not perfect.");
  try {
    const body = await fetchJson(`/activity/dashboard/vitals/${characterId}/eat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ discord_id: discordId, good_id: goodId, bonus }),
    });
    resultEl.hidden = false;
    resultEl.className = bonus ? "win" : "lose";
    resultEl.textContent = `${body.good_name} eaten -- hunger now ${body.hunger}/100${bonus ? " (bonus!)" : ""}.`;
    notifyParent({ kind, goodId, bonus, ...body });
  } catch (err) {
    resultEl.hidden = false;
    resultEl.className = "lose";
    resultEl.textContent = `Couldn't report the result: ${err.message}`;
  }
}

async function finishEntertain(won) {
  setStatus(won ? "Nicely played." : "Not your best round, but still a break.");
  try {
    const body = await fetchJson(`/activity/dashboard/vitals/${characterId}/entertain`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ discord_id: discordId, game_id: gameId }),
    });
    resultEl.hidden = false;
    resultEl.className = won ? "win" : "lose";
    resultEl.textContent = `Sanity now ${body.sanity}/100.`;
    notifyParent({ kind, gameId, won, ...body });
  } catch (err) {
    resultEl.hidden = false;
    resultEl.className = "lose";
    resultEl.textContent = `Couldn't report the result: ${err.message}`;
  }
}

async function main() {
  const params = new URLSearchParams(location.search);
  kind = params.get("kind");
  characterId = params.get("character_id");
  discordId = params.get("discord_id");
  goodId = params.get("good_id");
  gameId = params.get("game");

  if (!kind || !characterId || !discordId) {
    setStatus("Missing details -- open this from the Vitals tab.");
    return;
  }
  if ((kind === "cook" || kind === "bake") && !goodId) {
    setStatus("No ingredient chosen -- open this from the Vitals tab.");
    return;
  }
  if (kind === "entertain" && !gameId) {
    setStatus("No game chosen -- open this from the Vitals tab.");
    return;
  }

  titleEl.textContent = TITLES[kind] || "Vitals";

  let status;
  try {
    status = await fetchJson(
      `/activity/dashboard/vitals/${characterId}/status?discord_id=${encodeURIComponent(discordId)}`
    );
  } catch (err) {
    setStatus(`Can't load Vitals: ${err.message}`);
    return;
  }

  if (kind === "cook" || kind === "bake") {
    const good = status.edible.find((g) => g.good_id === goodId);
    if (!good) {
      setStatus("That ingredient isn't in inventory anymore.");
      return;
    }
    const [game] = await Promise.all([import(`./games/${kind}.js?v=${ASSET_VERSION}`)]);
    setStatus(`Cooking ${good.name}...`);
    instructionsEl.textContent = game.instructions();
    instructionsEl.hidden = false;
    boardEl.hidden = false;
    game.mount(boardEl, { onFinish: finishCookBake, setStatus });
    mountInfoButton(game.instructions());
    return;
  }

  // kind === "entertain"
  const option = status.entertainment.find((g) => g.game_id === gameId);
  const [game] = await Promise.all([import(`./games/${gameId}.js?v=${ASSET_VERSION}`)]);
  setStatus(`Playing ${option ? option.label : game.label}...`);
  instructionsEl.textContent = game.instructions();
  instructionsEl.hidden = false;
  boardEl.hidden = false;
  game.mount(boardEl, { onFinish: finishEntertain, setStatus });
  mountInfoButton(game.instructions());
}

main();
