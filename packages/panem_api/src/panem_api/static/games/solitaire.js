// Simplified Klondike solitaire. Click-based (no drag/drop): click a
// movable card to select it, then click the pile you want to move it to.
// Only the top face-up card of a pile is ever movable -- a deliberate
// simplification over full run-dragging, kept because "select then place"
// is the only interaction model this minigame needs to support.
//
// Win: all four foundations completed (Ace..King). Lose: the player gives
// up (a genuinely unwinnable Klondike deal can happen, and detecting that
// automatically is out of scope here) via the "Give Up" button -- reported
// as `{ neutral: true }` since, unlike every other game's loss, this one
// isn't necessarily the player's own misplay or bad luck.

const SUITS = ["S", "H", "D", "C"];
const SUIT_SYMBOL = { S: "♠", H: "♥", D: "♦", C: "♣" };
const RED_SUITS = new Set(["H", "D"]);
const RANK_LABEL = { 1: "A", 11: "J", 12: "Q", 13: "K" };

export const label = "Solitaire";
export function instructions() {
  return "Klondike -- click a card, then click where to move it. Build foundations Ace to King.";
}

function rankLabel(rank) {
  return RANK_LABEL[rank] || String(rank);
}

function isRed(suit) {
  return RED_SUITS.has(suit);
}

function buildDeck() {
  const deck = [];
  for (const suit of SUITS) {
    for (let rank = 1; rank <= 13; rank++) {
      deck.push({ suit, rank, faceUp: false });
    }
  }
  for (let i = deck.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [deck[i], deck[j]] = [deck[j], deck[i]];
  }
  return deck;
}

export function mount(boardEl, { onFinish }) {
  const deck = buildDeck();
  const tableau = Array.from({ length: 7 }, () => []);
  for (let col = 0; col < 7; col++) {
    for (let row = 0; row <= col; row++) {
      const card = deck.pop();
      card.faceUp = row === col;
      tableau[col].push(card);
    }
  }
  const stock = deck;
  const waste = [];
  const foundations = { S: [], H: [], D: [], C: [] };
  let selected = null; // { type: "tableau" | "waste", index }
  let gameOver = false;

  function sourcePile(sel) {
    if (sel.type === "tableau") return tableau[sel.index];
    if (sel.type === "waste") return waste;
    return null;
  }

  function isWon() {
    return SUITS.every((suit) => foundations[suit].length === 13);
  }

  function tryMove(from, toType, toIndex) {
    const pile = sourcePile(from);
    if (!pile || pile.length === 0) return;
    const card = pile[pile.length - 1];

    if (toType === "foundation") {
      const suit = SUITS[toIndex];
      if (card.suit !== suit) return;
      const foundation = foundations[suit];
      const wantRank = foundation.length === 0 ? 1 : foundation[foundation.length - 1].rank + 1;
      if (card.rank !== wantRank) return;
      pile.pop();
      foundation.push(card);
    } else if (toType === "tableau") {
      const dest = tableau[toIndex];
      if (from.type === "tableau" && from.index === toIndex) return;
      if (dest.length === 0) {
        if (card.rank !== 13) return;
      } else {
        const top = dest[dest.length - 1];
        if (!top.faceUp || top.rank !== card.rank + 1 || isRed(top.suit) === isRed(card.suit)) {
          return;
        }
      }
      pile.pop();
      dest.push(card);
    } else {
      return;
    }

    if (from.type === "tableau") {
      const remaining = tableau[from.index];
      if (remaining.length > 0) remaining[remaining.length - 1].faceUp = true;
    }
  }

  function cardEl(card, extraClass) {
    const div = document.createElement("div");
    div.className = `card ${card.faceUp ? (isRed(card.suit) ? "red" : "black") : "facedown"} ${
      extraClass || ""
    }`;
    if (card.faceUp) {
      div.textContent = `${rankLabel(card.rank)}${SUIT_SYMBOL[card.suit]}`;
    }
    return div;
  }

  function finishIfWon() {
    if (isWon()) {
      gameOver = true;
      onFinish(true);
    }
  }

  function onPileClick(type, index) {
    if (gameOver) return;
    if (selected) {
      const from = selected;
      selected = null;
      if (!(from.type === type && from.index === index)) {
        tryMove(from, type, index);
      }
      render();
      finishIfWon();
      return;
    }
    const pile = type === "tableau" ? tableau[index] : type === "waste" ? waste : null;
    if (pile && pile.length > 0 && pile[pile.length - 1].faceUp) {
      selected = { type, index };
      render();
    }
  }

  function onStockClick() {
    if (gameOver) return;
    if (stock.length === 0) {
      while (waste.length > 0) {
        const card = waste.pop();
        card.faceUp = false;
        stock.push(card);
      }
    } else {
      const card = stock.pop();
      card.faceUp = true;
      waste.push(card);
    }
    selected = null;
    render();
  }

  function render() {
    boardEl.innerHTML = "";

    const topRow = document.createElement("div");
    topRow.className = "solitaire-row";

    const stockEl = document.createElement("div");
    stockEl.className = "pile stock-pile";
    stockEl.textContent = stock.length > 0 ? `⬛ ${stock.length}` : "↻";
    stockEl.addEventListener("click", onStockClick);
    topRow.appendChild(stockEl);

    const wasteEl = document.createElement("div");
    wasteEl.className = "pile waste-pile";
    if (waste.length > 0) {
      const top = waste[waste.length - 1];
      const card = cardEl(top, selected && selected.type === "waste" ? "selected" : "");
      wasteEl.appendChild(card);
    }
    wasteEl.addEventListener("click", () => onPileClick("waste", 0));
    topRow.appendChild(wasteEl);

    const spacer = document.createElement("div");
    spacer.className = "solitaire-spacer";
    topRow.appendChild(spacer);

    SUITS.forEach((suit, index) => {
      const pileEl = document.createElement("div");
      pileEl.className = "pile foundation-pile";
      const foundation = foundations[suit];
      if (foundation.length > 0) {
        pileEl.appendChild(cardEl(foundation[foundation.length - 1]));
      } else {
        pileEl.textContent = SUIT_SYMBOL[suit];
        pileEl.classList.add(isRed(suit) ? "red" : "black");
      }
      pileEl.addEventListener("click", () => onPileClick("foundation", index));
      topRow.appendChild(pileEl);
    });

    boardEl.appendChild(topRow);

    const tableauRow = document.createElement("div");
    tableauRow.className = "solitaire-row solitaire-tableau";
    tableau.forEach((pile, colIndex) => {
      const colEl = document.createElement("div");
      colEl.className = "tableau-column";
      pile.forEach((card, cardIndex) => {
        const isTop = cardIndex === pile.length - 1;
        const el = cardEl(
          card,
          isTop && selected && selected.type === "tableau" && selected.index === colIndex
            ? "selected"
            : ""
        );
        el.style.top = `${cardIndex * 18}px`;
        colEl.appendChild(el);
      });
      colEl.style.minHeight = `${100 + pile.length * 18}px`;
      colEl.addEventListener("click", () => onPileClick("tableau", colIndex));
      tableauRow.appendChild(colEl);
    });
    boardEl.appendChild(tableauRow);

    const giveUpButton = document.createElement("button");
    giveUpButton.type = "button";
    giveUpButton.className = "give-up-btn";
    giveUpButton.textContent = "Give Up";
    giveUpButton.addEventListener("click", () => {
      if (gameOver) return;
      gameOver = true;
      onFinish(false, { neutral: true });
    });
    boardEl.appendChild(giveUpButton);
  }

  boardEl.className = "board-solitaire";
  render();
}
