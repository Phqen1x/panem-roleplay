// The Activity frontend (Plan §8, Phase 5): a live schematic map of a
// district's NPCs/characters, served as plain static files (no build
// step) by panem_api alongside its own REST/WebSocket data endpoints.
//
// This intentionally works in two modes:
//   1. Inside a real Discord Activity iframe -- does the embedded-app-sdk
//      authorize()/authenticate() handshake through /activity/token.
//   2. Opened directly in a browser (e.g. http://localhost:8000/) for local
//      testing -- the SDK handshake fails fast outside Discord's iframe, so
//      this falls back to "preview mode" and shows the map anyway.
//
// There's no real district map art yet (District.map.image in data/*.yaml
// is a placeholder path -- see the README), so this draws a schematic
// layout from each location's map coordinates rather than a background
// image.
//
// The embedded-app-sdk is loaded from a CDN via a *dynamic* import inside
// the same try/catch as the handshake itself, deliberately not a static
// top-level `import` -- a static import that fails to fetch (offline dev,
// a restrictive local network, an ad/tracker blocker) would throw before
// any of this module's code runs at all, permanently stuck on "Connecting…"
// with no fallback. A dynamic import failure is just another reason to
// fall back to preview mode.

const DISCORD_SDK_URL = "https://cdn.jsdelivr.net/npm/@discord/embedded-app-sdk@1/+esm";

const statusEl = document.getElementById("status");
const districtSelect = document.getElementById("district-select");
const countsEl = document.getElementById("counts");
const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");

let districts = [];
let currentDistrict = null;
let socket = null;

function setStatus(text) {
  statusEl.textContent = text;
}

async function fetchJson(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    throw new Error(`${path} -> ${response.status}`);
  }
  return response.json();
}

async function authenticateWithDiscord() {
  let clientId = "";
  try {
    ({ client_id: clientId } = await fetchJson("/activity/config"));
  } catch (err) {
    console.warn("Could not reach /activity/config:", err);
  }
  if (!clientId) {
    setStatus("Preview mode -- no DISCORD_CLIENT_ID configured on this server.");
    return;
  }

  try {
    const { DiscordSDK } = await import(DISCORD_SDK_URL);
    const discordSdk = new DiscordSDK(clientId);
    await discordSdk.ready();
    const { code } = await discordSdk.commands.authorize({
      client_id: clientId,
      response_type: "code",
      state: "",
      prompt: "none",
      scope: ["identify"],
    });
    const { access_token: accessToken } = await fetchJson("/activity/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    await discordSdk.commands.authenticate({ access_token: accessToken });
    setStatus("Connected via Discord.");
  } catch (err) {
    // Expected whenever this page isn't actually running inside a Discord
    // Activity iframe (e.g. a plain browser tab during local testing).
    console.warn("Not running as an authenticated Discord Activity:", err);
    setStatus("Preview mode -- not running inside Discord (or auth failed). Showing the live map anyway.");
  }
}

function wsUrlFor(districtId) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws/districts/${districtId}/positions`;
}

function connectToDistrict(districtId) {
  if (socket) {
    socket.close();
  }
  currentDistrict = districts.find((d) => d.id === districtId) ?? null;
  if (!currentDistrict) {
    return;
  }
  socket = new WebSocket(wsUrlFor(districtId));
  socket.onmessage = (event) => render(JSON.parse(event.data));
  socket.onerror = () => setStatus("Lost connection to the district feed -- retrying on reconnect.");
}

function toCanvasX(x) {
  return (x / currentDistrict.map_width) * canvas.width;
}

function toCanvasY(y) {
  return (y / currentDistrict.map_height) * canvas.height;
}

function render(positions) {
  const d = currentDistrict;
  if (!d) {
    return;
  }

  ctx.fillStyle = "#1b1f27";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.strokeStyle = "#3a4150";
  ctx.fillStyle = "#8b93a3";
  ctx.font = "12px sans-serif";
  ctx.textAlign = "center";
  for (const loc of d.locations) {
    const x = toCanvasX(loc.x);
    const y = toCanvasY(loc.y);
    ctx.beginPath();
    ctx.arc(x, y, 28, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillText(loc.name, x, y - 34);
  }

  ctx.fillStyle = "#7d8597";
  for (const npc of positions.npcs) {
    if (npc.x == null || npc.y == null) {
      continue;
    }
    ctx.beginPath();
    ctx.arc(toCanvasX(npc.x), toCanvasY(npc.y), 3, 0, Math.PI * 2);
    ctx.fill();
  }

  for (const character of positions.characters) {
    if (character.x == null || character.y == null) {
      continue;
    }
    const x = toCanvasX(character.x);
    const y = toCanvasY(character.y);
    ctx.fillStyle = "#e0a72e";
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillText(character.name, x, y + 16);
  }

  countsEl.textContent = `${positions.npcs.length} NPCs, ${positions.characters.length} characters`;
}

async function main() {
  await authenticateWithDiscord();

  try {
    districts = await fetchJson("/districts");
  } catch (err) {
    setStatus(`Could not load districts: ${err}`);
    return;
  }

  districtSelect.innerHTML = "";
  districtSelect.disabled = false;
  for (const d of districts) {
    const option = document.createElement("option");
    option.value = String(d.id);
    option.textContent = d.name;
    districtSelect.appendChild(option);
  }
  districtSelect.addEventListener("change", () => {
    connectToDistrict(Number(districtSelect.value));
  });

  if (districts.length > 0) {
    districtSelect.value = String(districts[0].id);
    connectToDistrict(districts[0].id);
  }
}

main();
