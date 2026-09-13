// `/work`'s minigame (Plan §9-adjacent): a random pick from a small pool
// of classic minigames (see ./games/*.js). No Discord SDK handshake here
// (unlike app.js) -- this page doesn't read any Discord-scoped data
// itself, it just needs to know *which shift*, which arrives one of two
// ways depending on how /work launched this page:
//   1. The plain-browser fallback link: `?shift_id=<id>` directly.
//   2. A real embedded Discord Activity launch (an `embedded_application`
//      invite): Discord loads this app's one configured root URL,
//      appending its own params (`channel_id`, `guild_id`, `instance_id`,
//      ...) -- never a custom `?shift_id=`. `resolveShiftId` falls back to
//      asking panem_api which shift is pending for that `channel_id`
//      (`panem_shared.redis_keys.work_pending_key`, written by /work).
//
// Every game module exports `mount(boardEl, { onFinish, setStatus })`,
// which renders itself into `#board` and calls `onFinish(won)` exactly
// once when the shift's outcome is decided -- this file doesn't care how
// a game reaches that decision, only what it reports. Winning/losing
// posts to panem_api's work-result endpoint, which pays the shift the
// same way panem_bot's classic /work option-select flow does, just with
// a win/lose wage multiplier instead of a chosen option's
// (`panem_shared.shifts.resolve_shift_game`).
//
// `?v=` cache-busting: there's no build step here (matching
// index.html/app.js's existing no-build pattern), so browsers and --
// worse -- Discord's own Activity CDN can go on serving a stale cached
// copy of these modules well after the server has been restarted with
// new ones (a plain server restart doesn't invalidate anything a client
// already fetched by URL). Bump ASSET_VERSION any time work.js or any
// file under games/ changes, and update the matching `?v=` on work.html's
// own <script> tag to match -- changing the URL is what actually forces
// every cache layer to refetch, restarting the server does not.
const ASSET_VERSION = "1";

const [coinflip, connect4, minesweeper, poison, snake, solitaire] = await Promise.all([
  import(`./games/coinflip.js?v=${ASSET_VERSION}`),
  import(`./games/connect4.js?v=${ASSET_VERSION}`),
  import(`./games/minesweeper.js?v=${ASSET_VERSION}`),
  import(`./games/poison.js?v=${ASSET_VERSION}`),
  import(`./games/snake.js?v=${ASSET_VERSION}`),
  import(`./games/solitaire.js?v=${ASSET_VERSION}`),
]);

const GAMES = [minesweeper, snake, connect4, coinflip, poison, solitaire];

const statusEl = document.getElementById("status");
const boardEl = document.getElementById("board");
const resultEl = document.getElementById("result");

let shiftId = null;

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

async function finish(won) {
  setStatus(won ? "Shift cleared!" : "The shift got away from you.");
  try {
    const body = await fetchJson(`/activity/work/${shiftId}/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ won }),
    });
    resultEl.hidden = false;
    resultEl.className = won ? "win" : "lose";
    resultEl.textContent = `${body.character_name} earns ${body.wage} money this shift.`;
    if (body.leveled_up) {
      resultEl.textContent += ` Now a ${body.level}!`;
    }
  } catch (err) {
    resultEl.hidden = false;
    resultEl.className = "lose";
    resultEl.textContent = `Couldn't report the result: ${err.message}`;
  }
}

async function resolveShiftId() {
  const params = new URLSearchParams(location.search);
  const direct = params.get("shift_id");
  if (direct) {
    return direct;
  }
  const channelId = params.get("channel_id");
  if (!channelId) {
    return null;
  }
  try {
    const body = await fetchJson(`/activity/work/for-channel/${channelId}`);
    return String(body.shift_id);
  } catch {
    return null;
  }
}

async function main() {
  shiftId = await resolveShiftId();
  if (!shiftId) {
    setStatus("No shift given -- use the link from /work in Discord.");
    return;
  }
  let info;
  try {
    info = await fetchJson(`/activity/work/${shiftId}`);
  } catch (err) {
    setStatus(`Can't load this shift: ${err.message}`);
    return;
  }
  if (info.already_resolved) {
    setStatus(`${info.character_name}'s ${info.job_title} shift is already done.`);
    return;
  }

  const game = GAMES[Math.floor(Math.random() * GAMES.length)];
  setStatus(`${info.character_name} works as ${info.job_title}. ${game.instructions}`);
  boardEl.hidden = false;
  game.mount(boardEl, { onFinish: finish, setStatus });
}

main();
