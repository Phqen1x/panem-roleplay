// The "Games" tab: a staff-only pitch/catalog for the "Panem Party Pack" --
// a set of very short multiplayer games meant for parties, festivals and
// homes, plus a list of board/card games that playable tables (in homes,
// cafes, club rooms) could offer. Nothing here is wired up to real
// gameplay yet -- see each card's own note. Only offered by `app.js` when
// `/identify` reports `is_staff` (same gating as `staff.js`), while the
// concept is reviewed before any of it is built for players.
import { el } from "./_shared.js?v=5";

const PARTY_PACK_GAMES = [
  {
    name: "Capitol Says",
    description:
      "A Simon Says-style reaction game. Players select the correct action before time expires.",
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
  },
  {
    name: "Pass the Parcel",
    description:
      "Players pass a package while music plays. It may contain a prize, prank item or challenge.",
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

function introPanel() {
  return el(
    "div",
    { class: "panel" },
    el("h2", { text: "Panem Party Pack" }),
    el(
      "p",
      { class: "tab-status" },
      "A collection of very short multiplayer games accessible from parties, festivals and homes. " +
        "Concept catalog only -- staff-visible for now while it's reviewed, with minimal artwork " +
        "planned so each one can become reusable across dozens of events."
    )
  );
}

function gameCard(game) {
  return el(
    "div",
    { class: "game-card" },
    el("h3", { text: game.name }),
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

export function mount(root) {
  root.append(introPanel(), partyPackPanel(), boardAndCardPanel());
}
