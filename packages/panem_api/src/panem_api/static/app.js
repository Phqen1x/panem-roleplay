// The Activity frontend's shell: header (Discord identity + character
// picker), a tab nav, and a router that mounts one `tabs/*.js` module at a
// time into `#tab-root`. This used to be the whole page (a single-view
// live map with no tabs) -- that original behavior now lives, unchanged,
// in `tabs/map.js`; this file's job is just auth + identity + routing.
//
// Two auth-adjacent things happen here that didn't before:
//   1. The embedded-app-sdk's `authenticate()` result is now actually
//      read (it used to be discarded) -- it carries the real logged-in
//      Discord user's `id`/`username`/`avatar`, which is what lets the
//      dashboard know *whose* characters to offer, the same way each
//      slash command's `character:` autocomplete already knows via
//      Discord's own interaction context.
//   2. Outside a real Discord Activity (this SDK handshake fails fast in
//      a plain browser tab, same as before), a manual "Discord ID" field
//      lets the dashboard still be used/tested in preview mode -- the
//      same fallback spirit as the old map-only page's "preview mode",
//      just needing one more piece of input now that there's real
//      per-player state to look up.
//
// See `dashboard_routes.py`'s module docstring for the identity model
// this feeds into server-side (POST /activity/dashboard/identify, plus
// every other dashboard endpoint re-validating discord_id+character_id
// together) -- there's still no cryptographic auth here, same documented
// gap as the rest of this process.
import { fetchJson, el } from "./tabs/_shared.js";

const DISCORD_SDK_URL = "/vendor/discord-embedded-app-sdk.js";
const STEP_TIMEOUT_MS = 8000;

// Bumped whenever any file under tabs/ changes -- matches work.js's/
// crime.js's own single-constant-for-a-whole-module-group convention.
const ASSET_VERSION = "4";

const TABS = ["map", "character", "work", "market", "travel", "social", "jail", "crime", "housing"];
const TAB_LABELS = {
  map: "Map",
  character: "Character",
  work: "Work",
  market: "Market",
  travel: "Travel",
  social: "Social",
  jail: "Jail",
  crime: "Crime",
  housing: "Housing",
};

const statusEl = document.getElementById("status");
const navEl = document.getElementById("tab-nav");
const tabRootEl = document.getElementById("tab-root");
const discordIdInput = document.getElementById("discord-id-input");
const characterSelect = document.getElementById("character-select");

const state = {
  discordUser: null,
  manualDiscordId: "",
  characters: [],
  characterId: null,
};

let currentTabHandle = null;

function setStatus(text) {
  statusEl.textContent = text;
}

function getDiscordId() {
  if (state.discordUser) return state.discordUser.id;
  return state.manualDiscordId || null;
}

function readStorage(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Private browsing / blocked storage -- selection just won't persist.
  }
}

const STEP = { current: "" };

function withTimeout(promise, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      setTimeout(() => reject(new Error(`"${label}" timed out after ${STEP_TIMEOUT_MS}ms`)), STEP_TIMEOUT_MS);
    }),
  ]);
}

async function reportClientError(step, err) {
  try {
    await fetch("/activity/debug", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        step,
        message: err instanceof Error ? err.message : String(err),
        stack: err instanceof Error ? err.stack : undefined,
      }),
    });
  } catch {
    // Nothing more we can do if even this fails.
  }
}

