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
import { fetchJson, el, renderTabIcon } from "./tabs/_shared.js?v=7";
import { mountThemePicker } from "./theme_picker.js?v=4";

// `?v=N`, same cache-busting convention as every other asset this page
// loads (see the `tabs/_shared.js`/`theme_picker.js` imports above) --
// this one never had it, so a 502 Discord's Activity proxy cached for
// this exact bare URL during an outage has no way to get invalidated
// short of the proxy's own cache expiring on its own.
const DISCORD_SDK_URL = "/vendor/discord-embedded-app-sdk.js?v=1";
const STEP_TIMEOUT_MS = 8000;

// Set once `authenticateWithDiscord()` gets past `discordSdk.ready()` --
// null outside a real Discord Activity (plain browser preview mode),
// which `openExternalLink()` below falls back around.
let discordSdk = null;

// Deep links (e.g. "Continue in Discord" -> a specific channel/thread)
// can't just be a plain `<a target="_blank">`: the Activity iframe is
// sandboxed and silently swallows that navigation instead of opening
// anything (Discord's own embedded-app-sdk exists specifically to route
// this kind of thing back out to the real client). Falls back to a plain
// new-tab open outside a real Activity, where there's no sandbox to route
// around and no `discordSdk` to route through.
async function openExternalLink(url) {
  if (discordSdk && discordSdk.commands && discordSdk.commands.openExternalLink) {
    try {
      await discordSdk.commands.openExternalLink({ url });
      return;
    } catch (err) {
      console.warn("discordSdk.commands.openExternalLink failed, falling back:", err);
    }
  }
  window.open(url, "_blank", "noopener");
}

// Bumped whenever any file under tabs/ changes -- matches work.js's/
// crime.js's own single-constant-for-a-whole-module-group convention.
const ASSET_VERSION = "47";

// District names mapping for Capitol and Districts 1-12
const DISTRICT_NAMES = {
  0: "The Capitol",
  1: "District One",
  2: "District Two",
  3: "District Three",
  4: "District Four",
  5: "District Five",
  6: "District Six",
  7: "District Seven",
  8: "District Eight",
  9: "District Nine",
  10: "District Ten",
  11: "District Eleven",
  12: "District Twelve",
};

// District mottos fallback defaults
const DEFAULT_DISTRICT_MOTTOS = {
  "0": "Panem Today, Panem Tomorrow, Panem Forever",
  "1": "Excellence Endures",
  "2": "Strength in Stone and Iron",
  "3": "Knowledge Lights the Dark",
  "4": "From the Deep, We Rise",
  "5": "Powering Panem's Light",
  "6": "Moving Panem Forward",
  "7": "From Strong Roots, Resilient Wood",
  "8": "Woven with Precision and Pride",
  "9": "Grain of the Republic",
  "10": "Guarding the Heartland Herds",
  "11": "Through Hardship, Strength Blooms",
  "12": "From the Dark, Pure Fire",
};

// Mirrors `panem_shared.theme`'s `DEFAULT_BACKGROUND_HEX`/`DEFAULT_ACCENT_
// HEX`/`DEFAULT_PANEL_HEX`/`DEFAULT_TEXT_HEX`, which themselves mirror
// `style.css`'s `:root` values -- what a non-donor, a never-customized
// donor, or a reset renders. Duplicated here (rather than fetched) so the
// dashboard never has to round-trip to the server just to know its own
// default colors.
const DEFAULT_THEME = {
  background_hex: "#0a0c10",
  accent_hex: "#c5a059",
  panel_hex: "#12161f",
  text_hex: "#f1f3f7",
  profile_id: null,
};

const TABS = [
  "home",
  "map",
  "character",
  "vitals",
  "work",
  "market",
  "travel",
  "social",
  "jail",
  "crime",
  "housing",
];
const TAB_LABELS = {
  home: "Home",
  map: "Map",
  character: "Character",
  vitals: "Vitals",
  work: "Work",
  market: "Market",
  travel: "Travel",
  social: "Social",
  jail: "Jail",
  crime: "Crime",
  housing: "Housing",
};

