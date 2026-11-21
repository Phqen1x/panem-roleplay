// The Hobbies tab's minigame page -- a character's hobby session now
// needs an actual skill check (fishing/gardening/baking) instead of
// resolving instantly, the same "no bare dashboard shortcut past a real
// minigame" posture work.js/crime.js/vitals.js already hold. Dispatches
// purely on `?kind=`, mirroring vitals.js's own `?kind=cook|bake` shape
// closely but trimmed to hobbies: `?kind=fishing|gardening|baking&
// character_id=<id>&discord_id=<id>&hobby_id=<id>`. `hobby_id` is
// already exactly `kind` for every hobby that exists today (`fishing`,
// `gardening`, `baking` all name both their hobby id and their game
// module 1:1), but kept as a separate param rather than collapsing them
// in case that ever stops being true (a future hobby reusing an existing
// minigame kind, say).
//
// `?v=` cache-busting matches work.js/crime.js/vitals.js's own
// reasoning: bump this file's own version (and hobby.html's/hobby.css's
// matching `?v=`) any time this file or games/fishing.js|gardening.js|
// bake.js changes.
const ASSET_VERSION = "1";

// Donor dashboard theme, same best-effort localStorage mirror every
// other standalone Activity page here already does.
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

const TITLES = {
  fishing: "Cast your line",
  gardening: "Tend the soil",
  baking: "Knead the dough",
};

const statusEl = document.getElementById("status");
const instructionsEl = document.getElementById("instructions");
const boardEl = document.getElementById("board");
const resultEl = document.getElementById("result");
const titleEl = document.getElementById("page-title");

let kind = null;
let characterId = null;
let discordId = null;
let hobbyId = null;

function setStatus(text) {
  statusEl.textContent = text;
}

// Same always-on-demand "i" info-button pattern as crime.js/work.js/
// vitals.js -- see any of those for the full reasoning.
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
    window.parent.postMessage({ source: "panem-activity", type: "hobby-result", ...payload }, "*");
  } catch {
    // Embedded in a cross-origin frame this can't reach -- nothing to do.
  }
}

function levelLabel(level) {
  return level.charAt(0).toUpperCase() + level.slice(1);
}

async function finishHobby(timingHit) {
  setStatus(timingHit ? "Nicely done." : "Didn't quite land it.");
  try {
    const outcome = await fetchJson(`/activity/dashboard/hobbies/${characterId}/play`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ discord_id: discordId, hobby_id: hobbyId, timing_hit: timingHit }),
    });
    resultEl.hidden = false;
    resultEl.className = outcome.success ? "win" : "lose";
    let text;
    if (!outcome.success) {
      text = "Nothing this time.";
    } else if (outcome.chain_started) {
      text = "Something odd turned up -- not a normal find. Ask around in Discord with /event mystery.";
    } else if (outcome.collectible_name) {
      text = `Found: ${outcome.collectible_name} (${outcome.collectible_rarity}).`;
    } else {
      text = "A successful session, but nothing to show for it.";
    }
    if (outcome.leveled_up_to) {
      text += ` Leveled up to ${levelLabel(outcome.leveled_up_to)}!`;
    }
    resultEl.textContent = text;
    notifyParent({ kind, hobbyId, timingHit, ...outcome });
    // Unhiding it below the board doesn't bring it into view by itself --
    // see work.js's `finish()` for the same fix and why it's needed.
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
  kind = params.get("kind");
  characterId = params.get("character_id");
  discordId = params.get("discord_id");
  hobbyId = params.get("hobby_id");

  if (!kind || !characterId || !discordId || !hobbyId) {
    setStatus("Missing details -- open this from the Hobbies tab.");
    return;
  }
  if (!TITLES[kind]) {
    setStatus("Unknown hobby minigame.");
    return;
  }

  titleEl.textContent = TITLES[kind];
  setStatus(`${TITLES[kind]}...`);

  const game = await import(`./games/${kind === "baking" ? "bake" : kind}.js?v=${ASSET_VERSION}`);
  instructionsEl.textContent = game.instructions();
  instructionsEl.hidden = false;
  boardEl.hidden = false;
  game.mount(boardEl, { onFinish: finishHobby, setStatus, difficulty: 0.5 });
  mountInfoButton(game.instructions());
}

main();