// Resolves the real Discord user via the embedded-app-sdk handshake, or
// null if this isn't running inside a real Discord Activity (falls back to
// the manual Discord ID field for preview mode either way).
async function authenticateWithDiscord() {
  let clientId = "";
  try {
    ({ client_id: clientId } = await fetchJson("/activity/config"));
  } catch (err) {
    console.warn("Could not reach /activity/config:", err);
  }
  if (!clientId) {
    setStatus("Preview mode -- no DISCORD_CLIENT_ID configured on this server.");
    return null;
  }

  STEP.current = "loading embedded-app-sdk";
  try {
    const { DiscordSDK } = await import(DISCORD_SDK_URL);
    const discordSdk = new DiscordSDK(clientId);

    STEP.current = "discordSdk.ready()";
    await withTimeout(discordSdk.ready(), STEP.current);

    STEP.current = "commands.authorize()";
    const { code } = await withTimeout(
      discordSdk.commands.authorize({
        client_id: clientId,
        response_type: "code",
        state: "",
        scope: ["identify"],
      }),
      STEP.current
    );

    STEP.current = "/activity/token exchange";
    const { access_token: accessToken } = await fetchJson("/activity/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });

    STEP.current = "commands.authenticate()";
    const authResult = await withTimeout(
      discordSdk.commands.authenticate({ access_token: accessToken }),
      STEP.current
    );

    setStatus("Connected via Discord.");
    return authResult?.user ?? null;
  } catch (err) {
    // Expected whenever this page isn't actually running inside a Discord
    // Activity iframe (e.g. a plain browser tab during local testing) --
    // but also where a real misconfiguration surfaces. Reported to both
    // the browser console and the server log so it's visible either way.
    console.error(`Discord Activity auth failed at step "${STEP.current}":`, err);
    reportClientError(STEP.current, err);
    setStatus(
      `Preview mode -- Discord auth failed at "${STEP.current}" ` +
        `(${err instanceof Error ? err.message : err}). Enter a Discord ID below to try the ` +
        "dashboard anyway; check the panem_api server log for details."
    );
    return null;
  }
}

function renderCharacterOptions() {
  characterSelect.innerHTML = "";
  if (state.characters.length === 0) {
    characterSelect.disabled = true;
    characterSelect.append(el("option", {}, "No characters"));
    state.characterId = null;
    return;
  }
  characterSelect.disabled = false;
  for (const character of state.characters) {
    const label = character.jailed_until_tick ? `${character.name} (jailed)` : character.name;
    characterSelect.append(el("option", { value: String(character.id) }, label));
  }
  const savedId = Number(readStorage("panem_character_id"));
  const stillValid = state.characters.some((c) => c.id === savedId);
  state.characterId = stillValid ? savedId : state.characters[0].id;
  characterSelect.value = String(state.characterId);
}

async function refreshIdentity() {
  const discordId = getDiscordId();
  if (!discordId) {
    state.characters = [];
    renderCharacterOptions();
    return;
  }
  try {
    const body = await fetchJson("/activity/dashboard/identify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ discord_id: Number(discordId) }),
    });
    state.characters = body.characters;
  } catch (err) {
    console.warn("Could not load characters:", err);
    state.characters = [];
  }
  renderCharacterOptions();
}

function buildCtx() {
  return {
    discordId: getDiscordId,
    characterId: () => state.characterId,
    characters: () => state.characters,
    apiFetch: fetchJson,
    refreshIdentity,
  };
}

function currentTabName() {
  const name = location.hash.replace(/^#/, "");
  return TABS.includes(name) ? name : TABS[0];
}

// Guards against two overlapping showTab() calls finishing out of order
// (e.g. a rapid double tab-switch) stomping on each other's mount -- the
// dynamic import() below is an async gap a second call can land inside.
let tabGeneration = 0;

async function showTab(name) {
  const generation = ++tabGeneration;
  if (currentTabHandle && typeof currentTabHandle.unmount === "function") {
    currentTabHandle.unmount();
  }
  currentTabHandle = null;
  tabRootEl.innerHTML = "";
  for (const button of navEl.children) {
    button.classList.toggle("active", button.dataset.tab === name);
  }
  try {
    const mod = await import(`./tabs/${name}.js?v=${ASSET_VERSION}`);
    if (generation !== tabGeneration) return; // superseded while importing
    currentTabHandle = mod.mount(tabRootEl, buildCtx()) || null;
  } catch (err) {
    if (generation !== tabGeneration) return;
    console.error(`Failed to load tab "${name}":`, err);
    tabRootEl.append(el("p", { class: "tab-status" }, `Could not load this tab: ${err.message}`));
  }
}

function setupNav() {
  for (const name of TABS) {
    navEl.append(
      el(
        "button",
        {
          "data-tab": name,
          onclick: () => {
            location.hash = `#${name}`;
          },
        },
        TAB_LABELS[name]
      )
    );
  }
  window.addEventListener("hashchange", () => showTab(currentTabName()));
}

function setupIdentityControls() {
  const savedManualId = readStorage("panem_discord_id");
  if (savedManualId) {
    state.manualDiscordId = savedManualId;
    discordIdInput.value = savedManualId;
  }
  discordIdInput.addEventListener("change", async () => {
    state.manualDiscordId = discordIdInput.value.trim();
    writeStorage("panem_discord_id", state.manualDiscordId);
    // Deliberately doesn't remount the current tab afterward: refreshIdentity
    // is a network round trip, and remounting once it resolves would wipe
    // out anything the player typed into the currently-open tab in the
    // meantime. ctx.discordId() is a live read, so the open tab already
    // sees the new id for any action it takes next -- switching tabs (or
    // back) picks up a fresh character list right away, same as any other
    // tab visit.
    await refreshIdentity();
  });
  characterSelect.addEventListener("change", async () => {
    state.characterId = Number(characterSelect.value);
    writeStorage("panem_character_id", String(state.characterId));
    await showTab(currentTabName());
  });
}

async function main() {
  setupNav();
  setupIdentityControls();

  state.discordUser = await authenticateWithDiscord();
  if (!state.discordUser) {
    discordIdInput.hidden = false;
  }

  await refreshIdentity();
  await showTab(currentTabName());
}

main();