// Only offered when `/identify` reports `is_staff` -- see `visibleTabs()`.
// Not part of `TABS`/`TAB_LABELS` above: those two arrays are also used as
// "every tab a plain player can reach" wherever that distinction matters
// (currently just `currentTabName()`'s fallback below).
const STAFF_TAB = "staff";
const STAFF_TAB_LABEL = "Staff";
// The District History tab -- staff-only district lore/context NPC
// dialogue draws a short summary from (`panem_shared.district_lore`).
// Kept separate from STAFF_TAB rather than folded into it: it's its own
// large, district-scoped editor, not another admin-panel panel.
const HISTORY_TAB = "history";
const HISTORY_TAB_LABEL = "History";

const statusEl = document.getElementById("status");
const navEl = document.getElementById("tab-nav");
const tabRootEl = document.getElementById("tab-root");
const discordIdInput = document.getElementById("discord-id-input");
const characterToggleEl = document.getElementById("character-select-toggle");
const characterMenuEl = document.getElementById("character-select-menu");
const themePickerEl = document.getElementById("theme-picker");
const worldClockTimeEl = document.getElementById("world-clock-time");
const worldClockDateEl = document.getElementById("world-clock-date");

const state = {
  discordUser: null,
  manualDiscordId: "",
  characters: [],
  characterId: null,
  isStaff: false,
  isDonor: false,
  theme: DEFAULT_THEME,
  themeProfiles: [],
  // The account's general/default profile id (independent of whatever the
  // *currently selected character* resolves to, which can differ -- see
  // `dashboard_routes._resolve_theme`'s "character overrides general"
  // order). Tracked separately so the picker can always show "Default"
  // correctly even while a character-specific profile is what's applied.
  activeThemeProfileId: null,
  districtMottos: { ...DEFAULT_DISTRICT_MOTTOS },
};

let currentTabHandle = null;
// Only rebuilds the nav bar's DOM when staff status actually changes --
// `refreshIdentity()` re-runs `setupNav()` every time (including from the
// manual Discord ID field's change handler, which deliberately doesn't
// remount the current tab -- see its own comment), and a full nav rebuild
// on every one of those calls would silently drop the "active" class
// `showTab()` sets on the current tab's button until the next tab switch.
let navBuiltForStaff = null;

