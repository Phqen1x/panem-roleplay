// The "Games" tab: a staff-only pitch/catalog for the "Panem Party Pack" --
// a set of very short multiplayer games meant for parties, festivals and
// homes, plus a list of board/card games that playable tables (in homes,
// cafes, club rooms) could offer. Three of the nine are wired up for
// real: `playable: true` cards link into the "Party Room"
// (`party.js`) -- a shared top-down space you walk a simple character
// around in and play Quickfire Trivia, Capitol Says, and Pass the Parcel
// as stations in it (see that file's own docstring). The rest stay a
// pitch for now. Only offered by `app.js` when `/identify` reports
// `is_staff` (same gating as `staff.js`), while the concept is reviewed
// before any of it is built out further for players.
import { el } from "./_shared.js?v=7";

// Bump whenever party.js changes -- same cache-busting reasoning as
// app.js's own ASSET_VERSION (see that file's module docstring), just
// scoped to this one dynamically-imported module since it isn't part of
// app.js's own tabs/*.js router.
const PARTY_ASSET_VERSION = "3";

const PARTY_PACK_GAMES = [
  {
    name: "Capitol Says",
    description:
      "A Simon Says-style reaction game. Players select the correct action before time expires.",
    playable: true,
  },
  {
    name: "District Draw",
    description: "One player draws a district object while everyone else guesses.",
  },
  {
    name: "Who Am I?",
    description:
      "Players receive a Panem occupation, historical figure, animal or object and ask yes/no questions.",
  },
  {
    name: "Quickfire Trivia",
    description: "Categories could include district lore, Games history and general trivia.",
    playable: true,
  },
  {
    name: "Pass the Parcel",
    description:
      "Players pass a package while music plays. It may contain a prize, prank item or challenge.",
    playable: true,
  },
  {
    name: "Memory Table",
    description: "Several objects appear, disappear, and players identify what changed.",
  },
  {
    name: "Harvest Scramble",
    description: "Players rapidly sort crops into the correct baskets.",
  },
  {
    name: "Outfit Relay",
    description: "Teams assemble an outfit matching a surprise theme.",
  },
  {
    name: "Don't Wake the Peacekeeper",
    description: "Players take turns removing items from a pile without filling the noise meter.",
  },
];

const BOARD_AND_CARD_GAMES = [
  "Checkers",
  "Chess",
  "Dominoes",
  "Matching cards",
  "Bluffing games",
  "Cooperative mystery cards",
  "District-themed property game",
  "Trivia",
  "Simple trading-card battles",
  "Dice games",
  "Word games",
  "Puzzle races",
];

function introPanel(onEnterPartyRoom) {
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Panem Party Pack" }),
    el(
      "p",
      { class: "tab-status" },
      "A collection of very short multiplayer games accessible from parties, festivals and homes. " +
        "Staff-visible for now while the concept is reviewed, with minimal artwork planned so each " +
        "one can become reusable across dozens of events. Three are already playable in a shared " +
        "top-down Party Room -- look for the ▶ Playable badge below."
    ),
    el(
      "div",
      { class: "field-row" },
      el(
        "button",
        { class: "btn primary", type: "button", onclick: onEnterPartyRoom },
        "Enter the Party Room"
      )
    )
  );
}

function gameCard(game) {
  return el(
    "div",
    { class: "game-card" },
    el(
      "h3",
      {},
      game.name,
      game.playable ? el("span", { class: "game-card-badge" }, "▶ Playable") : null
    ),
    el("p", { class: "tab-status" }, game.description)
  );
}

function partyPackPanel() {
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Quick Party Games" }),
    el("div", { class: "game-card-grid" }, ...PARTY_PACK_GAMES.map(gameCard))
  );
}

function boardAndCardPanel() {
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Board and Card Games" }),
    el(
      "p",
      { class: "tab-status" },
      "Homes, cafes and club rooms could contain playable tables. Possible games:"
    ),
    el(
      "ul",
      { class: "game-list" },
      ...BOARD_AND_CARD_GAMES.map((name) => el("li", {}, name))
    )
  );
}

function catalogView(onEnterPartyRoom) {
  return el(
    "div",
    {},
    introPanel(onEnterPartyRoom),
    partyPackPanel(),
    boardAndCardPanel()
  );
}

export function mount(root, ctx) {
  let currentHandle = null;

  function showCatalog() {
    if (currentHandle && typeof currentHandle.unmount === "function") currentHandle.unmount();
    currentHandle = null;
    root.innerHTML = "";
    root.append(catalogView(showPartyRoom));
  }

  async function showPartyRoom() {
    if (currentHandle && typeof currentHandle.unmount === "function") currentHandle.unmount();
    root.innerHTML = "";
    root.append(
      el(
        "div",
        { class: "field-row" },
        el("button", { class: "btn secondary", type: "button", onclick: showCatalog }, "← Back to catalog")
      )
    );
    try {
      const mod = await import(`./party.js?v=${PARTY_ASSET_VERSION}`);
      currentHandle = mod.mount(root, ctx) || null;
    } catch (err) {
      root.append(
        el("p", { class: "tab-status error" }, `Could not load the Party Room: ${err.message}`)
      );
    }
  }

  showCatalog();

  return {
    unmount() {
      if (currentHandle && typeof currentHandle.unmount === "function") currentHandle.unmount();
      currentHandle = null;
    },
  };
}
