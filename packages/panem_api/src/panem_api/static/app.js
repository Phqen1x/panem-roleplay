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
import { fetchJson, el, renderTabIcon, renderIcon, dropdown } from "./tabs/_shared.js?v=13";
import { mountThemePicker } from "./theme_picker.js?v=4";
import { initClickSFX } from "./click_sfx.js?v=53";
import { mountSoundPicker } from "./sound_picker.js?v=53";
import { updateAmbientContext } from "./ambient_player.js?v=53";

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

// `/activity/config`'s `activity_public_url`, captured once in
// `authenticateWithDiscord()` -- see `toSameOriginUrl()`'s own docstring
// for why this exists. Stays "" (a no-op) if that fetch fails or the
// server has no `ACTIVITY_PUBLIC_URL` configured, same as `discordSdk`
// staying null outside a real Activity.
let activityPublicUrl = "";

// Turns an absolute, `ACTIVITY_PUBLIC_URL`-based link (an uploaded
// avatar's `Character.avatar_url`/`Npc.avatar_url`, the same string
// Discord's webhook posting needs verbatim and so can't be stored as a
// relative path instead) into a page-relative one. Discord serves the
// Activity's HTML from its own `*.discordsays.com` origin and only
// proxies *relative*-path requests back to this app's real domain --
// an absolute URL naming that real domain explicitly is still cross-
// origin from the iframe's own point of view and gets blocked by its
// CSP, even though it's "our own" server. A URL that doesn't start with
// `activityPublicUrl` (a genuine third-party link someone pasted into
// the avatar URL field, or a local `blob:` preview) is returned
// untouched -- there's nothing this trick can do for an actual
// different host, and Discord's CSP still blocks those same as before.
function toSameOriginUrl(url) {
  if (!url || typeof url !== "string" || !activityPublicUrl) return url;
  const base = activityPublicUrl.replace(/\/+$/, "");
  if (!url.startsWith(base)) return url;
  const rest = url.slice(base.length);
  return rest.startsWith("/") ? rest : `/${rest}`;
}

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
const ASSET_VERSION = "70";

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
  "pets",
  "hobbies",
  "museum",
  "weather",
  "bulletin",
  "rumors",
  "events",
  "reactions",
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
  pets: "Pets",
  hobbies: "Hobbies",
  museum: "Museum",
  weather: "Weather",
  bulletin: "Bulletin",
  rumors: "Rumors",
  events: "Events",
  reactions: "Reactions",
};

