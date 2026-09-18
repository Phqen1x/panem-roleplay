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
// pickpocket timing minigame instead.
//
// `?v=` cache-busting matches work.js's own reasoning: bump
// ASSET_VERSION (and crime.html's/crime.css's matching `?v=`) any time
// this file or anything under games/lockpick.js|pickpocket.js changes.
//
// Like work.js, this page notifies a parent window (via postMessage) once
// an attempt is resolved -- the dashboard's Jail/Crime tabs embed this page
// in an `<iframe>` for the lockpick/steal/burgle minigames (see
// static/tabs/jail.js, static/tabs/crime.js) and use it to refresh their
// own status without a reload. A no-op outside an iframe.
const ASSET_VERSION = "2";

const [lockpick, pickpocket] = await Promise.all([
  import(`./games/lockpick.js?v=${ASSET_VERSION}`),
  import(`./games/pickpocket.js?v=${ASSET_VERSION}`),
]);

const TITLES = { lockpick: "Pick the lock", steal: "Pick the pocket", burgle: "Pick the lock" };

const statusEl = document.getElementById("status");
const boardEl = document.getElementById("board");
const resultEl = document.getElementById("result");
const titleEl = document.getElementById("page-title");

let attemptId = null;
let kind = null;

function setStatus(text) {
  statusEl.textContent = text;
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
  const game = kind === "steal" ? pickpocket : lockpick;
  setStatus(
    kind === "steal"
      ? `${info.character_name} lines up on ${info.target_name}. ${game.instructions()}`
      : `${info.character_name} works the lock. ${game.instructions()}`
  );
  boardEl.hidden = false;
  game.mount(boardEl, { onFinish: finish, setStatus, difficulty: info.difficulty });
}

main();