function setStatus(text) {
  if (!text || text.toLowerCase().startsWith("connected")) {
    statusEl.textContent = "";
    statusEl.hidden = true;
    statusEl.classList.remove("is-notice");
    return;
  }
  // Every message this ever shows is a connection/auth problem the player
  // can work around (preview mode, a failed Discord handshake) -- never a
  // benign "all good" note, since those clear to empty above instead.
  // `.is-notice` (style.css) marks it as a formal notice instead of
  // leaving it looking like ordinary muted status text.
  statusEl.textContent = text;
  statusEl.hidden = false;
  statusEl.classList.add("is-notice");
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

// Sets the four CSS custom properties `style.css`'s `:root` defines
// (`--bg`, `--accent`, `--panel`, `--text`) live -- both the donor theme
// picker's drag preview and the actual resolved theme go through this one
// function. Also mirrors the values to localStorage so `work.html`/
// `crime.html` (same-origin iframes the dashboard's Work/Jail/Crime tabs
// embed, with no Discord identity of their own to ask `/identify`
// themselves) pick up the same colors -- the same "app.js writes, another
// same-origin page reads" convention `panem_character_id` already
// established.
function applyTheme(theme) {
  document.documentElement.style.setProperty("--bg", theme.background_hex);
  document.documentElement.style.setProperty("--accent", theme.accent_hex);
  document.documentElement.style.setProperty("--panel", theme.panel_hex);
  document.documentElement.style.setProperty("--text", theme.text_hex);
  writeStorage("panem_theme_bg", theme.background_hex);
  writeStorage("panem_theme_accent", theme.accent_hex);
  writeStorage("panem_theme_panel", theme.panel_hex);
  writeStorage("panem_theme_text", theme.text_hex);
}

function currentCharacter() {
  return state.characters.find((c) => c.id === state.characterId) || null;
}

// Keeps `state.characters` in sync after an assign/unassign resolves, so
// the picker's "Use for <character>"/"Stop using for <character>" toggle
// (which reads `getCharacter().themeProfileId`) reflects the change
// immediately without waiting for the next `/identify`.
function patchCharacterThemeProfile(characterId, profileId) {
  const character = state.characters.find((c) => c.id === characterId);
  if (character) character.theme_profile_id = profileId;
}

async function themeApiPost(path, body) {
  return fetchJson(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Re-resolves the effective theme for the current context (the selected
// character's own override if it has one, else the account's general
// default, else the plain default -- `dashboard_routes._resolve_theme`)
// and applies it live. Called whenever that context changes: the selected
// character, or a fresh `/identify`.
async function refreshResolvedTheme() {
  if (!state.isDonor) return;
  const discordId = getDiscordId();
  if (!discordId) return;
  try {
    const resolved = await themeApiPost("/activity/dashboard/theme/resolve", {
      discord_id: discordId,
      character_id: state.characterId,
    });
    if (resolved && resolved.profile_id === null && resolved.accent_hex === "#e0a72e") {
      state.theme = DEFAULT_THEME;
    } else {
      state.theme = resolved || DEFAULT_THEME;
    }
  } catch (err) {
    console.warn("Could not resolve dashboard theme:", err);
    state.theme = DEFAULT_THEME;
  }
  applyTheme(state.theme);
  if (themePickerHandle) themePickerHandle.refresh();
}

let themePickerHandle = null;

function setupThemePicker() {
  if (themePickerHandle) return;
  themePickerHandle = mountThemePicker(themePickerEl, {
    getTheme: () => state.theme,
    getProfiles: () => state.themeProfiles,
    getActiveProfileId: () => state.activeThemeProfileId,
    getCharacter: () => {
      const character = currentCharacter();
      return character
        ? { id: character.id, name: character.name, themeProfileId: character.theme_profile_id ?? null }
        : null;
    },
    onPreview: applyTheme,
    onSaveNew: async ({ name, background_hex, accent_hex, panel_hex, text_hex }) => {
      const discordId = getDiscordId();
      if (!discordId) throw new Error("no Discord id");
      const profile = await themeApiPost("/activity/dashboard/theme/profiles", {
        discord_id: discordId,
        name,
        background_hex,
        accent_hex,
        panel_hex,
        text_hex,
      });
      state.themeProfiles = [...state.themeProfiles, profile];
      return profile;
    },
    onUpdate: async (id, { name, background_hex, accent_hex, panel_hex, text_hex }) => {
      const discordId = getDiscordId();
      if (!discordId) throw new Error("no Discord id");
      const profile = await fetchJson(`/activity/dashboard/theme/profiles/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: discordId,
          name,
          background_hex,
          accent_hex,
          panel_hex,
          text_hex,
        }),
      });
      state.themeProfiles = state.themeProfiles.map((p) => (p.id === id ? profile : p));
      return profile;
    },
    onDelete: async (id) => {
      const discordId = getDiscordId();
      if (!discordId) throw new Error("no Discord id");
      const list = await themeApiPost(`/activity/dashboard/theme/profiles/${id}/delete`, {
        discord_id: discordId,
      });
      state.themeProfiles = list.profiles;
      state.activeThemeProfileId = list.active_profile_id;
      for (const character of state.characters) {
        if (character.theme_profile_id === id) character.theme_profile_id = null;
      }
      // Whatever was applied might have just been deleted out from under
      // the current context -- re-resolve rather than guess what it fell
      // back to.
      await refreshResolvedTheme();
    },
    onActivate: async (id) => {
      const discordId = getDiscordId();
      if (!discordId) throw new Error("no Discord id");
      await themeApiPost(`/activity/dashboard/theme/profiles/${id}/activate`, {
        discord_id: discordId,
      });
      state.activeThemeProfileId = id;
      await refreshResolvedTheme();
    },
    onAssign: async (id, characterId) => {
      const discordId = getDiscordId();
      if (!discordId) throw new Error("no Discord id");
      await themeApiPost(`/activity/dashboard/theme/profiles/${id}/assign`, {
        discord_id: discordId,
        character_id: characterId,
      });
      patchCharacterThemeProfile(characterId, id);
      if (state.characterId === characterId) await refreshResolvedTheme();
    },
    onUnassign: async (characterId) => {
      const discordId = getDiscordId();
      if (!discordId) throw new Error("no Discord id");
      await themeApiPost("/activity/dashboard/theme/unassign", {
        discord_id: discordId,
        character_id: characterId,
      });
      patchCharacterThemeProfile(characterId, null);
      if (state.characterId === characterId) await refreshResolvedTheme();
    },
    onReset: async () => {
      const discordId = getDiscordId();
      if (!discordId) throw new Error("no Discord id");
      const character = currentCharacter();
      await themeApiPost("/activity/dashboard/theme/reset", {
        discord_id: discordId,
        character_id: character ? character.id : null,
      });
      if (character) patchCharacterThemeProfile(character.id, null);
      else state.activeThemeProfileId = null;
      await refreshResolvedTheme();
    },
  });
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

// The embedded-app-sdk's own RPC failures (e.g. a rejected
// `commands.authorize()`) come back as plain `{code, message}` objects, not
// `Error` instances -- `String(err)` on those is just "[object Object]",
// which is exactly the unhelpful text this used to send to both the status
// banner and the server log. Pull out whatever's actually there instead.
function describeError(err) {
  if (err instanceof Error) return err.message;
  if (err && typeof err === "object") {
    const parts = [];
    if ("code" in err) parts.push(`code ${err.code}`);
    if ("message" in err && err.message) parts.push(String(err.message));
    if (parts.length > 0) return parts.join(": ");
    try {
      const json = JSON.stringify(err);
      if (json && json !== "{}") return json;
    } catch {
      // Fall through to the generic String() below.
    }
  }
  return String(err);
}

async function reportClientError(step, err) {
  try {
    await fetch("/activity/debug", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        step,
        message: describeError(err),
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
    discordSdk = new DiscordSDK(clientId);

    STEP.current = "discordSdk.ready()";
    await withTimeout(discordSdk.ready(), STEP.current);

    // `commands.authorize()` is what shows Discord's consent popup -- and an
    // access token obtained from it stays valid (per Discord's own OAuth2
    // token lifetime, independent of this page/iframe) well beyond a single
    // Activity launch. Skipping straight to `commands.authenticate()` with a
    // token cached from a previous launch, and only falling back to a fresh
    // `authorize()` if that cached token no longer works (expired/revoked),
    // is what avoids re-prompting the player on every single launch -- the
    // previous code discarded `accessToken` right after using it once,
    // so every launch ran the full consent flow from a blank slate.
    // `identify` is a low-sensitivity scope (just id/username/avatar, same
    // as what any command's autocomplete already sees), so caching it in
    // localStorage is a reasonable tradeoff given this process's already-
    // documented lack of a stronger session layer (see this file's own
    // module docstring, and dashboard_routes.py's).
    const cachedToken = readStorage("panem_discord_access_token");
    if (cachedToken) {
      try {
        STEP.current = "commands.authenticate() (cached token)";
        const cachedResult = await withTimeout(
          discordSdk.commands.authenticate({ access_token: cachedToken }),
          STEP.current
        );
        setStatus("");
        return cachedResult?.user ?? null;
      } catch (err) {
        console.warn("Cached Discord token no longer works, re-authorizing:", err);
      }
    }

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
    writeStorage("panem_discord_access_token", accessToken);

    STEP.current = "commands.authenticate()";
    const authResult = await withTimeout(
      discordSdk.commands.authenticate({ access_token: accessToken }),
      STEP.current
    );

    setStatus("");
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
        `(${describeError(err)}). Enter a Discord ID below to try the ` +
        "dashboard anyway; check the panem_api server log for details."
    );
    discordSdk = null; // handshake didn't complete -- openExternalLink() falls back to window.open
    return null;
  }
}

function characterLabel(character) {
  return character.jailed ? `${character.name} (jailed)` : character.name;
}

function closeCharacterMenu() {
  characterMenuEl.hidden = true;
}

function updateTelemetry(character) {
  const telemetryEl = document.getElementById("footer-telemetry");
  if (!telemetryEl) return;
  const districtId = character
    ? (character.current_district_id ?? character.district_id ?? 1)
    : 1;
  const districtName =
    (character && (character.current_district_name || character.district_name)) ||
    DISTRICT_NAMES[districtId] ||
    `District ${districtId}`;
  const motto =
    state.districtMottos[String(districtId)] ||
    DEFAULT_DISTRICT_MOTTOS[String(districtId)] ||
    "Excellence Endures";
  telemetryEl.innerHTML = `${districtName} <span class="accent">&#9671;</span> ${motto}`;
}

async function selectCharacter(id) {
  closeCharacterMenu();
  if (id === state.characterId) return;
  state.characterId = id;
  writeStorage("panem_character_id", String(id));
  const chosen = state.characters.find((c) => c.id === id);
  if (chosen) {
    characterToggleEl.textContent = characterLabel(chosen);
  }
  updateTelemetry(chosen);
  // A donor's theme can be assigned per-character -- switching characters
  // may mean switching the applied colors too.
  await refreshResolvedTheme();
  await showTab(currentTabName());
}

function renderCharacterOptions() {
  characterMenuEl.innerHTML = "";
  closeCharacterMenu();
  if (state.characters.length === 0) {
    characterToggleEl.disabled = true;
    characterToggleEl.textContent = "No characters";
    state.characterId = null;
    updateTelemetry(null);
    return;
  }
  characterToggleEl.disabled = false;
  const savedId = Number(readStorage("panem_character_id"));
  const stillValid = state.characters.some((c) => c.id === savedId);
  state.characterId = stillValid ? savedId : state.characters[0].id;
  for (const character of state.characters) {
    const item = el("li", {}, [
      el(
        "button",
        {
          type: "button",
          class: character.id === state.characterId ? "active" : "",
          onclick: () => selectCharacter(character.id),
        },
        characterLabel(character)
      ),
    ]);
    characterMenuEl.append(item);
  }
  const selected = state.characters.find((c) => c.id === state.characterId);
  characterToggleEl.textContent = selected ? characterLabel(selected) : "No characters";
  updateTelemetry(selected);
}

async function refreshIdentity() {
  const statusBadge = document.getElementById("discord-status-text");
  if (statusBadge) {
    statusBadge.textContent = state.discordUser ? "Connected via Discord" : "Preview Mode";
  }
  const discordId = getDiscordId();
  if (!discordId) {
    state.characters = [];
    state.isStaff = false;
    state.isDonor = false;
    state.theme = DEFAULT_THEME;
    state.themeProfiles = [];
    state.activeThemeProfileId = null;
    applyTheme(state.theme);
    themePickerEl.hidden = true;
    renderCharacterOptions();
    setupNav();
    return;
  }
  try {
    const body = await fetchJson("/activity/dashboard/identify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ discord_id: discordId }),
    });
    state.characters = body.characters;
    state.isStaff = Boolean(body.is_staff);
    state.isDonor = Boolean(body.is_donor);
    // `body.theme` here has no character context (`/identify` doesn't
    // know which one's selected) -- it's the account's general theme, so
    // its `profile_id` is exactly the account's general default.
    if (!state.isDonor || (body.theme && body.theme.profile_id === null && body.theme.accent_hex === "#e0a72e")) {
      state.theme = DEFAULT_THEME;
    } else {
      state.theme = body.theme || DEFAULT_THEME;
    }
    state.themeProfiles = body.theme_profiles || [];
    state.activeThemeProfileId = state.theme.profile_id ?? null;
  } catch (err) {
    console.warn("Could not load characters:", err);
    state.characters = [];
    state.isStaff = false;
    state.isDonor = false;
    state.theme = DEFAULT_THEME;
    state.themeProfiles = [];
    state.activeThemeProfileId = null;
  }
  applyTheme(state.theme);
  themePickerEl.hidden = !state.isDonor;
  renderCharacterOptions(); // may set state.characterId from localStorage
  if (state.isDonor) {
    setupThemePicker();
    // Now that the selected character (if any) is known, re-resolve for
    // that specific context -- it may differ from the general theme above.
    await refreshResolvedTheme();
  }
  setupNav();
}

function buildCtx() {
  return {
    discordId: getDiscordId,
    characterId: () => state.characterId,
    characters: () => state.characters,
    districtNames: DISTRICT_NAMES,
    districtMotto: (id) =>
      state.districtMottos[String(id)] || DEFAULT_DISTRICT_MOTTOS[String(id)] || "Excellence Endures",
    districtName: (id) => DISTRICT_NAMES[id] || `District ${id}`,
    apiFetch: fetchJson,
    refreshIdentity,
    openExternalLink,
  };
}

function visibleTabs() {
  return state.isStaff ? [...TABS, STAFF_TAB, HISTORY_TAB] : TABS;
}

function currentTabName() {
  const name = location.hash.replace(/^#/, "");
  if (name) {
    return visibleTabs().includes(name) ? name : TABS[0];
  }
  // No hash yet -- this is the activity's default landing tab, not a
  // player-chosen navigation. A player with no characters at all has
  // nothing for Home to show (it's all per-character state), so send
  // them straight to Character to make one instead. Only applies to this
  // no-hash landing case -- explicitly opening #home (e.g. the nav
  // button) is always respected, characters or not.
  return state.characters.length === 0 ? "character" : TABS[0];
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
    tabRootEl.append(
      el(
        "div",
        { class: "panel tab-load-error" },
        el("h2", { text: "This tab couldn't load" }),
        el("p", { class: "tab-status error" }, err.message),
        el(
          "p",
          { class: "tab-status" },
          "Try switching tabs again, or reload the Activity if it keeps happening."
        )
      )
    );
  }
}

function setupNav() {
  if (navBuiltForStaff === state.isStaff) return;
  navBuiltForStaff = state.isStaff;
  navEl.innerHTML = "";
  const active = currentTabName();
  for (const name of visibleTabs()) {
    const icon = renderTabIcon(name);
    const label = el(
      "span",
      {},
      name === STAFF_TAB
        ? STAFF_TAB_LABEL
        : name === HISTORY_TAB
          ? HISTORY_TAB_LABEL
          : TAB_LABELS[name]
    );
    navEl.append(
      el(
        "button",
        {
          "data-tab": name,
          class: name === active ? "active" : "",
          onclick: () => {
            location.hash = `#${name}`;
          },
        },
        icon,
        label
      )
    );
  }
}

function setupIdentityControls() {
  const savedManualId = readStorage("panem_discord_id");
  if (savedManualId) {
    state.manualDiscordId = savedManualId;
    discordIdInput.value = savedManualId;
  }
  discordIdInput.addEventListener("change", async () => {
    const next = discordIdInput.value.trim();
    // A real click on the character dropdown blurs this field first (focus
    // moves to the toggle button), and a browser fires "change" on blur
    // whenever the field's value differs from what it was when it *gained*
    // focus -- which, since this field is filled programmatically rather
    // than typed into fresh each time, fires again here even though nothing
    // actually changed. Without this guard that spuriously re-runs
    // refreshIdentity() -> renderCharacterOptions(), which closes the
    // dropdown menu it was rebuilding right as the same click opens it.
    if (next === state.manualDiscordId) return;
    state.manualDiscordId = next;
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
  characterToggleEl.addEventListener("click", () => {
    if (characterToggleEl.disabled) return;
    characterMenuEl.hidden = !characterMenuEl.hidden;
  });
  document.addEventListener("click", (event) => {
    if (!characterMenuEl.hidden && !event.composedPath().includes(document.getElementById("character-select"))) {
      closeCharacterMenu();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeCharacterMenu();
  });
}

async function loadDistrictMottos() {
  try {
    const data = await fetchJson("/district_mottos.json");
    if (data && typeof data === "object") {
      state.districtMottos = { ...DEFAULT_DISTRICT_MOTTOS, ...data };
      updateTelemetry(currentCharacter());
    }
  } catch (err) {
    console.warn("Could not load district mottos:", err);
  }
}

// The world clock is global sim state, not per-player -- `/world/time`
// needs no discord_id and is polled on a plain interval rather than
// re-fetched alongside identity/character refreshes.
const WORLD_TIME_POLL_INTERVAL_MS = 60_000;

async function refreshWorldTime() {
  try {
    const time = await fetchJson("/world/time");
    worldClockTimeEl.innerHTML = `Simulation Time: <strong>${time.time}</strong>`;
    worldClockDateEl.innerHTML =
      `Simulation Date: <strong>Month ${time.month}, Day ${time.day}, Year ${time.year}</strong>`;
  } catch (err) {
    console.warn("Could not load world time:", err);
  }
}

async function main() {
  setupIdentityControls();
  window.addEventListener("hashchange", () => showTab(currentTabName()));
  window.addEventListener("panem:motto-updated", (event) => {
    if (event.detail && event.detail.district_id !== undefined && event.detail.motto) {
      state.districtMottos[String(event.detail.district_id)] = event.detail.motto;
      updateTelemetry(currentCharacter());
    }
  });

  loadDistrictMottos();
  refreshWorldTime();
  setInterval(refreshWorldTime, WORLD_TIME_POLL_INTERVAL_MS);

  state.discordUser = await authenticateWithDiscord();
  if (!state.discordUser) {
    discordIdInput.hidden = false;
  }

  await refreshIdentity();
  await showTab(currentTabName());
}

main();