// The mobile bottom dock's curated shortcuts -- everything else (every
// tab in TABS, grouped) is still one tap away via Home's own app grid,
// the same "a few pinned apps in the dock, the rest behind the home
// screen" shape a phone's own launcher uses. Desktop has no dock at all
// (the sidebar already lists every tab), so this is read only by
// `setupDock()`.
const DOCK_TABS = ["home", "map", "events", "character"];
const DOCK_TAB_LABELS = {
  home: "Home",
  map: "Map",
  events: "Activities",
  character: "Character",
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

// The "Panem Party Pack" games catalog -- staff-only for now while the
// pitch/roadmap is reviewed, same gating as STAFF_TAB above. Once the
// individual games are actually built and ready for players, this should
// move into TABS/TAB_LABELS like any other player-facing tab.
const GAMES_TAB = "games";
const GAMES_TAB_LABEL = "Games";
const STAFF_ONLY_TABS = [STAFF_TAB, HISTORY_TAB, GAMES_TAB];
const STAFF_ONLY_TAB_LABELS = {
  [STAFF_TAB]: STAFF_TAB_LABEL,
  [HISTORY_TAB]: HISTORY_TAB_LABEL,
  [GAMES_TAB]: GAMES_TAB_LABEL,
};

const statusEl = document.getElementById("status");
const navEl = document.getElementById("tab-nav");
const dockEl = document.getElementById("tab-dock");
const tabRootEl = document.getElementById("tab-root");
const discordIdInput = document.getElementById("discord-id-input");
const characterToggleEl = document.getElementById("character-select-toggle");
const characterMenuEl = document.getElementById("character-select-menu");
const themePickerEl = document.getElementById("theme-picker");
const worldClockTimeEl = document.getElementById("world-clock-time");
const worldClockDateEl = document.getElementById("world-clock-date");
const worldClockShiftEl = document.getElementById("world-clock-shift");
const profileButtonEl = document.getElementById("profile-button");
const profileButtonAvatarEl = document.getElementById("profile-button-avatar");

const state = {
  discordUser: null,
  manualDiscordId: "",
  characters: [],
  characterId: null,
  isStaff: false,
  isDonor: false,
  // Bare tab names (e.g. "map") this account shouldn't see right now --
  // `/identify`'s `hidden_tabs`, already resolved server-side against
  // `feature_flags` + `is_staff` (`dashboard_routes._hidden_tabs`). See
  // `visibleTabs()`/`setupDock()` below for where this is applied.
  hiddenTabs: [],
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

// Per-viewer "frequently/recently used" tracking for Home's own app
// shortcuts (`tabs/home.js`) -- never shared, never read back by
// anything but this same browser, same spirit as the theme colors
// mirrored into localStorage above. Read through `ctx.tabUsage()`
// rather than home.js touching `localStorage` itself, so this stays the
// one place usage is actually recorded (`showTab()` below).
const TAB_USAGE_STORAGE_KEY = "panem_tab_usage_v1";

function loadTabUsage() {
  try {
    const raw = readStorage(TAB_USAGE_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && parsed.counts && Array.isArray(parsed.recent)) return parsed;
  } catch {
    // Malformed/blocked storage -- fall through to a fresh, empty record.
  }
  return { counts: {}, recent: [] };
}

function recordTabUsage(name) {
  if (name === "home") return; // the home screen itself isn't "an app" to rank
  const usage = loadTabUsage();
  usage.counts[name] = (usage.counts[name] || 0) + 1;
  usage.recent = [name, ...usage.recent.filter((n) => n !== name)].slice(0, 8);
  writeStorage(TAB_USAGE_STORAGE_KEY, JSON.stringify(usage));
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
    ({ client_id: clientId, activity_public_url: activityPublicUrl } =
      await fetchJson("/activity/config"));
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
  const districtId = character
    ? (character.current_district_id ?? character.district_id ?? 1)
    : 1;
  const locationId = character ? (character.location_id || null) : null;
  updateAmbientContext({ district_id: districtId, location_id: locationId });

  const telemetryEl = document.getElementById("footer-telemetry");
  if (!telemetryEl) return;
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

// The header's faceclaim button -- `state.characters` (the `/identify`
// summary) already carries `avatar_url`, so this needs no fetch of its
// own; it's just kept in sync with whichever character is selected.
// `profileButtonPlaceholderEl` (the generic "user" glyph, appended once
// in `main()`) shows instead whenever there's no character selected yet
// or the selected one has no faceclaim.
let profileButtonPlaceholderEl = null;

function updateProfileButtonAvatar(character) {
  if (!profileButtonAvatarEl) return;
  const url = character && character.avatar_url ? toSameOriginUrl(character.avatar_url) : null;
  if (url) {
    profileButtonAvatarEl.src = url;
    profileButtonAvatarEl.hidden = false;
    if (profileButtonPlaceholderEl) profileButtonPlaceholderEl.hidden = true;
  } else {
    profileButtonAvatarEl.hidden = true;
    profileButtonAvatarEl.removeAttribute("src");
    if (profileButtonPlaceholderEl) profileButtonPlaceholderEl.hidden = false;
  }
}

async function selectCharacter(id) {
  closeCharacterMenu();
  if (id === state.characterId) return;
  state.characterId = id;
  // Any pop-up still showing belonged to the character being switched
  // away from -- answering it now would silently apply to the wrong one.
  if (reactionOverlay) reactionOverlay.close();
  if (profileOverlay) profileOverlay.close();
  writeStorage("panem_character_id", String(id));
  const chosen = state.characters.find((c) => c.id === id);
  if (chosen) {
    characterToggleEl.textContent = characterLabel(chosen);
  }
  updateTelemetry(chosen);
  updateProfileButtonAvatar(chosen);
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
    updateProfileButtonAvatar(null);
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
  updateProfileButtonAvatar(selected);
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
    state.hiddenTabs = [];
    state.theme = DEFAULT_THEME;
    state.themeProfiles = [];
    state.activeThemeProfileId = null;
    applyTheme(state.theme);
    themePickerEl.hidden = true;
    renderCharacterOptions();
    setupNav();
    setupDock();
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
    state.hiddenTabs = body.hidden_tabs || [];
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
    state.hiddenTabs = [];
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
  setupDock();
}

function buildCtx() {
  return {
    discordId: getDiscordId,
    characterId: () => state.characterId,
    characters: () => state.characters,
    currentCharacter: currentCharacter,
    districtNames: DISTRICT_NAMES,
    districtMotto: (id) =>
      state.districtMottos[String(id)] || DEFAULT_DISTRICT_MOTTOS[String(id)] || "Excellence Endures",
    districtName: (id) => DISTRICT_NAMES[id] || `District ${id}`,
    apiFetch: fetchJson,
    mediaUrl: toSameOriginUrl,
    formatTick: formatSimMoment,
    formatDay: formatSimDay,
    refreshIdentity,
    openExternalLink,
    // Below: what `tabs/home.js` needs to render its own "Apps"/
    // "Frequently used"/"Recently opened" shortcuts without duplicating
    // app.js's own TABS/TAB_LABELS/usage-tracking internals.
    visibleTabs: () => visibleTabs().filter((name) => name !== "home"),
    tabLabel: (name) => STAFF_ONLY_TAB_LABELS[name] || TAB_LABELS[name] || name,
    tabUsage: loadTabUsage,
    goToTab: (name) => {
      location.hash = `#${name}`;
    },
  };
}

function visibleTabs() {
  const base = state.isStaff ? [...TABS, ...STAFF_ONLY_TABS] : TABS;
  return base.filter((name) => !state.hiddenTabs.includes(name));
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
  recordTabUsage(name);
  if (currentTabHandle && typeof currentTabHandle.unmount === "function") {
    currentTabHandle.unmount();
  }
  currentTabHandle = null;
  tabRootEl.innerHTML = "";
  for (const button of navEl.children) {
    button.classList.toggle("active", button.dataset.tab === name);
  }
  for (const button of dockEl.children) {
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

function navButton(name, active, labelOverride = null) {
  const icon = renderTabIcon(name);
  const label = el("span", {}, labelOverride || STAFF_ONLY_TAB_LABELS[name] || TAB_LABELS[name]);
  return el(
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
  );
}

function setupNav() {
  if (navBuiltForStaff === state.isStaff) return;
  navBuiltForStaff = state.isStaff;
  navEl.innerHTML = "";
  const active = currentTabName();
  for (const name of visibleTabs()) {
    navEl.append(navButton(name, active));
  }
}

// The mobile bottom dock -- a fixed, small set of shortcuts (`DOCK_TABS`),
// never staff-gated, so unlike `setupNav()` it only ever needs building
// once rather than re-running on every identity refresh.
let dockBuilt = false;
function setupDock() {
  if (dockBuilt) return;
  dockBuilt = true;
  dockEl.innerHTML = "";
  const active = currentTabName();
  // Unlike `visibleTabs()` this never staff-gates (see DOCK_TABS' own
  // comment), but it still has to respect an admin's `hidden_tabs` --
  // otherwise a disabled tab (e.g. Map, mid-beta) would stay one tap away
  // here even though the nav bar and Home's app grid both hide it.
  for (const name of DOCK_TABS.filter((n) => !state.hiddenTabs.includes(n))) {
    dockEl.append(navButton(name, active, DOCK_TAB_LABELS[name]));
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
// re-fetched alongside identity/character refreshes. The poll just
// re-syncs a baseline (tick + when it was persisted); `renderWorldClock`
// below runs far more often than that to interpolate the displayed
// time/shift smoothly in between polls, mirroring `panem_shared.simtime`'s
// own tick -> hour/day/month/phase math client-side (see that module for
// the server version this must stay in sync with).
const WORLD_TIME_POLL_INTERVAL_MS = 60_000;
const WORLD_CLOCK_RENDER_INTERVAL_MS = 1000;
const PHASE_ORDER = ["night", "morning", "afternoon", "evening"];

let worldTimeBaseline = null; // { tick, updatedAtMs, tickIntervalSeconds, ticksPerDay, daysPerMonth }

function capitalize(word) {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

// Fallbacks for `panem_shared.constants.TICKS_PER_DAY`/`DAYS_PER_MONTH` --
// only used for the brief window before `refreshWorldTime`'s first
// `/world/time` response lands (a tab mounted immediately at startup could
// otherwise call `formatSimMoment` with no baseline yet); once it lands,
// the real (possibly `tuning.yaml`-overridden) values from the server take
// over.
const DEFAULT_TICKS_PER_DAY = 24;
const DEFAULT_DAYS_PER_MONTH = 30;

// Converts an absolute tick (e.g. a `WeatherEvent.predicted_start_tick`, a
// bulletin entry's `event_tick`) into the same "simulation time" shape the
// world-clock header renders for *now* -- mirrors `panem_shared.simtime`'s
// tick -> hour/day/month/phase math exactly like `renderWorldClock` below
// does, but for an arbitrary tick rather than the live, interpolated one.
// Used wherever a tab used to show a bare "tick 1234" to players, who have
// no reason to know what a "tick" is.
function formatSimMoment(tick) {
  const ticksPerDay = worldTimeBaseline ? worldTimeBaseline.ticksPerDay : DEFAULT_TICKS_PER_DAY;
  const daysPerMonth = worldTimeBaseline ? worldTimeBaseline.daysPerMonth : DEFAULT_DAYS_PER_MONTH;

  const hourOfDay = tick % ticksPerDay;
  const dayIndex = Math.floor(tick / ticksPerDay);
  const day = (dayIndex % daysPerMonth) + 1;
  const month = (Math.floor(dayIndex / daysPerMonth) % 12) + 1;
  const year = Math.floor(dayIndex / (daysPerMonth * 12)) + 1;

  const totalMinutes = (hourOfDay * 24 * 60) / ticksPerDay;
  const hour24 = Math.floor(totalMinutes / 60) % 24;
  const minute = Math.floor(totalMinutes % 60);
  const period = hour24 < 12 ? "AM" : "PM";
  const hour12 = hour24 % 12 || 12;
  const timeStr = `${hour12}:${String(minute).padStart(2, "0")} ${period}`;

  const ticksPerPhase = ticksPerDay / PHASE_ORDER.length;
  const phaseIndex = Math.min(PHASE_ORDER.length - 1, Math.floor(hourOfDay / ticksPerPhase));
  const phase = PHASE_ORDER[phaseIndex];

  return `${timeStr} on Month ${month}, Day ${day}, Year ${year} (${capitalize(phase)} shift)`;
}

// A date-only sibling of `formatSimMoment` above for displays keyed to a
// whole sim *day* rather than one exact moment (e.g. the Weather tab's
// 10-day forecast, one row per `WeatherForecast.day_index` with no
// specific hour of its own) -- same month/day/year math, minus the
// time-of-day/shift half nothing on a given day needs yet.
function formatSimDay(dayIndex) {
  const daysPerMonth = worldTimeBaseline ? worldTimeBaseline.daysPerMonth : DEFAULT_DAYS_PER_MONTH;
  const day = (dayIndex % daysPerMonth) + 1;
  const month = (Math.floor(dayIndex / daysPerMonth) % 12) + 1;
  const year = Math.floor(dayIndex / (daysPerMonth * 12)) + 1;
  return `Month ${month}, Day ${day}, Year ${year}`;
}

function renderWorldClock() {
  if (!worldTimeBaseline) return;
  const { tick, updatedAtMs, tickIntervalSeconds, ticksPerDay, daysPerMonth } = worldTimeBaseline;

  const elapsedSeconds = (Date.now() - updatedAtMs) / 1000;
  // Clamped below 1 full tick's worth of progress: if the sim has
  // stalled (or is just running behind), this holds the display just
  // shy of the next hour instead of sprinting arbitrarily far into a
  // "future" the sim never actually reached.
  const elapsedTicks = Math.max(0, Math.min(elapsedSeconds / tickIntervalSeconds, 0.999999));
  const fractionalTick = tick + elapsedTicks;

  const hourOfDay = fractionalTick % ticksPerDay;
  const dayIndex = Math.floor(fractionalTick / ticksPerDay);
  const day = (dayIndex % daysPerMonth) + 1;
  const month = (Math.floor(dayIndex / daysPerMonth) % 12) + 1;
  const year = Math.floor(dayIndex / (daysPerMonth * 12)) + 1;

  const totalMinutes = (hourOfDay * 24 * 60) / ticksPerDay;
  const hour24 = Math.floor(totalMinutes / 60) % 24;
  const minute = Math.floor(totalMinutes % 60);
  const period = hour24 < 12 ? "AM" : "PM";
  const hour12 = hour24 % 12 || 12;
  const timeStr = `${hour12}:${String(minute).padStart(2, "0")} ${period}`;

  const ticksPerPhase = ticksPerDay / PHASE_ORDER.length;
  const phaseIndex = Math.min(
    PHASE_ORDER.length - 1,
    Math.floor(hourOfDay / ticksPerPhase)
  );
  const phase = PHASE_ORDER[phaseIndex];

  worldClockTimeEl.innerHTML = `Simulation Time: <strong>${timeStr}</strong>`;
  worldClockDateEl.innerHTML =
    `Simulation Date: <strong>Month ${month}, Day ${day}, Year ${year}</strong>`;
  worldClockShiftEl.innerHTML = `Current Shift: <strong>${capitalize(phase)}</strong>`;
}

async function refreshWorldTime() {
  try {
    const time = await fetchJson("/world/time");
    worldTimeBaseline = {
      tick: time.tick,
      updatedAtMs: Date.parse(time.updated_at),
      tickIntervalSeconds: time.tick_interval_seconds,
      ticksPerDay: time.ticks_per_day,
      daysPerMonth: time.days_per_month,
    };
    renderWorldClock();
  } catch (err) {
    console.warn("Could not load world time:", err);
  }
}

// Reaction-event pop-ups (`panem_shared.reactions`) -- no more DM from a
// Discord-side background loop; the *only* place a fresh one ever gets
// rolled now is this poll's own call to `/activity/dashboard/reactions/
// .../status` (see that route's docstring), which only runs while this
// page is actually open, so "pops up occasionally while the Activity is
// open" falls out of polling itself rather than needing its own
// presence check. Floats above whichever tab is currently showing --
// unlike the Reactions tab's own (read-only) archetype panel, this isn't
// scoped to that one tab, since a pop-up can land while the player is on
// any of them. `REACTION_EVENT_DASHBOARD_POLL_SECONDS`'s own docstring
// is the other half of this interval's math -- keep the two in sync.
const REACTIONS_POLL_INTERVAL_MS = 5000;
let reactionOverlay = null;
let reactionPollInFlight = false;

// Tracks whether this browser has ever had the Getting Started guide
// auto-shown, same localStorage convention as `TAB_USAGE_STORAGE_KEY`
// above -- per-viewer only, never read back by anything but this same
// browser. Bump the `_v1` suffix if the guide's content changes enough
// that returning players should see it again automatically.
const GUIDE_SEEN_STORAGE_KEY = "panem_guide_seen_v5";
let guideOverlay = null;
let profileOverlay = null;

// Plain static HTML (not user-supplied, so no escaping concerns) for each
// page of the guide pop-up -- condensed from the same command list
// `/help` builds live from the bot's command tree, so this needs
// updating by hand if a whole new category of command ships, but isn't
// expected to drift command-by-command the way `/help` would. One array
// entry per page, each rendered as its own `h3` + body by
// `buildGuideOverlay`'s `renderPage()` below.
const GUIDE_PAGES = [
  {
    heading: "Getting started",
    bodyHtml: `
      <ol>
        <li>Create a character: <code>/character create district:&lt;your district&gt;</code></li>
        <li>You'll be asked to pick a district, roleplay mode, gender, and other character
          details (name, age, etc.) -- all changeable later.</li>
        <li>Once approved by staff, you're in! Use <code>/help</code> any time for a full,
          always-up-to-date list of commands.</li>
      </ol>
    `,
  },
  {
    heading: "📍 The basics",
    bodyHtml: `
      <ul>
        <li><code>/where</code> -- see where your character currently is</li>
        <li><code>/time</code> -- check the in-world day and time</li>
        <li><code>/travel</code> -- move around your district, or board a train to another one</li>
        <li><code>/resident list</code> -- see the NPCs living in your district</li>
      </ul>
    `,
  },
  {
    heading: "💰 Working &amp; money",
    bodyHtml: `
      <ul>
        <li><code>/work</code> -- work your currently open shift</li>
        <li><code>/market prices</code> -- check what's selling in your district</li>
        <li><code>/inventory</code> -- see what your character is carrying</li>
        <li><code>/pay</code> -- send money to another player's character</li>
        <li><code>/trade</code> -- offer, accept, or cancel a trade with another player</li>
        <li><code>/housing</code> -- buy, rent, or manage a place to live</li>
      </ul>
    `,
  },
  {
    heading: "🎭 Roleplay",
    bodyHtml: `
      <ul>
        <li><code>/scene start</code> -- kick off a roleplay scene at a location</li>
        <li><code>/engage</code> -- start or join a group RP with players</li>
        <li><code>/rp</code> -- enable roleplay mode for your character in a channel</li>
      </ul>
    `,
  },
  {
    heading: "🍞 Needs (Simulation mode)",
    bodyHtml: `
      <p>Keep your character alive and well:</p>
      <ul>
        <li><code>/eat</code>, <code>/drink</code>, <code>/heal</code>, <code>/energize</code>,
          <code>/entertain</code>, <code>/use</code></li>
      </ul>
    `,
  },
  {
    heading: "⚠️ Crime &amp; risk (use with caution!)",
    bodyHtml: `
      <ul>
        <li><code>/steal</code> -- pickpocket a player or NPC</li>
        <li><code>/burgle</code> -- break into someone's house</li>
        <li><code>/poach</code> -- poach food at the district outskirts</li>
        <li><code>/shipment</code> -- rob a contraband shipment</li>
        <li><code>/blackmarket</code> -- trade illicit goods, if the black market NPC trusts you</li>
        <li><code>/crimelog</code> -- see your recent crime attempts</li>
        <li>Caught? <code>/bail</code> pays your way out, or try <code>/lockpick</code> to escape
          jail yourself.</li>
      </ul>
    `,
  },
  {
    heading: "🕹️ The Activity",
    bodyHtml: `
      <p>This server has a full embedded Discord <strong>Activity</strong> -- a dashboard
        mirroring every command above, playable right inside Discord. To launch it: click the
        apps icon next to the emoji button, find <strong>Panem RPG</strong>, and click
        "Launch".</p>
    `,
  },
  {
    heading: "🧭 Finding your way around in the activity",
    bodyHtml: `
      <ul>
        <li>The <strong>sidebar</strong> (or the bottom dock, on mobile) jumps straight to any
          tab -- Character, Vitals, Work, Market, Travel, Social, Jail, Crime, Housing.</li>
        <li><strong>Home</strong> is your dashboard -- its app grid holds every tab that isn't
          pinned to the dock, plus your recently-opened ones.</li>
        <li>The <strong>character switcher</strong> in the top bar swaps which of your
          characters you're playing.</li>
        <li>The <strong>world clock</strong> in the top bar always shows the current in-world
          day, time, and shift.</li>
        <li>Click the <strong>?</strong> button up top any time to see this guide again.</li>
      </ul>
      <p><strong>Need the full command list any time? Just run <code>/help</code>.</strong></p>
    `,
  },
];

function buildGuideOverlay() {
  const overlay = el("div", { class: "modal-overlay guide-modal-overlay" });
  const closeBtn = el("button", { class: "btn-close-modal", text: "×", onclick: close });
  const bodyEl = el("div", { class: "guide-modal-body" });
  const pageIndicatorEl = el("span", { class: "guide-page-indicator" });
  const prevBtn = el("button", { class: "btn secondary", type: "button", text: "Prev" });
  const nextBtn = el("button", { class: "btn primary", type: "button" });
  const footerEl = el(
    "div",
    { class: "guide-modal-footer" },
    pageIndicatorEl,
    el("div", { class: "guide-modal-footer-buttons" }, prevBtn, nextBtn)
  );
  const card = el(
    "div",
    { class: "guide-modal-card", onclick: (event) => event.stopPropagation() },
    el(
      "div",
      { class: "dossier-header" },
      el("div", {}, el("div", { class: "dossier-name", text: "Panem Roleplay" })),
      closeBtn
    ),
    bodyEl,
    footerEl
  );
  overlay.append(card);
  overlay.addEventListener("click", close);

  let pageIndex = 0;

  function renderPage() {
    const page = GUIDE_PAGES[pageIndex];
    bodyEl.innerHTML = `<h3>${page.heading}</h3>${page.bodyHtml}`;
    bodyEl.scrollTop = 0;
    pageIndicatorEl.textContent = `${pageIndex + 1} / ${GUIDE_PAGES.length}`;
    prevBtn.disabled = pageIndex === 0;
    const isLastPage = pageIndex === GUIDE_PAGES.length - 1;
    nextBtn.textContent = isLastPage ? "Finish" : "Next";
  }

  prevBtn.addEventListener("click", () => {
    if (pageIndex > 0) {
      pageIndex -= 1;
      renderPage();
    }
  });
  nextBtn.addEventListener("click", () => {
    if (pageIndex < GUIDE_PAGES.length - 1) {
      pageIndex += 1;
      renderPage();
    } else {
      close();
    }
  });

  function close() {
    overlay.classList.remove("open");
  }

  function show() {
    pageIndex = 0;
    renderPage();
    overlay.classList.add("open");
  }

  return { element: overlay, show, close };
}

// The header's own faceclaim button opens this -- everything submitted
// when the current character was created (age, gender, occupation,
// appearance, backstory, faceclaim), fetched fresh from `GET .../
// characters` each time it's shown rather than cached, since `state.
// characters` (the `/identify` summary) doesn't carry most of these
// fields at all. Mirrors `tabs/social.js`'s `dossierModal` (same
// `.dossier-*` markup/CSS, same "user" row shape) -- that one is for
// *other* players' characters, reached from the Social tab; this is
// always the viewer's own, reached from anywhere via the header.
const PROFILE_GENDER_OPTIONS = [
  { value: "", label: "Unspecified" },
  { value: "male", label: "Male" },
  { value: "female", label: "Female" },
  { value: "nonbinary", label: "Non-binary" },
];

function buildProfileOverlay() {
  const overlay = el("div", { class: "modal-overlay profile-modal-overlay" });
  const avatarEl = el("div", { class: "dossier-avatar", text: "?" });
  const avatarImgEl = el("img", { class: "dossier-avatar-img", alt: "" });
  avatarImgEl.hidden = true;
  const nameEl = el("div", { class: "dossier-name", text: "Profile" });
  const jobEl = el("div", { class: "dossier-job", text: "" });
  const closeBtn = el("button", { class: "btn-close-modal", text: "×", onclick: close });
  const statusEl = el("p", { class: "tab-status" });

  // --- view mode: the read-only profile (unchanged from before editing
  // existed) -- the "Edit" button lives in this body, not the header,
  // so it's as easy to hide (a non-approved character, or a load
  // failure) as every other row here.
  const editBtn = el("button", { class: "btn secondary", type: "button", text: "Edit" });
  editBtn.hidden = true;
  const districtEl = el("span", {});
  const ageGenderEl = el("span", {});
  const rpModeEl = el("span", {});
  const appearanceEl = el("span", {});
  const backstoryEl = el("span", {});
  const viewBody = el(
    "div",
    { class: "dossier-body" },
    statusEl,
    el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "District" }), districtEl),
    el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Age & Gender" }), ageGenderEl),
    el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "RP Mode" }), rpModeEl),
    el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Appearance" }), appearanceEl),
    el("div", { class: "dossier-prop" }, el("span", { class: "dossier-prop-label", text: "Backstory" }), backstoryEl),
    el("div", { class: "dossier-prop" }, editBtn)
  );

  // --- edit mode: the same fields creation collects, prefilled with
  // what's currently live. Submitting never applies anything here --
  // `POST .../request-edit` stages it for staff approval, same as
  // `/profile edit` in Discord (see that command's own docstring).
  const nameInput = el("input", { type: "text", maxlength: "32" });
  const ageInput = el("input", { type: "number", min: "12", max: "89" });
  const genderSelect = dropdown(PROFILE_GENDER_OPTIONS);
  const jobInput = el("input", { type: "text", maxlength: "80" });
  const appearanceInput = el("textarea", { rows: "2", maxlength: "400" });
  const backstoryInput = el("textarea", { rows: "3", maxlength: "1500" });

  // Avatar upload UI mirrors `tabs/character.js`'s creation form exactly
  // (same endpoint, same upload-then-fill-the-URL-field flow) -- kept
  // local to this overlay rather than imported, since nothing here needs
  // the layered appearance-editor machinery that form also carries.
  const avatarUrlInput = el("input", {
    type: "text",
    placeholder: "https://... or choose a file below -- leave blank to keep your current one",
  });
  const avatarFileInput = el("input", {
    type: "file",
    class: "avatar-file-hidden",
    accept: "image/png,image/jpeg,image/webp,image/gif",
  });
  const avatarFileLabel = el(
    "label",
    { class: "btn secondary avatar-file-label" },
    el("span", { text: "Choose picture" }),
    avatarFileInput
  );
  const avatarFileName = el("span", { class: "avatar-file-name", text: "No file chosen" });
  const avatarUploadBtn = el("button", { class: "btn secondary", type: "button" }, "Upload picture");
  avatarUploadBtn.disabled = true;
  const avatarUploadResult = el("span", { class: "result-line" });

  let chosenFile = null;
  avatarFileInput.addEventListener("change", () => {
    const file = avatarFileInput.files && avatarFileInput.files[0];
    if (!file) return;
    chosenFile = file;
    avatarFileName.className = "avatar-file-name has-file";
    avatarFileName.textContent = `Selected: ${file.name}`;
    avatarUploadBtn.disabled = false;
    avatarUploadResult.textContent = "";
  });

  async function uploadSelectedImage() {
    const file = chosenFile || (avatarFileInput.files && avatarFileInput.files[0]);
    if (!file) return null;
    avatarUploadBtn.disabled = true;
    avatarUploadResult.className = "result-line";
    avatarUploadResult.textContent = "Uploading…";
    const formData = new FormData();
    formData.append("discord_id", String(getDiscordId()));
    formData.append("file", file);
    try {
      const res = await fetchJson("/activity/dashboard/characters/avatar-upload", {
        method: "POST",
        body: formData,
      });
      avatarUrlInput.value = res.avatar_url || "";
      avatarFileName.className = "avatar-file-name is-uploaded";
      avatarFileName.textContent = `✓ Uploaded: ${file.name}`;
      avatarUploadResult.className = "result-line win";
      avatarUploadResult.textContent = "Uploaded and ready!";
      return res.avatar_url;
    } catch (err) {
      avatarUploadBtn.disabled = false;
      avatarUploadResult.className = "result-line lose";
      avatarUploadResult.textContent = err.message;
      throw err;
    }
  }
  avatarUploadBtn.addEventListener("click", () => {
    uploadSelectedImage().catch(() => {});
  });

  const editResultEl = el("p", { class: "result-line" });
  const submitBtn = el("button", { class: "btn", type: "button", text: "Submit for approval" });
  const cancelBtn = el("button", { class: "btn secondary", type: "button", text: "Cancel" });
  const editBody = el(
    "div",
    { class: "dossier-body profile-edit-form" },
    el("div", { class: "field-row" }, el("label", { text: "Name" }), nameInput),
    el("div", { class: "field-row" }, el("label", { text: "Age" }), ageInput),
    el("div", { class: "field-row" }, el("label", { text: "Gender" }), genderSelect),
    el("div", { class: "field-row" }, el("label", { text: "Occupation" }), jobInput),
    el("div", { class: "field-row" }, el("label", { text: "Appearance" }), appearanceInput),
    el("div", { class: "field-row" }, el("label", { text: "Backstory" }), backstoryInput),
    el(
      "div",
      { class: "field-row" },
      el("label", { text: "Faceclaim" }),
      el(
        "div",
        { class: "avatar-upload-box" },
        el("div", { class: "avatar-upload-row" }, avatarFileLabel, avatarUploadBtn, avatarFileName),
        avatarUploadResult
      )
    ),
    el("div", { class: "field-row" }, el("label", { text: "Or image URL" }), avatarUrlInput),
    editResultEl,
    el("div", { class: "field-row" }, submitBtn, cancelBtn)
  );
  editBody.hidden = true;

  const card = el(
    "div",
    { class: "dossier-card", onclick: (event) => event.stopPropagation() },
    el(
      "div",
      { class: "dossier-header" },
      el("div", { class: "dossier-header-info" }, avatarEl, avatarImgEl, el("div", {}, nameEl, jobEl)),
      closeBtn
    ),
    viewBody,
    editBody
  );
  overlay.append(card);
  overlay.addEventListener("click", close);

  let lastDetail = null;

  function close() {
    overlay.classList.remove("open");
  }

  function resetAvatarUpload() {
    chosenFile = null;
    avatarFileInput.value = "";
    avatarFileName.className = "avatar-file-name";
    avatarFileName.textContent = "No file chosen";
    avatarUploadBtn.disabled = true;
    avatarUploadResult.textContent = "";
  }

  function reset() {
    viewBody.hidden = false;
    editBody.hidden = true;
    editBtn.hidden = true;
    avatarEl.hidden = false;
    avatarImgEl.hidden = true;
    nameEl.textContent = "Profile";
    jobEl.textContent = "";
    districtEl.textContent = "—";
    ageGenderEl.textContent = "—";
    rpModeEl.textContent = "—";
    appearanceEl.textContent = "—";
    backstoryEl.textContent = "—";
    avatarUrlInput.value = "";
    resetAvatarUpload();
    editResultEl.textContent = "";
  }

  function enterEditMode() {
    if (!lastDetail) return;
    nameInput.value = lastDetail.name;
    ageInput.value = String(lastDetail.age);
    genderSelect.value = lastDetail.gender || "";
    jobInput.value = lastDetail.job_title || "";
    appearanceInput.value = lastDetail.appearance || "";
    backstoryInput.value = lastDetail.backstory || "";
    avatarUrlInput.value = "";
    resetAvatarUpload();
    editResultEl.textContent = "";
    viewBody.hidden = true;
    editBody.hidden = false;
  }

  editBtn.addEventListener("click", enterEditMode);
  cancelBtn.addEventListener("click", () => {
    viewBody.hidden = false;
    editBody.hidden = true;
  });

  submitBtn.addEventListener("click", async () => {
    if (!lastDetail) return;
    const discordId = getDiscordId();
    editResultEl.className = "result-line";
    editResultEl.textContent = "";
    const name = nameInput.value.trim();
    const age = Number(ageInput.value);
    if (!name || !Number.isInteger(age)) {
      editResultEl.className = "result-line lose";
      editResultEl.textContent = "Name and a valid age are required.";
      return;
    }
    submitBtn.disabled = true;
    try {
      let finalAvatarUrl = avatarUrlInput.value.trim() || lastDetail.avatar_url;
      if (chosenFile && !avatarUrlInput.value.trim()) {
        try {
          finalAvatarUrl = (await uploadSelectedImage()) || lastDetail.avatar_url;
        } catch {
          submitBtn.disabled = false;
          return;
        }
      }
      await fetchJson(`/activity/dashboard/characters/${lastDetail.id}/request-edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          discord_id: discordId,
          name,
          age,
          gender: genderSelect.value || null,
          appearance: appearanceInput.value,
          backstory: backstoryInput.value,
          job_title: jobInput.value.trim() || null,
          avatar_url: finalAvatarUrl,
        }),
      });
      editResultEl.className = "result-line win";
      editResultEl.textContent =
        "Submitted for staff approval -- your current profile stays active until then.";
    } catch (err) {
      editResultEl.className = "result-line lose";
      editResultEl.textContent = err.message;
    } finally {
      submitBtn.disabled = false;
    }
  });

  async function show() {
    reset();
    overlay.classList.add("open");
    const character = currentCharacter();
    const discordId = getDiscordId();
    if (!character || !discordId) {
      statusEl.textContent = "Pick a character above first.";
      return;
    }
    statusEl.textContent = "Loading…";
    try {
      const body = await fetchJson(
        `/activity/dashboard/characters?discord_id=${encodeURIComponent(discordId)}`
      );
      const detail = (body.characters || []).find((c) => c.id === character.id);
      if (!detail) throw new Error("Character not found.");
      lastDetail = detail;
      statusEl.textContent = "";
      avatarEl.textContent = detail.name.charAt(0);
      nameEl.textContent = detail.name;
      const jobLabel =
        detail.job_title || (detail.rp_mode === "story" ? "No occupation (Story mode)" : "Unemployed");
      jobEl.textContent = jobLabel;
      districtEl.textContent = detail.district_name || "—";
      ageGenderEl.textContent = [detail.age, detail.gender].filter(Boolean).join(", ") || "—";
      rpModeEl.textContent = detail.rp_mode
        ? detail.rp_mode.charAt(0).toUpperCase() + detail.rp_mode.slice(1)
        : "—";
      appearanceEl.textContent = detail.appearance || "No appearance description given.";
      backstoryEl.textContent = detail.backstory || "No backstory given.";
      if (detail.avatar_url) {
        avatarImgEl.src = toSameOriginUrl(detail.avatar_url);
        avatarImgEl.hidden = false;
        avatarEl.hidden = true;
      }
      // Editing only makes sense for an already-approved character --
      // a pending/retired/dead one has nothing live to stage changes
      // against the same way.
      editBtn.hidden = detail.status !== "approved";
    } catch (err) {
      statusEl.textContent = `Could not load profile: ${err.message}`;
    }
  }

  return { element: overlay, show, close };
}

function buildReactionOverlay() {
  const overlay = el("div", { class: "modal-overlay reaction-modal-overlay" });
  const promptEl = el("p", { class: "tab-status" });
  const countdownEl = el("p", { class: "tab-status" });
  const buttonsHost = el("div", { class: "field-row" });
  const resultEl = el("p", { class: "result-line" });
  const card = el(
    "div",
    { class: "reaction-modal-card", onclick: (event) => event.stopPropagation() },
    el("h2", { text: "Something's Happening" }),
    promptEl,
    countdownEl,
    buttonsHost,
    resultEl
  );
  overlay.append(card);

  let currentLogId = null;
  let countdownTimer = null;
  let answered = false;

  function stopCountdown() {
    if (countdownTimer) {
      clearInterval(countdownTimer);
      countdownTimer = null;
    }
  }

  function close() {
    stopCountdown();
    overlay.classList.remove("open");
    currentLogId = null;
  }

  function renderCountdown(expiresAtMs) {
    stopCountdown();
    const tick = () => {
      const secondsLeft = Math.max(0, Math.round((expiresAtMs - Date.now()) / 1000));
      countdownEl.textContent =
        secondsLeft > 0
          ? `You have about ${secondsLeft}s left to answer.`
          : "This pop-up is about to expire.";
      if (secondsLeft <= 0) {
        stopCountdown();
        if (!answered) close();
      }
    };
    tick();
    countdownTimer = setInterval(tick, 1000);
  }

  async function choose(choice, buttons) {
    if (answered) return;
    answered = true;
    for (const button of buttons) button.disabled = true;
    try {
      const result = await fetchJson(`/activity/dashboard/reactions/${state.characterId}/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_id: getDiscordId(), choice }),
      });
      resultEl.className = "result-line win";
      resultEl.textContent = `Reading now: ${result.archetype_label} (score ${result.archetype_score.toFixed(1)}).`;
      stopCountdown();
      setTimeout(close, 2500);
    } catch (err) {
      resultEl.className = "result-line lose";
      resultEl.textContent = err.message;
      answered = false;
      for (const button of buttons) button.disabled = false;
    }
  }

  function show(pending) {
    // Already showing this exact pop-up (or mid-answer on it) -- a later
    // poll finding the same still-pending log shouldn't re-render out
    // from under an in-progress click, or restart its countdown.
    if (currentLogId === pending.log_id && overlay.classList.contains("open")) return;

    answered = false;
    currentLogId = pending.log_id;
    promptEl.textContent = pending.prompt;
    resultEl.textContent = "";
    resultEl.className = "result-line";
    buttonsHost.innerHTML = "";
    const buttons = [];
    for (const option of pending.options) {
      const button = el("button", { class: "btn", type: "button" }, option.label);
      button.addEventListener("click", () => choose(option.choice, buttons));
      buttons.push(button);
      buttonsHost.append(button);
    }
    renderCountdown(Date.now() + pending.expires_in_seconds * 1000);
    overlay.classList.add("open");
  }

  return { element: overlay, show, close };
}

