// `/lockpick`, `/steal`, `/burgle`'s Activity minigames (contraband
// system's skill check) -- crime.html's coordinator, mirroring work.js's
// shape closely. Unlike `/work`, a crime attempt never launches through
// Discord's embedded_application voice-channel invite -- see
// `panem_bot.activity_launch`'s module docstring for why -- so this page
// only ever reads a plain `?attempt_id=&kind=`, no `channel_id` fallback
// needed.
//
// Every game module exports `mount(boardEl, { onFinish, setStatus, difficulty })`,
// rendering itself into #board and calling `onFinish(won)` exactly once
// once the attempt is decided. `kind: "lockpick"` (escaping jail) and
// `kind: "burgle"` (breaking into a house) both play the same lockpick
// minigame -- picking a lock is picking a lock either way, just against
// a cell door or a house door -- while `kind: "steal"` plays the
// pickpocket timing minigame instead, and `kind: "poach"` plays the
// archery target-practice minigame.
//
// `?v=` cache-busting matches work.js's own reasoning: bump
// ASSET_VERSION (and crime.html's/crime.css's matching `?v=`) any time
// this file or anything under games/lockpick.js|pickpocket.js|archery.js
// changes.
//
// Like work.js, this page notifies a parent window (via postMessage) once
// an attempt is resolved -- the dashboard's Jail/Crime tabs embed this page
// in an `<iframe>` for the lockpick/steal/burgle/poach minigames (see
// static/tabs/jail.js, static/tabs/crime.js) and use it to refresh their
// own status without a reload. A no-op outside an iframe.
const ASSET_VERSION = "13";

// Donor dashboard theme (`static/theme_picker.js`'s popup, saved via the
// profile endpoints under `/activity/dashboard/theme/profiles`): `app.js`
// mirrors the resolved `--bg`/`--accent`/`--panel`/`--text` CSS custom
// properties to localStorage on every load, so this same-origin page --
// opened either directly or in the dashboard's Jail/Crime tabs' iframe,
// with no Discord identity of its own to ask `/identify` -- picks up the
// same colors. Best-effort: a missing/blocked value just leaves
// `style.css`'s plain default in place for that one property.
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
// it (the dashboard's Jail/Crime tabs, in an iframe -- see
// static/tabs/_shared.js's `watchIframeResize`) so that iframe can grow to
// fit instead of clipping or scrolling internally. Matches work.js's own
// `reportSize` -- see its comment for the full reasoning. A no-op outside
// an iframe, same posture as `notifyParent` below.
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

const [lockpick, pickpocket, archery] = await Promise.all([
  import(`./games/lockpick.js?v=${ASSET_VERSION}`),
  import(`./games/pickpocket.js?v=${ASSET_VERSION}`),
  import(`./games/archery.js?v=${ASSET_VERSION}`),
]);

const TITLES = {
  lockpick: "Pick the lock",
  steal: "Pick the pocket",
  burgle: "Pick the lock",
  poach: "Hunt at the outskirts",
};

const statusEl = document.getElementById("status");
const instructionsEl = document.getElementById("instructions");
const boardEl = document.getElementById("board");
const resultEl = document.getElementById("result");
const titleEl = document.getElementById("page-title");

let attemptId = null;
let kind = null;

function setStatus(text) {
  statusEl.textContent = text;
}

// A small "i" button pinned to the board's top-right corner (in addition
// to `instructionsEl`'s always-visible line above the board) so how-to-
// play text is available on demand without permanently taking up space
// on screen -- toggles a popup of the same `game.instructions()` text
// every game module already exports. Called once, right after `game.
// mount(...)` sets `boardEl`'s contents, so it isn't wiped out by it.
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
    window.parent.postMessage({ source: "panem-activity", type: "crime-result", ...payload }, "*");
  } catch {
    // Embedded in a cross-origin frame this can't reach -- nothing to do.
  }
}

function describeResult(body) {
  if (kind === "lockpick") {
    if (body.success) return `${body.character_name} works the lock loose and slips out.`;
    return `The lock holds. ${body.tries_left} attempt(s) left.`;
  }
  if (kind === "poach") {
    if (body.caught) {
      return `${body.character_name} is caught poaching -- fined ${body.fine} money and jailed.`;
    }
    if (body.success) {
      return `${body.character_name} slips back with ${body.qty}x ${body.good_name}, unseen.`;
    }
    return `${body.character_name} can't land the shot and comes back empty-handed.`;
  }
  const verb = kind === "burgle" ? "breaking in" : "going for the pocket";
  if (body.success) {
    return `${body.character_name} gets away with ${body.amount} money, unnoticed.`;
  }
  if (body.caught) {
    return `${body.character_name} is caught ${verb} -- fined and jailed.`;
  }
  if (body.alerted) {
    return `${body.character_name} is spotted ${verb} -- and bolts clear.`;
  }
  return `${body.character_name} comes up empty-handed -- and, as far as they can tell, unnoticed.`;
}

async function finish(won) {
  setStatus(won ? "Nicely done." : "That didn't go as planned.");
  try {
    const body = await fetchJson(`/activity/crime/${attemptId}/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ won }),
    });
    resultEl.hidden = false;
    resultEl.className = body.success ? "win" : "lose";
    resultEl.textContent = describeResult(body);
    notifyParent({ attemptId, kind, ...body });
  } catch (err) {
    resultEl.hidden = false;
    resultEl.className = "lose";
    resultEl.textContent = `Couldn't report the result: ${err.message}`;
  }
}

async function main() {
  const params = new URLSearchParams(location.search);
  attemptId = params.get("attempt_id");
  kind = params.get("kind");
  if (!attemptId || !kind) {
    setStatus("No attempt given -- use the link from Discord.");
    return;
  }
  let info;
  try {
    info = await fetchJson(`/activity/crime/${attemptId}`);
  } catch (err) {
    setStatus(`Can't load this attempt: ${err.message}`);
    return;
  }
  titleEl.textContent = TITLES[kind] || "Contraband";
  const game = kind === "steal" ? pickpocket : kind === "poach" ? archery : lockpick;
  setStatus(
    kind === "steal"
      ? `${info.character_name} lines up on ${info.target_name}.`
      : kind === "poach"
        ? `${info.character_name} draws a bow at the treeline.`
        : `${info.character_name} works the lock.`
  );
  // A dedicated, always-visible line rather than folding the instructions
  // into the status sentence -- `setStatus` gets overwritten as the
  // attempt plays out (win/lose text), which would otherwise take "how to
  // play" down with it right when a losing attempt most needs a reminder.
  instructionsEl.textContent = game.instructions();
  instructionsEl.hidden = false;
  boardEl.hidden = false;
  game.mount(boardEl, { onFinish: finish, setStatus, difficulty: info.difficulty });
  mountInfoButton(game.instructions());
}

main();