async function refreshReactions() {
  if (!state.characterId || !getDiscordId() || reactionPollInFlight) return;
  reactionPollInFlight = true;
  try {
    const status = await fetchJson(
      `/activity/dashboard/reactions/${state.characterId}/status?discord_id=${encodeURIComponent(getDiscordId())}`
    );
    if (status.pending && reactionOverlay) {
      reactionOverlay.show(status.pending);
    }
  } catch (err) {
    console.warn("Could not poll reaction status:", err);
  } finally {
    reactionPollInFlight = false;
  }
}

async function main() {
  initClickSFX();
  const soundPickerEl = document.getElementById("sound-picker");
  if (soundPickerEl) {
    mountSoundPicker(soundPickerEl);
  }

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
  setInterval(renderWorldClock, WORLD_CLOCK_RENDER_INTERVAL_MS);

  reactionOverlay = buildReactionOverlay();
  document.body.append(reactionOverlay.element);
  refreshReactions();
  setInterval(refreshReactions, REACTIONS_POLL_INTERVAL_MS);

  guideOverlay = buildGuideOverlay();
  document.body.append(guideOverlay.element);
  const guideButtonEl = document.getElementById("guide-button");
  if (guideButtonEl) {
    guideButtonEl.addEventListener("click", () => guideOverlay.show());
  }
  if (!readStorage(GUIDE_SEEN_STORAGE_KEY)) {
    guideOverlay.show();
    writeStorage(GUIDE_SEEN_STORAGE_KEY, "1");
  }

  profileOverlay = buildProfileOverlay();
  document.body.append(profileOverlay.element);
  if (profileButtonEl) {
    if (profileButtonAvatarEl) {
      profileButtonPlaceholderEl = renderIcon("user", 16);
      profileButtonEl.append(profileButtonPlaceholderEl);
    }
    profileButtonEl.addEventListener("click", () => profileOverlay.show());
  }

  state.discordUser = await authenticateWithDiscord();
  if (!state.discordUser) {
    discordIdInput.hidden = false;
  }

  await refreshIdentity();
  await showTab(currentTabName());
}

main();
